import io
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APITestCase

from paperless.access import constants as C
from paperless.images import tasks
from paperless.images.processing.sniff import sniff_mime
from paperless.images.processing.variant_generator import plan_variants
from paperless.images.storage import InvalidStorageKey
from paperless.images.storage import get_storage
from paperless.images.storage import validate_key
from paperless.images.storage.local import LocalStorageProvider
from paperless.images.throttles import ImageMutationThrottle
from paperless.models import Image
from paperless.models import ImageVariant
from paperless.models import JobRecord
from paperless.models import SecurityAuditLog
from paperless.tests.test_access_policy import child_of
from paperless.tests.test_access_policy import grant

ENDPOINT = "/api/images/"


def make_image(
    fmt="JPEG", size=(300, 100), exif_orientation=None, *, gps=False
) -> bytes:
    img = PILImage.new("RGB", size, (200, 30, 30))
    exif = PILImage.Exif()
    if exif_orientation:
        exif[0x0112] = exif_orientation
    if gps:
        exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0)}  # GPS IFD
        exif[0x010F] = "SecretCam"
    buf = io.BytesIO()
    kwargs = {"exif": exif.tobytes()} if (exif_orientation or gps) else {}
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def upload(name, content, content_type="image/jpeg"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class ImageTestBase(APITestCase):
    def setUp(self):
        super().setUp()
        self.storage_dir = Path(tempfile.mkdtemp())
        # The feature is off by default (the UI page was removed); the API
        # is still tested with it switched on.
        self.override = override_settings(
            IMAGES_ENABLED=True,
            IMAGES_STORAGE_DIR=self.storage_dir,
        )
        self.override.enable()
        get_storage.cache_clear()
        cache.clear()
        # Run background jobs synchronously in tests.
        self.patches = [
            mock.patch.object(
                tasks.generate_image_variants,
                "delay",
                side_effect=lambda i: tasks.generate_image_variants.apply(args=[i]),
            ),
            mock.patch.object(
                tasks.purge_image_files,
                "delay",
                side_effect=lambda i: tasks.purge_image_files.apply(args=[i]),
            ),
        ]
        for p in self.patches:
            p.start()

        self.father = child_of(None, "father")
        self.son = child_of(self.father, "son")
        self.stranger = child_of(None, "stranger")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.override.disable()
        get_storage.cache_clear()
        shutil.rmtree(self.storage_dir, ignore_errors=True)
        super().tearDown()

    def post(self, actor, file, **data):
        self.client.force_authenticate(actor)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                ENDPOINT,
                {"file": file, **data},
                format="multipart",
            )

    def stored_files(self):
        return [p for p in self.storage_dir.rglob("*") if p.is_file()]


class TestImageUpload(ImageTestBase):
    def test_upload_reencodes_strips_metadata_and_builds_variants(self):
        response = self.post(
            self.son,
            upload("photo.jpg", make_image(exif_orientation=6, gps=True)),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        image = Image.objects.get(pk=response.data["id"])
        self.assertEqual(image.status, Image.Status.READY)
        self.assertEqual(image.source_mime_type, "image/jpeg")
        self.assertEqual(image.mime_type, "image/webp")
        # EXIF orientation 6 (rotate 90) applied: 300x100 -> 100x300
        self.assertEqual((image.width, image.height), (100, 300))
        kinds = set(image.variants.values_list("kind", flat=True))
        self.assertEqual(kinds, {"thumbnail"})  # 100px wide: no upscaling

        variant = image.variants.get()
        with get_storage().open(variant.storage_key) as fh:
            out = PILImage.open(io.BytesIO(fh.read()))
            self.assertEqual(out.format, "WEBP")
            self.assertEqual(len(out.getexif()), 0)
            self.assertNotIn("exif", out.info)
        # the unprocessed upload is gone (keep-original is off)
        self.assertFalse(get_storage().exists(image.storage_key))

    def test_variant_plan(self):
        self.assertEqual(plan_variants(150), [("thumbnail", 150)])
        self.assertEqual(
            plan_variants(1000),
            [("thumbnail", 200), ("small", 640), ("medium", 1000)],
        )
        self.assertEqual(
            plan_variants(5000),
            [("thumbnail", 200), ("small", 640), ("medium", 1280), ("large", 2048)],
        )

    def test_magic_bytes_decide_the_type_not_the_client(self):
        # PNG bytes sent as "photo.jpg" / image/jpeg: accepted as PNG
        response = self.post(
            self.son,
            upload("photo.jpg", make_image("PNG"), "image/jpeg"),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source_mime_type"], "image/png")

        # an executable pretending to be a JPEG
        response = self.post(
            self.son,
            upload("evil.jpg", b"MZ\x90\x00" + b"\x00" * 200, "image/jpeg"),
        )
        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(response.data["code"], "unsupported_type")

    def test_jpeg_magic_with_garbage_body_is_rejected(self):
        response = self.post(
            self.son,
            upload("x.jpg", b"\xff\xd8\xff\xe0" + b"garbage" * 50),
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)

    def test_svg_and_html_are_rejected(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        response = self.post(self.son, upload("x.svg", svg, "image/svg+xml"))
        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(response.data["code"], "svg_not_allowed")
        response = self.post(
            self.son,
            upload("x.png", b"<html><body>hi</body></html>" * 5, "image/png"),
        )
        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_type_not_in_allowlist(self):
        with override_settings(IMAGES_ALLOWED_TYPES=("image/png",)):
            response = self.post(self.son, upload("p.jpg", make_image()))
        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

    def test_oversize_is_413(self):
        with override_settings(IMAGES_MAX_UPLOAD_BYTES=1024):
            response = self.post(
                self.son,
                upload("big.png", make_image("PNG", (600, 600)) + b"\x00" * 4096),
            )
        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        self.assertEqual(Image.objects.count(), 0)

    def test_more_than_one_file_is_rejected(self):
        self.client.force_authenticate(self.son)
        response = self.client.post(
            ENDPOINT,
            {"file": [upload("a.jpg", make_image()), upload("b.jpg", make_image())]},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(Image.objects.count(), 0)

    def test_missing_file_is_422(self):
        self.client.force_authenticate(self.son)
        response = self.client.post(ENDPOINT, {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        self.assertEqual(response.data["code"], "validation_error")

    def test_client_filename_never_reaches_the_disk(self):
        response = self.post(self.son, upload("../../../etc/passwd.jpg", make_image()))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["original_name"], "passwd.jpg")
        for path in self.stored_files():
            self.assertTrue(path.resolve().is_relative_to(self.storage_dir.resolve()))
            self.assertNotIn("passwd", path.name)

    def test_storage_keys_reject_traversal(self):
        for bad in (
            "../x",
            "/etc/passwd",
            "a/../../b",
            "a\\b",
            "A/b",
            "a/b\x00",
            "",
            "a//b",
        ):
            with self.assertRaises(InvalidStorageKey, msg=bad):
                validate_key(bad)
        provider = LocalStorageProvider(self.storage_dir)
        with self.assertRaises(InvalidStorageKey):
            provider.open("incoming/../../secret")

    def test_idempotency_key_returns_the_same_image(self):
        first = self.post(
            self.son,
            upload("a.jpg", make_image()),
            idempotency_key="upload-1234",
        )
        second = self.post(
            self.son,
            upload("a.jpg", make_image()),
            idempotency_key="upload-1234",
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(Image.objects.count(), 1)

    def test_variant_generation_is_idempotent(self):
        response = self.post(self.son, upload("big.jpg", make_image(size=(1500, 900))))
        image = Image.objects.get(pk=response.data["id"])
        count = image.variants.count()
        # 200, 640, 1280 and the full 1500px as "large"
        self.assertEqual(count, 4)
        Image.objects.filter(pk=image.pk).update(status=Image.Status.PENDING)
        tasks.generate_image_variants.apply(args=[str(image.pk)])
        self.assertEqual(ImageVariant.objects.filter(image=image).count(), count)
        self.assertTrue(
            JobRecord.objects.filter(
                idempotency_key=f"variants:{image.pk}",
                status=JobRecord.Status.SUCCEEDED,
            ).exists(),
        )

    def test_unauthenticated_is_401(self):
        response = self.client.post(
            ENDPOINT,
            {"file": upload("a.jpg", make_image())},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_upload_and_delete_are_audited(self):
        response = self.post(self.son, upload("a.jpg", make_image()))
        self.client.force_authenticate(self.son)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(f"{ENDPOINT}{response.data['id']}/")
        actions = set(SecurityAuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"image.upload", "image.delete"} <= actions)


class TestImagePermissions(ImageTestBase):
    def test_son_cannot_upload_for_father(self):
        son = grant(self.son, C.USER_READ_ANY, C.IMAGE_UPLOAD_ANY)
        response = self.post(son, upload("a.jpg", make_image()), owner=self.father.pk)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Image.objects.count(), 0)
        self.assertTrue(
            SecurityAuditLog.objects.filter(
                action="image.upload",
                outcome="denied",
            ).exists(),
        )

    def test_father_uploads_for_direct_child_with_child_permission(self):
        response = self.post(
            self.father,
            upload("a.jpg", make_image()),
            owner=self.son.pk,
        )
        # father cannot see the son's account without user.read.child -> 422
        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        father = grant(self.father, C.USER_READ_CHILD)
        response = self.post(father, upload("a.jpg", make_image()), owner=self.son.pk)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        father = grant(father, C.IMAGE_UPLOAD_CHILD)
        response = self.post(father, upload("a.jpg", make_image()), owner=self.son.pk)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["owner"], self.son.pk)

    def test_son_cannot_delete_fathers_image(self):
        image_id = self.post(self.father, upload("a.jpg", make_image())).data["id"]
        self.client.force_authenticate(self.son)
        # not even visible to him: 404, existence not leaked
        response = self.client.delete(f"{ENDPOINT}{image_id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        # with a general delete permission it is visible, but still forbidden
        son = grant(self.son, C.IMAGE_DELETE_ANY)
        self.client.force_authenticate(son)
        response = self.client.delete(f"{ENDPOINT}{image_id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIsNone(Image.objects.get(pk=image_id).deleted_at)

    def test_unrelated_user_sees_nothing(self):
        image_id = self.post(self.son, upload("a.jpg", make_image())).data["id"]
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(f"{ENDPOINT}{image_id}/").status_code, 404)
        self.assertEqual(self.client.get(ENDPOINT).data["count"], 0)

    @override_settings(ACCESS_IMPLICIT_PERMISSIONS=())
    def test_strict_mode_requires_explicit_grants(self):
        response = self.post(self.son, upload("a.jpg", make_image()))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_global_admin_can_delete_anything(self):
        image_id = self.post(self.son, upload("a.jpg", make_image())).data["id"]
        admin = grant(child_of(None, "admin"), C.SYSTEM_MANAGE_ALL)
        self.client.force_authenticate(admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(f"{ENDPOINT}{image_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_upload_is_rate_limited(self):
        with mock.patch.dict(
            ImageMutationThrottle.THROTTLE_RATES,
            {"image_upload": "2/min"},
        ):
            codes = [
                self.post(self.son, upload("a.jpg", make_image())).status_code
                for _ in range(3)
            ]
        self.assertEqual(codes, [201, 201, 429])


class TestImageDeleteAndServe(ImageTestBase):
    def test_delete_soft_deletes_and_purges_files(self):
        image_id = self.post(
            self.son,
            upload("a.jpg", make_image(size=(800, 600))),
        ).data["id"]
        self.assertTrue(self.stored_files())
        self.client.force_authenticate(self.son)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(f"{ENDPOINT}{image_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        image = Image.objects.get(pk=image_id)
        self.assertIsNotNone(image.deleted_at)
        self.assertIsNotNone(image.files_purged_at)
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(self.client.get(f"{ENDPOINT}{image_id}/").status_code, 404)

    def test_deleting_a_user_cleans_up_their_images(self):
        image_id = self.post(self.son, upload("a.jpg", make_image())).data["id"]
        father = grant(self.father, C.USER_READ_CHILD, "auth.delete_user")
        self.client.force_authenticate(father)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.delete(f"/api/users/{self.son.pk}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIsNotNone(Image.objects.get(pk=image_id).files_purged_at)
        self.assertEqual(self.stored_files(), [])

    def test_variant_streaming_caching_and_ranges(self):
        data = self.post(self.son, upload("a.jpg", make_image(size=(800, 600)))).data
        image = Image.objects.get(pk=data["id"])
        self.client.force_authenticate(self.son)
        meta = self.client.get(f"{ENDPOINT}{image.pk}/").data
        url = next(v["url"] for v in meta["variants"] if v["kind"] == "small")
        self.assertIn("?v=", url)
        self.assertIn("640w", meta["srcset"])

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = b"".join(response.streaming_content)
        self.assertEqual(response["Content-Type"], "image/webp")
        self.assertEqual(response["Accept-Ranges"], "bytes")
        self.assertIn("immutable", response["Cache-Control"])
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        etag = response["ETag"]

        self.assertEqual(self.client.get(url, HTTP_IF_NONE_MATCH=etag).status_code, 304)

        partial = self.client.get(url, HTTP_RANGE="bytes=0-9")
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial["Content-Range"], f"bytes 0-9/{len(body)}")
        self.assertEqual(b"".join(partial.streaming_content), body[:10])

        bad = self.client.get(url, HTTP_RANGE=f"bytes={len(body) + 10}-")
        self.assertEqual(bad.status_code, 416)
        self.assertEqual(bad["Content-Range"], f"bytes */{len(body)}")

        # the /file/ endpoint serves the largest processed variant
        self.assertEqual(
            self.client.get(f"{ENDPOINT}{image.pk}/file/").status_code,
            200,
        )

    def test_sniffer(self):
        self.assertEqual(sniff_mime(make_image("WEBP")[:64]), "image/webp")
        self.assertEqual(sniff_mime(make_image("GIF")[:64]), "image/gif")
        self.assertIsNone(sniff_mime(b"%PDF-1.7"))
