"""
Image use cases. Authorization (policy engine), validation, storage, and
the audit entry all happen here, never in signals.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
import uuid
from typing import TYPE_CHECKING

from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q
from django.db.models import QuerySet
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from paperless.access import audit
from paperless.access import hierarchy
from paperless.access.constants import Action
from paperless.access.policy import Resource
from paperless.access.policy import decide
from paperless.access.policy import has_permission
from paperless.access.policy import is_system_admin
from paperless.images.processing.image_processor import inspect_image
from paperless.images.scanning import scan_file
from paperless.images.storage import get_storage
from paperless.models import Image

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("paperless.images")


def safe_display_name(name: str | None) -> str:
    """Client file name for display only: basename, no control chars, bounded."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:255]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def visible_images(actor) -> QuerySet[Image]:
    """Live images the actor may see (mirrors the image.view rules)."""
    qs = Image.objects.filter(deleted_at__isnull=True)
    if is_system_admin(actor):
        return qs
    if any(has_permission(actor, f"image.{a}.any") for a in ("upload", "delete")):
        return qs
    visible = Q(owner_id=actor.pk)
    if any(has_permission(actor, f"image.{a}.child") for a in ("upload", "delete")):
        visible |= Q(owner_id__in=hierarchy.get_direct_child_ids(actor))
    return qs.filter(visible)


def _require(actor, action, resource, *, request, audit_action, target_id=None):
    decision = decide(actor=actor, action=action, resource=resource)
    if not decision.allowed:
        audit.record(
            actor=actor,
            action=audit_action,
            target_type="image",
            target_id=target_id,
            outcome="denied",
            after={"owner": resource.owner_id, "reason": decision.reason},
            request=request,
        )
        raise PermissionDenied(decision.reason)


def upload_image(
    *,
    actor,
    owner,
    temp_path: Path,
    original_name: str,
    alt_text: str = "",
    idempotency_key: str | None = None,
    request=None,
) -> tuple[Image, bool]:
    """Returns (image, created). Raises PermissionDenied / ImageRejected."""
    _require(
        actor,
        Action.UPLOAD,
        Resource.image(owner_id=owner.pk),
        request=request,
        audit_action="image.upload",
    )

    if idempotency_key:
        existing = Image.objects.filter(
            uploaded_by=actor,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            return existing, False

    info = inspect_image(temp_path)
    scan_file(temp_path)
    checksum = sha256_file(temp_path)
    size = temp_path.stat().st_size

    image_id = uuid.uuid4()
    storage_key = f"incoming/{image_id.hex}"
    storage = get_storage()
    with temp_path.open("rb") as fh:
        storage.save(storage_key, fh, info.mime_type)

    try:
        with transaction.atomic():
            image = Image.objects.create(
                id=image_id,
                owner=owner,
                uploaded_by=actor,
                original_name=safe_display_name(original_name),
                alt_text=(alt_text or "")[:500],
                source_mime_type=info.mime_type,
                size_bytes=size,
                width=info.width,
                height=info.height,
                storage_key=storage_key,
                checksum=checksum,
                idempotency_key=idempotency_key or None,
            )
            audit.record(
                actor=actor,
                action="image.upload",
                target_type="image",
                target_id=image.pk,
                after={
                    "owner": owner.pk,
                    "mime_type": info.mime_type,
                    "size_bytes": size,
                    "checksum": checksum,
                },
                request=request,
            )
            from paperless.images.tasks import enqueue_variants

            transaction.on_commit(lambda: enqueue_variants(image.pk))
    except IntegrityError:
        # A concurrent request with the same idempotency key won the race.
        storage.delete(storage_key)
        existing = Image.objects.filter(
            uploaded_by=actor,
            idempotency_key=idempotency_key,
        ).first()
        if existing is None:
            raise
        return existing, False
    except BaseException:
        storage.delete(storage_key)  # never leave orphan files
        raise
    return image, True


def delete_image(*, actor, image: Image, request=None) -> None:
    _require(
        actor,
        Action.DELETE,
        Resource.image(image),
        request=request,
        audit_action="image.delete",
        target_id=image.pk,
    )
    with transaction.atomic():
        image.deleted_at = timezone.now()
        image.save(update_fields=["deleted_at"])
        audit.record(
            actor=actor,
            action="image.delete",
            target_type="image",
            target_id=image.pk,
            before={"owner": image.owner_id, "checksum": image.checksum},
            request=request,
        )
        from paperless.images.tasks import enqueue_cleanup

        transaction.on_commit(lambda: enqueue_cleanup(image.pk))


def delete_images_of_owner(*, actor, owner, request=None) -> int:
    """Soft-delete all images of an account that is being deleted."""
    ids = list(
        Image.objects.filter(owner=owner, deleted_at__isnull=True).values_list(
            "pk",
            flat=True,
        ),
    )
    if not ids:
        return 0
    Image.objects.filter(pk__in=ids).update(deleted_at=timezone.now())
    audit.record(
        actor=actor,
        action="image.delete.owner_removed",
        target_type="user",
        target_id=owner.pk,
        before={"images": [str(i) for i in ids]},
        request=request,
    )
    from paperless.images.tasks import enqueue_cleanup

    for image_id in ids:
        transaction.on_commit(lambda image_id=image_id: enqueue_cleanup(image_id))
    return len(ids)
