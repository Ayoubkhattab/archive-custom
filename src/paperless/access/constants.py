"""
Permission names used by the policy engine.

Policy code always speaks in dotted names (``user.update.child``). Django
stores permissions as ``<app_label>.<codename>`` and codenames are best kept
free of dots (other code splits permission strings on "."), so every dotted
name maps to a codename on the unmanaged ``paperless.AccessPermission`` model.
"""

from __future__ import annotations

APP_LABEL = "paperless"

# --- Global -----------------------------------------------------------------
SYSTEM_MANAGE_ALL = "system.manage.all"

# --- Users ------------------------------------------------------------------
USER_READ_SELF = "user.read.self"
USER_UPDATE_SELF = "user.update.self"
USER_READ_CHILD = "user.read.child"
USER_UPDATE_CHILD = "user.update.child"
USER_CREATE_CHILD = "user.create.child"
USER_DELETE_CHILD = "user.delete.child"
USER_READ_ANY = "user.read.any"
USER_UPDATE_ANY = "user.update.any"
USER_DELETE_ANY = "user.delete.any"

# --- PDF --------------------------------------------------------------------
PDF_VIEW_SELF = "pdf.view.self"
PDF_VIEW_CHILD = "pdf.view.child"
PDF_VIEW_ANY = "pdf.view.any"

# --- Images -----------------------------------------------------------------
IMAGE_UPLOAD_SELF = "image.upload.self"
IMAGE_UPLOAD_CHILD = "image.upload.child"
IMAGE_UPLOAD_ANY = "image.upload.any"
IMAGE_DELETE_SELF = "image.delete.self"
IMAGE_DELETE_CHILD = "image.delete.child"
IMAGE_DELETE_ANY = "image.delete.any"

# Human readable descriptions, used for the Django Permission rows.
PERMISSION_DESCRIPTIONS: dict[str, str] = {
    SYSTEM_MANAGE_ALL: "System: manage everything (except audit logs)",
    USER_READ_SELF: "Users: read own account",
    USER_UPDATE_SELF: "Users: update own account",
    USER_READ_CHILD: "Users: read direct child accounts",
    USER_UPDATE_CHILD: "Users: update direct child accounts",
    USER_CREATE_CHILD: "Users: create child accounts",
    USER_DELETE_CHILD: "Users: delete direct child accounts",
    USER_READ_ANY: "Users: read any account",
    USER_UPDATE_ANY: "Users: update any unrelated account",
    USER_DELETE_ANY: "Users: delete any unrelated account",
    PDF_VIEW_SELF: "PDF: view own documents",
    PDF_VIEW_CHILD: "PDF: view documents of direct children",
    PDF_VIEW_ANY: "PDF: view any document",
    IMAGE_UPLOAD_SELF: "Images: upload own images",
    IMAGE_UPLOAD_CHILD: "Images: upload images for direct children",
    IMAGE_UPLOAD_ANY: "Images: upload images for anyone",
    IMAGE_DELETE_SELF: "Images: delete own images",
    IMAGE_DELETE_CHILD: "Images: delete images of direct children",
    IMAGE_DELETE_ANY: "Images: delete anyone's images",
}

ALL_PERMISSIONS: tuple[str, ...] = tuple(PERMISSION_DESCRIPTIONS)


def codename(permission: str) -> str:
    """``user.update.child`` -> ``user_update_child``."""
    return permission.replace(".", "_")


def django_perm(permission: str) -> str:
    """``user.update.child`` -> ``paperless.user_update_child``."""
    return f"{APP_LABEL}.{codename(permission)}"


# Legacy Django model permissions that already meant the same thing in this
# project before the policy engine existed. Honouring them keeps existing
# role/group setups working:
#   * ``auth.change_user`` used to mean "change users I created".
#   * ``auth.view_user`` was needed to list users in the sharing dialogs.
LEGACY_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    USER_READ_ANY: ("auth.view_user",),
    USER_READ_CHILD: ("auth.view_user",),
    USER_UPDATE_CHILD: ("auth.change_user",),
    USER_CREATE_CHILD: ("auth.add_user",),
    USER_DELETE_CHILD: ("auth.delete_user",),
}

# Fields on a user that change authority, not just profile data. Only an
# actor with explicit rights may touch them, and never on themselves.
PRIVILEGED_USER_FIELDS = frozenset(
    {"is_superuser", "is_staff", "groups", "user_permissions", "parent"},
)

# Permissions that even ``system.manage.all`` never receives: audit trails
# are append-only for everyone.
AUDIT_LOG_PROTECTED_PERMS = frozenset(
    {
        "auditlog.change_logentry",
        "auditlog.delete_logentry",
        "paperless.change_securityauditlog",
        "paperless.delete_securityauditlog",
        "paperless.add_securityauditlog",
    },
)


class Action:
    """Actions understood by ``policy.can``."""

    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    CREATE = "create"
    CHANGE_PRIVILEGES = "change_privileges"
    CHANGE_RELATIONSHIP = "change_relationship"
    MANAGE_SECURITY = "manage_security"  # e.g. disable someone's 2FA
    VIEW = "view"
    UPLOAD = "upload"

    MUTATING = frozenset(
        {
            UPDATE,
            DELETE,
            CHANGE_PRIVILEGES,
            CHANGE_RELATIONSHIP,
            MANAGE_SECURITY,
            UPLOAD,
        },
    )
