from django.db import migrations

AUDIT_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION paperless_securityauditlog_immutable()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'paperless_securityauditlog is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS paperless_securityauditlog_no_change
    ON paperless_securityauditlog;
CREATE TRIGGER paperless_securityauditlog_no_change
    BEFORE UPDATE OR DELETE ON paperless_securityauditlog
    FOR EACH STATEMENT EXECUTE FUNCTION paperless_securityauditlog_immutable();
"""

AUDIT_TRIGGER_REVERSE_SQL = """
DROP TRIGGER IF EXISTS paperless_securityauditlog_no_change
    ON paperless_securityauditlog;
DROP FUNCTION IF EXISTS paperless_securityauditlog_immutable();
"""


def add_audit_trigger(apps, schema_editor):
    # Database level guarantee on PostgreSQL. Other databases rely on the
    # model/queryset guards in paperless.models.SecurityAuditLog.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(AUDIT_TRIGGER_SQL)


def remove_audit_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(AUDIT_TRIGGER_REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [
        ("paperless", "0007_access_audit_images"),
    ]

    operations = [
        migrations.RunPython(add_audit_trigger, remove_audit_trigger),
    ]
