"""
Management command to set up initial document structure for enterprise use.

Creates document types, tags (with hierarchy), correspondents, storage paths,
and custom fields. Idempotent - safe to run multiple times.
"""

import logging

from django.core.management.base import BaseCommand

from documents.models import Correspondent
from documents.models import CustomField
from documents.models import DocumentType
from documents.models import MatchingModel
from documents.models import StoragePath
from documents.models import Tag

logger = logging.getLogger("paperless.management.setup_document_structure")

# =============================================================================
# Data Definitions
# =============================================================================

DOCUMENT_TYPES = [
    {
        "name": "Invoice / فاتورة",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(invoice|فاتور[ةه]|bill|receipt|إيصال)",
        "is_insensitive": True,
    },
    {
        "name": "Contract / عقد",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(contract|agreement|عقد|اتفاقي[ةه])",
        "is_insensitive": True,
    },
    {
        "name": "Letter / خطاب",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(letter|correspondence|خطاب|مراسل[ةه])",
        "is_insensitive": True,
    },
    {
        "name": "Report / تقرير",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(report|تقرير|analysis|تحليل)",
        "is_insensitive": True,
    },
    {
        "name": "HR Document / مستند موارد بشرية",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(employee|salary|payroll|موظف|راتب|إجاز[ةه]|leave)",
        "is_insensitive": True,
    },
    {
        "name": "Legal Document / مستند قانوني",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(legal|court|lawsuit|قانون|محكم[ةه]|دعو[ىي])",
        "is_insensitive": True,
    },
    {
        "name": "Government / حكومي",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(government|ministry|وزار[ةه]|حكوم|هيئ[ةه])",
        "is_insensitive": True,
    },
    {
        "name": "Financial / مالي",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(financial|statement|bank|بنك|مصرف|مالي[ةه]|كشف)",
        "is_insensitive": True,
    },
]

# Tags with hierarchical structure: {parent_name: {color, children: [...]}}
TAGS = {
    "Status / الحالة": {
        "color": "#2196F3",
        "children": [
            {"name": "New / جديد", "color": "#4CAF50", "is_inbox_tag": True},
            {"name": "In Review / قيد المراجعة", "color": "#FF9800"},
            {"name": "Approved / معتمد", "color": "#8BC34A"},
            {"name": "Rejected / مرفوض", "color": "#f44336"},
            {"name": "Archived / مؤرشف", "color": "#9E9E9E"},
        ],
    },
    "Department / القسم": {
        "color": "#9C27B0",
        "children": [
            {"name": "Finance / المالية", "color": "#E91E63"},
            {"name": "HR / الموارد البشرية", "color": "#00BCD4"},
            {"name": "Legal / القانونية", "color": "#795548"},
            {"name": "Operations / العمليات", "color": "#FF5722"},
            {"name": "Management / الإدارة", "color": "#3F51B5"},
            {"name": "IT / تقنية المعلومات", "color": "#009688"},
        ],
    },
    "Priority / الأولوية": {
        "color": "#FF5722",
        "children": [
            {"name": "Urgent / عاجل", "color": "#f44336"},
            {"name": "High / عالي", "color": "#FF9800"},
            {"name": "Normal / عادي", "color": "#2196F3"},
            {"name": "Low / منخفض", "color": "#9E9E9E"},
        ],
    },
    "Confidentiality / السرية": {
        "color": "#f44336",
        "children": [
            {"name": "Public / عام", "color": "#4CAF50"},
            {"name": "Internal / داخلي", "color": "#2196F3"},
            {"name": "Confidential / سري", "color": "#FF9800"},
            {"name": "Top Secret / سري للغاية", "color": "#f44336"},
        ],
    },
    "Year / السنة": {
        "color": "#607D8B",
        "children": [
            {"name": "2024", "color": "#607D8B"},
            {"name": "2025", "color": "#607D8B"},
            {"name": "2026", "color": "#607D8B"},
        ],
    },
}

CORRESPONDENTS = [
    {
        "name": "Ministry of Commerce / وزارة التجارة",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(ministry.*commerce|وزار[ةه].*تجار)",
        "is_insensitive": True,
    },
    {
        "name": "ZATCA / هيئة الزكاة والضريبة",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(zatca|زكا[ةه]|ضريب|gazt)",
        "is_insensitive": True,
    },
    {
        "name": "Bank / البنك",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(bank|مصرف|بنك)",
        "is_insensitive": True,
    },
    {
        "name": "Ministry of Labor / وزارة العمل",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(ministry.*labor|وزار[ةه].*عمل|وزار[ةه].*موارد)",
        "is_insensitive": True,
    },
    {
        "name": "GOSI / التأمينات الاجتماعية",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(gosi|تأمينات|اجتماعي)",
        "is_insensitive": True,
    },
]

STORAGE_PATHS = [
    {
        "name": "By Type and Year / حسب النوع والسنة",
        "path": "{{ document_type }}/{{ created_year }}/{{ title }}",
        "matching_algorithm": MatchingModel.MATCH_NONE,
        "match": "",
    },
    {
        "name": "Invoices / الفواتير",
        "path": "Invoices/{{ created_year }}/{{ created_month }}/{{ correspondent }}/{{ title }}",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(invoice|فاتور)",
        "is_insensitive": True,
    },
    {
        "name": "Contracts / العقود",
        "path": "Contracts/{{ correspondent }}/{{ created_year }}/{{ title }}",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(contract|عقد)",
        "is_insensitive": True,
    },
    {
        "name": "HR Documents / الموارد البشرية",
        "path": "HR/{{ created_year }}/{{ title }}",
        "matching_algorithm": MatchingModel.MATCH_REGEX,
        "match": r"(?i)(employee|salary|موظف|راتب)",
        "is_insensitive": True,
    },
    {
        "name": "By Department / حسب القسم",
        "path": "Department/{{ document_type }}/{{ created_year }}/{{ title }}",
        "matching_algorithm": MatchingModel.MATCH_NONE,
        "match": "",
    },
]

CUSTOM_FIELDS = [
    # Invoice fields
    {"name": "Invoice Number / رقم الفاتورة", "data_type": "string"},
    {"name": "Invoice Date / تاريخ الفاتورة", "data_type": "date"},
    {"name": "Invoice Amount / المبلغ", "data_type": "monetary"},
    {"name": "Tax Amount / مبلغ الضريبة", "data_type": "monetary"},
    {
        "name": "Payment Status / حالة الدفع",
        "data_type": "select",
        "extra_data": {
            "select_options": [
                {"id": "0", "label": "Pending / معلق"},
                {"id": "1", "label": "Paid / مدفوع"},
                {"id": "2", "label": "Overdue / متأخر"},
                {"id": "3", "label": "Cancelled / ملغى"},
            ],
        },
    },
    # Contract fields
    {"name": "Contract Number / رقم العقد", "data_type": "string"},
    {"name": "Contract Start Date / تاريخ بداية العقد", "data_type": "date"},
    {"name": "Contract End Date / تاريخ نهاية العقد", "data_type": "date"},
    {"name": "Contract Value / قيمة العقد", "data_type": "monetary"},
    {
        "name": "Contract Status / حالة العقد",
        "data_type": "select",
        "extra_data": {
            "select_options": [
                {"id": "0", "label": "Draft / مسودة"},
                {"id": "1", "label": "Active / نشط"},
                {"id": "2", "label": "Expired / منتهي"},
                {"id": "3", "label": "Terminated / ملغى"},
            ],
        },
    },
    # General fields
    {"name": "Reference Number / رقم المرجع", "data_type": "string"},
    {"name": "Expiry Date / تاريخ الانتهاء", "data_type": "date"},
    {"name": "Notes / ملاحظات", "data_type": "longtext"},
]


# =============================================================================
# Management Command
# =============================================================================


class Command(BaseCommand):
    help = (
        "Sets up initial document structure for enterprise use "
        "(types, tags, correspondents, storage paths, custom fields). "
        "Idempotent - safe to run multiple times."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            default=False,
            action="store_true",
            help="Show what would be created without making changes",
        )
        parser.add_argument(
            "--component",
            type=str,
            default="all",
            choices=[
                "all",
                "types",
                "tags",
                "correspondents",
                "storage_paths",
                "custom_fields",
            ],
            help="Only create a specific component",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        component = options["component"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes will be made"))

        components = {
            "types": ("Document Types", self._create_document_types),
            "tags": ("Tags", self._create_tags),
            "correspondents": ("Correspondents", self._create_correspondents),
            "storage_paths": ("Storage Paths", self._create_storage_paths),
            "custom_fields": ("Custom Fields", self._create_custom_fields),
        }

        if component == "all":
            to_run = components.items()
        else:
            to_run = [(component, components[component])]

        total_created = 0
        total_existing = 0

        for key, (label, func) in to_run:
            self.stdout.write(f"\n{'=' * 50}")
            self.stdout.write(f"  {label}")
            self.stdout.write(f"{'=' * 50}")
            created, existing = func(dry_run)
            total_created += created
            total_existing += existing

        self.stdout.write(f"\n{'=' * 50}")
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN complete: {total_created} would be created, "
                    f"{total_existing} already exist",
                ),
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Setup complete: {total_created} created, "
                    f"{total_existing} already existed",
                ),
            )

    def _create_document_types(self, dry_run=False):
        created_count = 0
        existing_count = 0

        for dt_data in DOCUMENT_TYPES:
            if dry_run:
                exists = DocumentType.objects.filter(
                    name=dt_data["name"],
                    owner__isnull=True,
                ).exists()
                status = "EXISTS" if exists else "WOULD CREATE"
                if exists:
                    existing_count += 1
                else:
                    created_count += 1
            else:
                obj, created = DocumentType.objects.get_or_create(
                    name=dt_data["name"],
                    owner=None,
                    defaults={
                        "matching_algorithm": dt_data["matching_algorithm"],
                        "match": dt_data["match"],
                        "is_insensitive": dt_data.get("is_insensitive", True),
                    },
                )
                status = "CREATED" if created else "EXISTS"
                if created:
                    created_count += 1
                else:
                    existing_count += 1

            self.stdout.write(f"  [{status}] DocumentType: {dt_data['name']}")

        return created_count, existing_count

    def _create_tags(self, dry_run=False):
        created_count = 0
        existing_count = 0

        for parent_name, parent_data in TAGS.items():
            # Create parent tag
            if dry_run:
                parent_exists = Tag.objects.filter(
                    name=parent_name,
                    owner__isnull=True,
                ).exists()
                status = "EXISTS" if parent_exists else "WOULD CREATE"
                if parent_exists:
                    existing_count += 1
                else:
                    created_count += 1
                parent_tag = Tag.objects.filter(
                    name=parent_name,
                    owner__isnull=True,
                ).first()
            else:
                parent_tag, created = Tag.objects.get_or_create(
                    name=parent_name,
                    owner=None,
                    defaults={
                        "color": parent_data["color"],
                        "matching_algorithm": MatchingModel.MATCH_NONE,
                    },
                )
                status = "CREATED" if created else "EXISTS"
                if created:
                    created_count += 1
                else:
                    existing_count += 1

            self.stdout.write(f"  [{status}] Tag (parent): {parent_name}")

            # Create child tags
            for child_data in parent_data.get("children", []):
                if dry_run:
                    child_exists = Tag.objects.filter(
                        name=child_data["name"],
                        owner__isnull=True,
                    ).exists()
                    status = "EXISTS" if child_exists else "WOULD CREATE"
                    if child_exists:
                        existing_count += 1
                    else:
                        created_count += 1
                else:
                    child_tag, created = Tag.objects.get_or_create(
                        name=child_data["name"],
                        owner=None,
                        defaults={
                            "color": child_data.get("color", "#a6cee3"),
                            "matching_algorithm": MatchingModel.MATCH_NONE,
                            "is_inbox_tag": child_data.get("is_inbox_tag", False),
                        },
                    )

                    # Set parent if tag was just created or has no parent
                    if parent_tag and (created or child_tag.tn_parent != parent_tag):
                        child_tag.tn_parent = parent_tag
                        child_tag.save()

                    status = "CREATED" if created else "EXISTS"
                    if created:
                        created_count += 1
                    else:
                        existing_count += 1

                self.stdout.write(
                    f"    [{status}] Tag (child): {child_data['name']}",
                )

        return created_count, existing_count

    def _create_correspondents(self, dry_run=False):
        created_count = 0
        existing_count = 0

        for corr_data in CORRESPONDENTS:
            if dry_run:
                exists = Correspondent.objects.filter(
                    name=corr_data["name"],
                    owner__isnull=True,
                ).exists()
                status = "EXISTS" if exists else "WOULD CREATE"
                if exists:
                    existing_count += 1
                else:
                    created_count += 1
            else:
                obj, created = Correspondent.objects.get_or_create(
                    name=corr_data["name"],
                    owner=None,
                    defaults={
                        "matching_algorithm": corr_data["matching_algorithm"],
                        "match": corr_data["match"],
                        "is_insensitive": corr_data.get("is_insensitive", True),
                    },
                )
                status = "CREATED" if created else "EXISTS"
                if created:
                    created_count += 1
                else:
                    existing_count += 1

            self.stdout.write(f"  [{status}] Correspondent: {corr_data['name']}")

        return created_count, existing_count

    def _create_storage_paths(self, dry_run=False):
        created_count = 0
        existing_count = 0

        for sp_data in STORAGE_PATHS:
            if dry_run:
                exists = StoragePath.objects.filter(
                    name=sp_data["name"],
                    owner__isnull=True,
                ).exists()
                status = "EXISTS" if exists else "WOULD CREATE"
                if exists:
                    existing_count += 1
                else:
                    created_count += 1
            else:
                obj, created = StoragePath.objects.get_or_create(
                    name=sp_data["name"],
                    owner=None,
                    defaults={
                        "path": sp_data["path"],
                        "matching_algorithm": sp_data["matching_algorithm"],
                        "match": sp_data.get("match", ""),
                        "is_insensitive": sp_data.get("is_insensitive", True),
                    },
                )
                status = "CREATED" if created else "EXISTS"
                if created:
                    created_count += 1
                else:
                    existing_count += 1

            self.stdout.write(f"  [{status}] StoragePath: {sp_data['name']}")

        return created_count, existing_count

    def _create_custom_fields(self, dry_run=False):
        created_count = 0
        existing_count = 0

        for cf_data in CUSTOM_FIELDS:
            if dry_run:
                exists = CustomField.objects.filter(name=cf_data["name"]).exists()
                status = "EXISTS" if exists else "WOULD CREATE"
                if exists:
                    existing_count += 1
                else:
                    created_count += 1
            else:
                obj, created = CustomField.objects.get_or_create(
                    name=cf_data["name"],
                    defaults={
                        "data_type": cf_data["data_type"],
                        "extra_data": cf_data.get("extra_data"),
                    },
                )
                status = "CREATED" if created else "EXISTS"
                if created:
                    created_count += 1
                else:
                    existing_count += 1

            self.stdout.write(f"  [{status}] CustomField: {cf_data['name']}")

        return created_count, existing_count
