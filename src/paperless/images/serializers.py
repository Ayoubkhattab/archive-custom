from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import serializers

from paperless.access.constants import Action
from paperless.access.policy import Resource
from paperless.access.policy import can
from paperless.access.service import readable_users
from paperless.models import Image
from paperless.models import ImageVariant


class ImageUploadSerializer(serializers.Serializer):
    """Schema for POST /api/images/ (multipart/form-data)."""

    file = serializers.FileField(allow_empty_file=False)
    # Whose image this is; defaults to the uploader. Only accounts the
    # uploader can see are valid (others are a 422, not a 403/404 oracle).
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.none(),
        required=False,
    )
    alt_text = serializers.CharField(max_length=500, required=False, allow_blank=True)
    idempotency_key = serializers.RegexField(
        r"^[A-Za-z0-9_-]{8,128}$",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None:
            self.fields["owner"].queryset = readable_users(
                request.user,
                User.objects.all(),
            )


class ImageVariantSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ImageVariant
        fields = ("kind", "width", "height", "format", "size_bytes", "url")

    def get_url(self, variant) -> str:
        # The checksum in the query makes the URL content addressed, which is
        # what allows "immutable" caching of the response.
        path = reverse(
            "images-variant",
            kwargs={"pk": variant.image_id, "kind": variant.kind},
        )
        return f"{path}?v={variant.checksum[:16]}"


class ImageSerializer(serializers.ModelSerializer):
    variants = serializers.SerializerMethodField()
    srcset = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = Image
        fields = (
            "id",
            "owner",
            "original_name",
            "alt_text",
            "mime_type",
            "source_mime_type",
            "size_bytes",
            "width",
            "height",
            "checksum",
            "status",
            "created_at",
            "variants",
            "srcset",
            "can_delete",
        )
        read_only_fields = fields

    def _variants(self, image):
        return sorted(image.variants.all(), key=lambda v: v.width)

    def get_variants(self, image) -> list[dict]:
        return ImageVariantSerializer(self._variants(image), many=True).data

    def get_srcset(self, image) -> str:
        return ", ".join(
            f"{ImageVariantSerializer().get_url(v)} {v.width}w"
            for v in self._variants(image)
        )

    def get_can_delete(self, image) -> bool:
        request = self.context.get("request")
        return bool(
            request
            and can(
                actor=request.user,
                action=Action.DELETE,
                resource=Resource.image(image),
            ),
        )
