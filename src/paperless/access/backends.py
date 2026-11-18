"""
Authentication backend that turns ``system.manage.all`` into a real global
permission: holders pass every ``user.has_perm()`` check, model or object
level, except the audit log permissions (audit trails are immutable).

It never authenticates anyone (``authenticate`` returns None); it only
answers authorization questions.
"""

from __future__ import annotations

from paperless.access.constants import AUDIT_LOG_PROTECTED_PERMS
from paperless.access.policy import is_system_admin


class SystemManageAllBackend:
    def authenticate(self, request, **credentials):
        return None

    def get_user(self, user_id):
        return None

    def has_perm(self, user_obj, perm, obj=None):
        if perm in AUDIT_LOG_PROTECTED_PERMS:
            return False
        if user_obj is None or user_obj.is_superuser:
            # Superusers are already handled by Django itself.
            return False
        return is_system_admin(user_obj)

    def has_module_perms(self, user_obj, app_label):
        return is_system_admin(user_obj)
