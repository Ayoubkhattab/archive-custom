import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "1077_correspondence_routing_and_bottleneck"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Folder",
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
                ("name", models.CharField(max_length=255, verbose_name="name")),
                (
                    "created",
                    models.DateTimeField(
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name="created",
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
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="children",
                        to="documents.folder",
                        verbose_name="parent",
                    ),
                ),
            ],
            options={
                "verbose_name": "folder",
                "verbose_name_plural": "folders",
                "ordering": ("name",),
                "indexes": [
                    models.Index(
                        fields=["parent", "name"],
                        name="documents_folder_parent_name",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="document",
            name="folder",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="documents",
                to="documents.folder",
                verbose_name="folder",
            ),
        ),
    ]
