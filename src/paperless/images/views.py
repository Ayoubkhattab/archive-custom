"""
/api/images/                         GET list, POST upload (multipart)
/api/images/{id}/                    GET metadata, DELETE (soft delete)
/api/images/{id}/file/               GET original (only if kept) or largest variant
/api/images/{id}/variants/{kind}/    GET one variant (Range, ETag, 304)
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db.models import Prefetch
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from paperless.access.constants import Action
from paperless.access.drf import HasPermission
from paperless.access.drf import HasRelationship
from paperless.access.drf import PermissionScopedMixin
from paperless.access.exceptions import ApiError
from paperless.access.policy import Resource
from paperless.images import service
from paperless.images.processing.image_processor import ImageRejected
from paperless.images.serializers import ImageSerializer
from paperless.images.serializers import ImageUploadSerializer
from paperless.images.storage import get_storage
from paperless.images.throttles import ImageMutationThrottle
from paperless.images.upload_handler import LimitedTemporaryFileUploadHandler
from paperless.models import Image
from paperless.models import ImageVariant
from paperless.streaming import ranged_file_response
from paperless.views import StandardPagination

IMMUTABLE = "private, max-age=31536000, immutable"
PRIVATE_NO_STORE = "private, no-store"


class ImageViewSet(PermissionScopedMixin, GenericViewSet):
    serializer_class = ImageSerializer
    parser_classes = (MultiPartParser,)
    pagination_class = StandardPagination
    throttle_classes = (ImageMutationThrottle,)
    permission_classes = (IsAuthenticated, HasPermission, HasRelationship)
    required_permission = {
        "GET": "image.view.*",
        "HEAD": "image.view.*",
        "OPTIONS": "image.view.*",
        "POST": "image.upload.*",
        "DELETE": "image.delete.*",
    }
    relationship_guard = {
        "GET": Action.VIEW,
        "HEAD": Action.VIEW,
        "DELETE": Action.DELETE,
    }

    def initialize_request(self, request, *args, **kwargs):
        # Install the streaming limits before anything (CSRF, auth) can read
        # the body with Django's default upload handlers.
        if request.method == "POST":
            request.upload_handlers = [LimitedTemporaryFileUploadHandler(request)]
        return super().initialize_request(request, *args, **kwargs)

    def get_policy_resource(self, obj):
        return Resource.image(obj)

    def get_queryset(self):
        return (
            service.visible_images(self.request.user)
            .prefetch_related(
                Prefetch("variants", queryset=ImageVariant.objects.order_by("width")),
            )
            .order_by("-created_at")
        )

    def get_object(self):
        # Images the actor may not view are reported as missing (404), so the
        # API never confirms that someone else's image exists.
        try:
            obj = Image.objects.prefetch_related("variants").get(
                pk=self.kwargs["pk"],
                deleted_at__isnull=True,
            )
        except (Image.DoesNotExist, ValueError, TypeError):
            raise Http404
        from paperless.access.policy import can

        if not can(
            actor=self.request.user,
            action=Action.VIEW,
            resource=Resource.image(obj),
        ):
            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    # ---- metadata ----------------------------------------------------------

    def list(self, request):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(self.get_serializer(self.get_object()).data)

    # ---- upload ------------------------------------------------------------

    def create(self, request):
        if not settings.IMAGES_ENABLED:
            raise ApiError(404, "not_found", "Not found.")
        try:
            data = request.data
            files = request.FILES.getlist("file")
        except Exception:
            data, files = None, []
        error = getattr(request._request, "image_upload_error", None)
        if error == "too_large":
            raise ApiError(
                413,
                "payload_too_large",
                f"The file is larger than {settings.IMAGES_MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            )
        if (
            error == "too_many_files"
            or len(files) > settings.IMAGES_MAX_FILES_PER_REQUEST
        ):
            raise ApiError(422, "too_many_files", "Upload one file per request.")

        serializer = ImageUploadSerializer(data=data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        upload = serializer.validated_data["file"]
        owner = serializer.validated_data.get("owner") or request.user
        try:
            image, created = service.upload_image(
                actor=request.user,
                owner=owner,
                temp_path=Path(upload.temporary_file_path()),
                original_name=upload.name,
                alt_text=serializer.validated_data.get("alt_text", ""),
                idempotency_key=serializer.validated_data.get("idempotency_key")
                or request.headers.get("Idempotency-Key"),
                request=request,
            )
        except ImageRejected as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
        finally:
            upload.close()
        return Response(
            self.get_serializer(image).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    # ---- delete ------------------------------------------------------------

    def destroy(self, request, pk=None):
        image = self.get_object()  # 404 if not visible, 403 if not deletable
        service.delete_image(actor=request.user, image=image, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---- streaming ---------------------------------------------------------

    def _stream(
        self,
        request,
        *,
        key,
        content_type,
        checksum,
        cache_control,
        last_modified,
        size=None,
    ):
        storage = get_storage()
        try:
            size = size if size is not None else storage.size(key)
        except (FileNotFoundError, OSError):
            raise Http404
        return ranged_file_response(
            request,
            open_file=lambda: storage.open(key),
            size=size,
            content_type=content_type,
            etag=checksum,
            last_modified=last_modified,
            disposition="inline",
            cache_control=cache_control,
            x_accel_path=storage.x_accel_path(key),
        )

    @action(
        detail=True,
        methods=["get"],
        url_path=r"variants/(?P<kind>[a-z]+)",
        url_name="variant",
    )
    def variant(self, request, pk=None, kind=None):
        image = self.get_object()
        variant = next((v for v in image.variants.all() if v.kind == kind), None)
        if variant is None:
            raise Http404
        return self._stream(
            request,
            key=variant.storage_key,
            content_type=f"image/{variant.format}",
            checksum=variant.checksum,
            cache_control=IMMUTABLE,
            last_modified=variant.created_at,
            size=variant.size_bytes,
        )

    @action(detail=True, methods=["get"], url_path="file", url_name="file")
    def file(self, request, pk=None):
        image = self.get_object()
        if settings.IMAGES_KEEP_ORIGINAL and image.storage_key:
            return self._stream(
                request,
                key=image.storage_key,
                content_type=image.source_mime_type,
                checksum=image.checksum,
                cache_control=PRIVATE_NO_STORE,
                last_modified=image.created_at,
            )
        variants = sorted(image.variants.all(), key=lambda v: v.width)
        if not variants:
            # still processing (or failed): never serve the unprocessed upload
            raise ApiError(404, "not_ready", "The image is still being processed.")
        largest = variants[-1]
        return self._stream(
            request,
            key=largest.storage_key,
            content_type=f"image/{largest.format}",
            checksum=largest.checksum,
            cache_control=IMMUTABLE
            if request.query_params.get("v")
            else "private, no-cache",
            last_modified=largest.created_at,
            size=largest.size_bytes,
        )
