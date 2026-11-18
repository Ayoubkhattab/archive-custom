"""
Background jobs for images (Celery, Redis broker).

* generate_image_variants - decode once, re-encode every variant. Idempotent:
  JobRecord key ``variants:<image id>`` plus a unique (image, kind) row per
  variant, so a retried or re-delivered task never duplicates work.
* purge_image_files - removes every stored file of a soft-deleted image.
  Idempotent via ``Image.files_purged_at``.
* sweep_images - periodic safety net: purges deleted images whose cleanup
  was lost and re-queues uploads stuck in "pending".

All tasks retry with exponential backoff and jitter on storage errors and
run under soft/hard time limits.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from paperless.images.processing.image_processor import ImageRejected
from paperless.images.processing.image_processor import encode
from paperless.images.processing.image_processor import load_clean
from paperless.images.processing.variant_generator import plan_variants
from paperless.images.storage import get_storage
from paperless.models import Image
from paperless.models import ImageVariant
from paperless.models import JobRecord

logger = logging.getLogger("paperless.images.tasks")

RETRY_OPTS = {
    "autoretry_for": (OSError, ConnectionError, TimeoutError),
    "retry_backoff": 2,
    "retry_backoff_max": 300,
    "retry_jitter": True,
    "max_retries": 6,
    "soft_time_limit": 120,
    "time_limit": 180,
    "acks_late": True,
}


def enqueue_variants(image_id) -> None:
    JobRecord.objects.get_or_create(
        idempotency_key=f"variants:{image_id}",
        defaults={"kind": "image.variants", "payload": {"image_id": str(image_id)}},
    )
    generate_image_variants.delay(str(image_id))


def enqueue_cleanup(image_id) -> None:
    JobRecord.objects.get_or_create(
        idempotency_key=f"purge:{image_id}",
        defaults={"kind": "image.purge", "payload": {"image_id": str(image_id)}},
    )
    purge_image_files.delay(str(image_id))


def _job(key: str) -> JobRecord | None:
    job = JobRecord.objects.filter(idempotency_key=key).first()
    if job is not None:
        JobRecord.objects.filter(pk=job.pk).update(
            status=JobRecord.Status.RUNNING,
            attempts=job.attempts + 1,
        )
    return job


def _finish(job: JobRecord | None, status: str, error: str = "") -> None:
    if job is not None:
        JobRecord.objects.filter(pk=job.pk).update(
            status=status,
            last_error=error[:500],
        )


@shared_task(bind=True, **RETRY_OPTS)
def generate_image_variants(self, image_id: str) -> str:
    key = f"variants:{image_id}"
    done = JobRecord.objects.filter(
        idempotency_key=key,
        status=JobRecord.Status.SUCCEEDED,
    ).exists()
    image = Image.objects.filter(pk=image_id).first()
    if done or image is None or image.status == Image.Status.READY:
        return "skipped"
    if image.deleted_at is not None:
        enqueue_cleanup(image.pk)
        return "deleted"

    job = _job(key)
    storage = get_storage()
    workdir = Path(tempfile.mkdtemp(prefix="paperless-image-"))
    try:
        source = workdir / "source"
        with storage.open(image.storage_key) as src, source.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        try:
            clean = load_clean(source)
        except (ImageRejected, OSError, ValueError, SyntaxError) as exc:
            # Corrupt on full decode: permanent failure, no retry.
            Image.objects.filter(pk=image.pk).update(
                status=Image.Status.FAILED,
                error="The image could not be decoded.",
            )
            storage.delete(image.storage_key)
            _finish(job, JobRecord.Status.FAILED, str(exc))
            return "failed"

        existing = set(image.variants.values_list("kind", flat=True))
        out_mime = ""
        for kind, width in plan_variants(clean.width):
            if kind in existing:
                continue  # idempotency per (image, kind)
            encoded = encode(clean, width)
            out_mime = encoded.mime_type
            variant_key = f"variants/{image.pk.hex}/{kind}-{encoded.checksum[:16]}.{encoded.format}"
            storage.save(variant_key, BytesIO(encoded.data), encoded.mime_type)
            ImageVariant.objects.get_or_create(
                image=image,
                kind=kind,
                defaults={
                    "width": encoded.width,
                    "height": encoded.height,
                    "storage_key": variant_key,
                    "format": encoded.format,
                    "size_bytes": len(encoded.data),
                    "checksum": encoded.checksum,
                },
            )

        with transaction.atomic():
            fields = {
                "status": Image.Status.READY,
                "width": clean.width,  # after EXIF orientation
                "height": clean.height,
                "error": "",
            }
            if out_mime:
                fields["mime_type"] = out_mime
            Image.objects.filter(pk=image.pk).update(**fields)
        if not settings.IMAGES_KEEP_ORIGINAL:
            storage.delete(image.storage_key)
        _finish(job, JobRecord.Status.SUCCEEDED)
        return "ready"
    except Exception as exc:
        _finish(job, JobRecord.Status.QUEUED, str(exc))
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@shared_task(bind=True, **RETRY_OPTS)
def purge_image_files(self, image_id: str) -> str:
    image = Image.objects.filter(pk=image_id).first()
    if image is None or image.files_purged_at is not None:
        return "skipped"
    if image.deleted_at is None:
        return "not-deleted"  # never purge a live image
    job = _job(f"purge:{image_id}")
    storage = get_storage()
    try:
        for variant in image.variants.all():
            storage.delete(variant.storage_key)
        if image.storage_key:
            storage.delete(image.storage_key)
        with transaction.atomic():
            image.variants.all().delete()
            Image.objects.filter(pk=image.pk).update(files_purged_at=timezone.now())
    except Exception as exc:
        _finish(job, JobRecord.Status.QUEUED, str(exc))
        raise
    _finish(job, JobRecord.Status.SUCCEEDED)
    return "purged"


@shared_task(soft_time_limit=300, time_limit=360)
def sweep_images() -> dict:
    purged = 0
    for image_id in Image.objects.filter(
        deleted_at__isnull=False,
        files_purged_at__isnull=True,
    ).values_list("pk", flat=True)[:500]:
        purge_image_files.delay(str(image_id))
        purged += 1
    stale = timezone.now() - timedelta(minutes=15)
    requeued = 0
    for image_id in Image.objects.filter(
        status=Image.Status.PENDING,
        deleted_at__isnull=True,
        created_at__lt=stale,
    ).values_list("pk", flat=True)[:500]:
        generate_image_variants.delay(str(image_id))
        requeued += 1
    return {"purged": purged, "requeued": requeued}
