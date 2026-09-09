import django.db.models.deletion
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "1076_alter_paperlesstask_task_name"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # -------------------------------------------------------------
        # Document classification taxonomy (تصنيف الوثيقة)
        # -------------------------------------------------------------
        migrations.CreateModel(
            name="DocumentClassification",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=128, verbose_name="name")),
                ("match", models.CharField(blank=True, max_length=256, verbose_name="match")),
                (
                    "matching_algorithm",
                    models.PositiveIntegerField(
                        choices=[
                            (0, "None"),
                            (1, "Any word"),
                            (2, "All words"),
                            (3, "Exact match"),
                            (4, "Regular expression"),
                            (5, "Fuzzy word"),
                            (6, "Automatic"),
                        ],
                        default=1,
                        verbose_name="matching algorithm",
                    ),
                ),
                (
                    "is_insensitive",
                    models.BooleanField(default=True, verbose_name="is insensitive"),
                ),
                (
                    "code",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="Short official code identifying this classification.",
                        max_length=32,
                        null=True,
                        unique=True,
                        verbose_name="classification code",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="owner",
                    ),
                ),
            ],
            options={
                "verbose_name": "document classification",
                "verbose_name_plural": "document classifications",
                "ordering": ("name",),
                "abstract": False,
            },
        ),
        migrations.AddConstraint(
            model_name="documentclassification",
            constraint=models.UniqueConstraint(
                fields=("name", "owner"),
                name="documents_documentclassification_unique_name_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="documentclassification",
            constraint=models.UniqueConstraint(
                condition=models.Q(("owner__isnull", True)),
                fields=("name",),
                name="documents_documentclassification_name_uniq",
            ),
        ),
        # -------------------------------------------------------------
        # Entity (جهة) identification fields
        # -------------------------------------------------------------
        migrations.AddField(
            model_name="correspondent",
            name="code",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    "Short official code identifying this entity in "
                    "correspondence (e.g. MOC-01). Must be unique."
                ),
                max_length=32,
                null=True,
                unique=True,
                verbose_name="entity code",
            ),
        ),
        migrations.AddField(
            model_name="correspondent",
            name="diwan_number",
            field=models.CharField(
                blank=True,
                default="",
                help_text="The registry (diwan) number assigned to this entity.",
                max_length=64,
                verbose_name="diwan number",
            ),
        ),
        migrations.AddField(
            model_name="correspondent",
            name="entity_type",
            field=models.CharField(
                choices=[
                    ("external", "External entity"),
                    ("internal", "Internal department"),
                ],
                db_index=True,
                default="external",
                help_text=(
                    "Whether this entity is external to the organization or an "
                    "internal department. Used to separate internal handling "
                    "time from external turnaround time in reports."
                ),
                max_length=16,
                verbose_name="entity type",
            ),
        ),
        # -------------------------------------------------------------
        # Document routing fields
        # -------------------------------------------------------------
        migrations.AddField(
            model_name="document",
            name="sender",
            field=models.ForeignKey(
                blank=True,
                # Not `sender_id`: the index name Django derives from that
                # column collides with the fossil index left on
                # `correspondent_id` by the 2016 sender->correspondent rename.
                db_column="sending_entity_id",
                help_text="The entity this document was sent from.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sent_documents",
                to="documents.correspondent",
                verbose_name="sending entity",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="recipient",
            field=models.ForeignKey(
                blank=True,
                help_text="The entity this document was sent to.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="received_documents",
                to="documents.correspondent",
                verbose_name="receiving entity",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="classification",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documents",
                to="documents.documentclassification",
                verbose_name="classification",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="diwan_number",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="The registry (diwan) number recorded for this document.",
                max_length=64,
                verbose_name="diwan number",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="sent_date",
            field=models.DateField(
                blank=True,
                db_index=True,
                help_text=(
                    "The date this document was handed over to the receiving entity."
                ),
                null=True,
                verbose_name="sent date",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="internal_closed_date",
            field=models.DateField(
                blank=True,
                db_index=True,
                help_text=(
                    "The date processing of this document was closed internally. "
                    "When set, turnaround is measured from this date instead of "
                    "the sent date."
                ),
                null=True,
                verbose_name="internal closing date",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="returned_date",
            field=models.DateField(
                blank=True,
                db_index=True,
                help_text=(
                    "The date this document came back from the receiving entity."
                ),
                null=True,
                verbose_name="returned date",
            ),
        ),
    ]
