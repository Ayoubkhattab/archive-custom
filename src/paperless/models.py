import uuid

from django.core.validators import FileExtensionValidator
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import User, Group

DEFAULT_SINGLETON_INSTANCE_ID = 1


class AbstractSingletonModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        """
        Always save as the first and only model
        """
        self.pk = DEFAULT_SINGLETON_INSTANCE_ID
        super().save(*args, **kwargs)


class OutputTypeChoices(models.TextChoices):
    """
    Matches to --output-type
    """

    PDF = ("pdf", _("pdf"))
    PDF_A = ("pdfa", _("pdfa"))
    PDF_A1 = ("pdfa-1", _("pdfa-1"))
    PDF_A2 = ("pdfa-2", _("pdfa-2"))
    PDF_A3 = ("pdfa-3", _("pdfa-3"))


class ModeChoices(models.TextChoices):
    """
    Matches to --skip-text, --redo-ocr, --force-ocr
    and our own custom setting
    """

    SKIP = ("skip", _("skip"))
    REDO = ("redo", _("redo"))
    FORCE = ("force", _("force"))
    SKIP_NO_ARCHIVE = ("skip_noarchive", _("skip_noarchive"))


class ArchiveFileChoices(models.TextChoices):
    """
    Settings to control creation of an archive PDF file
    """

    NEVER = ("never", _("never"))
    WITH_TEXT = ("with_text", _("with_text"))
    ALWAYS = ("always", _("always"))


class CleanChoices(models.TextChoices):
    """
    Matches to --clean, --clean-final
    """

    CLEAN = ("clean", _("clean"))
    FINAL = ("clean-final", _("clean-final"))
    NONE = ("none", _("none"))


class ColorConvertChoices(models.TextChoices):
    """
    Refer to the Ghostscript documentation for valid options
    """

    UNCHANGED = ("LeaveColorUnchanged", _("LeaveColorUnchanged"))
    RGB = ("RGB", _("RGB"))
    INDEPENDENT = ("UseDeviceIndependentColor", _("UseDeviceIndependentColor"))
    GRAY = ("Gray", _("Gray"))
    CMYK = ("CMYK", _("CMYK"))


class LLMEmbeddingBackend(models.TextChoices):
    OPENAI = ("openai", _("OpenAI"))
    HUGGINGFACE = ("huggingface", _("Huggingface"))


class LLMBackend(models.TextChoices):
    """
    Matches to --llm-backend
    """

    OPENAI = ("openai", _("OpenAI"))
    OLLAMA = ("ollama", _("Ollama"))


class ApplicationConfiguration(AbstractSingletonModel):
    """
    Settings which are common across more than 1 parser
    """

    output_type = models.CharField(
        verbose_name=_("Sets the output PDF type"),
        null=True,
        blank=True,
        max_length=8,
        choices=OutputTypeChoices.choices,
    )

    """
    Settings for the Tesseract based OCR parser
    """

    pages = models.PositiveIntegerField(
        verbose_name=_("Do OCR from page 1 to this value"),
        null=True,
        validators=[MinValueValidator(1)],
    )

    language = models.CharField(
        verbose_name=_("Do OCR using these languages"),
        null=True,
        blank=True,
        max_length=32,
    )

    mode = models.CharField(
        verbose_name=_("Sets the OCR mode"),
        null=True,
        blank=True,
        max_length=16,
        choices=ModeChoices.choices,
    )

    skip_archive_file = models.CharField(
        verbose_name=_("Controls the generation of an archive file"),
        null=True,
        blank=True,
        max_length=16,
        choices=ArchiveFileChoices.choices,
    )

    image_dpi = models.PositiveIntegerField(
        verbose_name=_("Sets image DPI fallback value"),
        null=True,
        validators=[MinValueValidator(1)],
    )

    # Can't call it clean, that's a model method
    unpaper_clean = models.CharField(
        verbose_name=_("Controls the unpaper cleaning"),
        null=True,
        blank=True,
        max_length=16,
        choices=CleanChoices.choices,
    )

    deskew = models.BooleanField(verbose_name=_("Enables deskew"), null=True)

    rotate_pages = models.BooleanField(
        verbose_name=_("Enables page rotation"),
        null=True,
    )

    rotate_pages_threshold = models.FloatField(
        verbose_name=_("Sets the threshold for rotation of pages"),
        null=True,
        validators=[MinValueValidator(0.0)],
    )

    max_image_pixels = models.FloatField(
        verbose_name=_("Sets the maximum image size for decompression"),
        null=True,
        validators=[MinValueValidator(0.0)],
    )

    color_conversion_strategy = models.CharField(
        verbose_name=_("Sets the Ghostscript color conversion strategy"),
        blank=True,
        null=True,
        max_length=32,
        choices=ColorConvertChoices.choices,
    )

    user_args = models.JSONField(
        verbose_name=_("Adds additional user arguments for OCRMyPDF"),
        null=True,
    )

    """
    Settings for the Paperless application
    """

    app_title = models.CharField(
        verbose_name=_("Application title"),
        null=True,
        blank=True,
        max_length=48,
    )

    app_logo = models.FileField(
        verbose_name=_("Application logo"),
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=["jpg", "png", "gif", "svg"]),
        ],
        upload_to="logo/",
    )

    """
    Settings for the barcode scanner
    """

    # PAPERLESS_CONSUMER_ENABLE_BARCODES
    barcodes_enabled = models.BooleanField(
        verbose_name=_("Enables barcode scanning"),
        null=True,
    )

    # PAPERLESS_CONSUMER_BARCODE_TIFF_SUPPORT
    barcode_enable_tiff_support = models.BooleanField(
        verbose_name=_("Enables barcode TIFF support"),
        null=True,
    )

    # PAPERLESS_CONSUMER_BARCODE_STRING
    barcode_string = models.CharField(
        verbose_name=_("Sets the barcode string"),
        null=True,
        blank=True,
        max_length=32,
    )

    # PAPERLESS_CONSUMER_BARCODE_RETAIN_SPLIT_PAGES
    barcode_retain_split_pages = models.BooleanField(
        verbose_name=_("Retains split pages"),
        null=True,
    )

    # PAPERLESS_CONSUMER_ENABLE_ASN_BARCODE
    barcode_enable_asn = models.BooleanField(
        verbose_name=_("Enables ASN barcode"),
        null=True,
    )

    # PAPERLESS_CONSUMER_ASN_BARCODE_PREFIX
    barcode_asn_prefix = models.CharField(
        verbose_name=_("Sets the ASN barcode prefix"),
        null=True,
        blank=True,
        max_length=32,
    )

    # PAPERLESS_CONSUMER_BARCODE_UPSCALE
    barcode_upscale = models.FloatField(
        verbose_name=_("Sets the barcode upscale factor"),
        null=True,
        validators=[MinValueValidator(1.0)],
    )

    # PAPERLESS_CONSUMER_BARCODE_DPI
    barcode_dpi = models.PositiveIntegerField(
        verbose_name=_("Sets the barcode DPI"),
        null=True,
        validators=[MinValueValidator(1)],
    )

    # PAPERLESS_CONSUMER_BARCODE_MAX_PAGES
    barcode_max_pages = models.PositiveIntegerField(
        verbose_name=_("Sets the maximum pages for barcode"),
        null=True,
        validators=[MinValueValidator(1)],
    )

    # PAPERLESS_CONSUMER_ENABLE_TAG_BARCODE
    barcode_enable_tag = models.BooleanField(
        verbose_name=_("Enables tag barcode"),
        null=True,
    )

    # PAPERLESS_CONSUMER_TAG_BARCODE_MAPPING
    barcode_tag_mapping = models.JSONField(
        verbose_name=_("Sets the tag barcode mapping"),
        null=True,
    )

    """
    AI related settings
    """

    ai_enabled = models.BooleanField(
        verbose_name=_("Enables AI features"),
        null=True,
        default=False,
    )

    llm_embedding_backend = models.CharField(
        verbose_name=_("Sets the LLM embedding backend"),
        blank=True,
        null=True,
        max_length=128,
        choices=LLMEmbeddingBackend.choices,
    )

    llm_embedding_model = models.CharField(
        verbose_name=_("Sets the LLM embedding model"),
        blank=True,
        null=True,
        max_length=128,
    )

    llm_backend = models.CharField(
        verbose_name=_("Sets the LLM backend"),
        blank=True,
        null=True,
        max_length=128,
        choices=LLMBackend.choices,
    )

    llm_model = models.CharField(
        verbose_name=_("Sets the LLM model"),
        blank=True,
        null=True,
        max_length=128,
    )

    llm_api_key = models.CharField(
        verbose_name=_("Sets the LLM API key"),
        blank=True,
        null=True,
        max_length=1024,
    )

    llm_endpoint = models.CharField(
        verbose_name=_("Sets the LLM endpoint, optional"),
        blank=True,
        null=True,
        max_length=256,
    )

    class Meta:
        verbose_name = _("paperless application settings")

    def __str__(self) -> str:  # pragma: no cover
        return "ApplicationConfiguration"


class UserOwnership(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='ownership'
    )
    created_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name='created_users'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'paperless'


class GroupOwnership(models.Model):
    group = models.OneToOneField(
        Group, on_delete=models.CASCADE, related_name='ownership'
    )
    created_by = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name='created_groups'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'paperless'


# ---------------------------------------------------------------------------
# Access control (see paperless/access/)
# ---------------------------------------------------------------------------


class AccessPermission(models.Model):
    """
    Holder for the policy engine's global permissions (``system.manage.all``,
    ``user.update.child``...). No table: it only gives Django a content type
    to hang the Permission rows on, so they can be assigned to users and
    groups like any other permission.
    """

    class Meta:
        app_label = "paperless"
        managed = False
        default_permissions = ()
        permissions = [
            ("system_manage_all", "System: manage everything (except audit logs)"),
            ("user_read_self", "Users: read own account"),
            ("user_update_self", "Users: update own account"),
            ("user_read_child", "Users: read direct child accounts"),
            ("user_update_child", "Users: update direct child accounts"),
            ("user_create_child", "Users: create child accounts"),
            ("user_delete_child", "Users: delete direct child accounts"),
            ("user_read_any", "Users: read any account"),
            ("user_update_any", "Users: update any unrelated account"),
            ("user_delete_any", "Users: delete any unrelated account"),
            ("pdf_view_self", "PDF: view own documents"),
            ("pdf_view_child", "PDF: view documents of direct children"),
            ("pdf_view_any", "PDF: view any document"),
            ("image_upload_self", "Images: upload own images"),
            ("image_upload_child", "Images: upload images for direct children"),
            ("image_upload_any", "Images: upload images for anyone"),
            ("image_delete_self", "Images: delete own images"),
            ("image_delete_child", "Images: delete images of direct children"),
            ("image_delete_any", "Images: delete anyone's images"),
        ]


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Audit log entries are immutable")

    def delete(self):
        raise PermissionError("Audit log entries are immutable")

    def bulk_update(self, *args, **kwargs):
        raise PermissionError("Audit log entries are immutable")


class SecurityAuditLog(models.Model):
    """
    Append-only security audit trail ("AuditLog"). The actor is stored by
    value (id + username), not as a foreign key, so deleting a user never
    rewrites history (ON DELETE SET NULL would be an UPDATE).
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor_id = models.IntegerField(null=True, db_index=True)
    actor_username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=32)
    target_id = models.CharField(max_length=64, blank=True)
    outcome = models.CharField(max_length=16, default="success")
    before_json = models.JSONField(default=dict)
    after_json = models.JSONField(default=dict)
    ip = models.GenericIPAddressField(null=True)
    user_agent = models.CharField(max_length=255, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        app_label = "paperless"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["target_type", "target_id"], name="audit_target_idx"),
        ]
        # Only "view" exists; nobody can be granted add/change/delete.
        default_permissions = ("view",)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("Audit log entries are immutable")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries are immutable")


# ---------------------------------------------------------------------------
# Images (see paperless/images/)
# ---------------------------------------------------------------------------


class Image(models.Model):
    class Status(models.TextChoices):
        PENDING = ("pending", _("pending"))
        READY = ("ready", _("ready"))
        FAILED = ("failed", _("failed"))

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="images",
    )
    uploaded_by = models.ForeignKey(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_images",
    )
    # Display only, sanitised; never used on disk.
    original_name = models.CharField(max_length=255, blank=True)
    alt_text = models.CharField(max_length=500, blank=True)
    # Type detected from the bytes (magic), not from the client.
    source_mime_type = models.CharField(max_length=64)
    # Type of the generated variants.
    mime_type = models.CharField(max_length=64, blank=True)
    size_bytes = models.BigIntegerField()
    width = models.PositiveIntegerField(null=True)
    height = models.PositiveIntegerField(null=True)
    # Private key of the uploaded source while it awaits processing (and
    # afterwards only if keeping originals is enabled).
    storage_key = models.CharField(max_length=255)
    checksum = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error = models.CharField(max_length=255, blank=True)
    idempotency_key = models.CharField(max_length=128, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    files_purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "paperless"
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=["owner", "deleted_at", "-created_at"],
                name="image_owner_live_idx",
            ),
            models.Index(
                fields=["deleted_at", "files_purged_at"],
                name="image_purge_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["uploaded_by", "idempotency_key"],
                name="image_unique_idempotency_key",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Image {self.pk}"


class ImageVariant(models.Model):
    class Kind(models.TextChoices):
        THUMBNAIL = ("thumbnail", _("thumbnail"))
        SMALL = ("small", _("small"))
        MEDIUM = ("medium", _("medium"))
        LARGE = ("large", _("large"))

    image = models.ForeignKey(Image, on_delete=models.CASCADE, related_name="variants")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    storage_key = models.CharField(max_length=255)
    format = models.CharField(max_length=16)
    size_bytes = models.BigIntegerField()
    # sha256 of the encoded bytes: the ETag, and makes URLs cache-busting.
    checksum = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "paperless"
        constraints = [
            # Idempotency of variant generation: one row per (image, kind).
            models.UniqueConstraint(fields=["image", "kind"], name="imagevariant_unique_kind"),
        ]


class JobRecord(models.Model):
    """
    Bookkeeping for background jobs (variant generation, file cleanup):
    the idempotency key makes re-delivered or retried Celery tasks no-ops
    once they succeeded, and attempts/last_error make failures visible.
    """

    class Status(models.TextChoices):
        QUEUED = ("queued", _("queued"))
        RUNNING = ("running", _("running"))
        SUCCEEDED = ("succeeded", _("succeeded"))
        FAILED = ("failed", _("failed"))

    kind = models.CharField(max_length=32)
    idempotency_key = models.CharField(max_length=128, unique=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "paperless"
        indexes = [models.Index(fields=["kind", "status"], name="jobrecord_kind_status_idx")]


class ProtectedAccount(models.Model):
    """
    System accounts that no one can see in the users list or modify through
    the API / Django admin, e.g. the admin seeded on first start-up
    (PAPERLESS_ADMIN_USER). Stored by id, so renaming the account does not
    lift the protection. Removing it requires shell access:

        python3 manage.py shell -c "from paperless.models import ProtectedAccount; \
ProtectedAccount.objects.filter(user__username='admin').delete()"
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="protected_account",
    )
    reason = models.CharField(max_length=64, default="seed_admin")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "paperless"
        default_permissions = ()
