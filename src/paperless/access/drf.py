"""
DRF integration (Django equivalents of ``requirePermission`` /
``requireRelationship``):

    class ImageViewSet(PermissionScopedMixin, ViewSet):
        required_permission = {"POST": "image.upload.*", "DELETE": "image.delete.*"}
        permission_classes = [IsAuthenticated, HasPermission, HasRelationship]

* ``HasPermission`` - coarse RBAC gate: does the actor hold the view's
  ``required_permission`` in at least one scope (``family.*``)?
* ``HasRelationship`` - object level: asks the policy engine about the
  concrete object, where the parent/child rules live.
* ``IsGlobalAdmin`` - ``system.manage.all`` (or Django superuser) only.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from paperless.access.exceptions import scoped_exception_handler
from paperless.access.policy import can
from paperless.access.policy import has_permission
from paperless.access.policy import is_system_admin

_SCOPES = ("self", "child", "any")
SAFE_MESSAGE = "You do not have permission to perform this action."


def holds(actor, permission: str) -> bool:
    """``family.*`` matches any scope of the family."""
    if is_system_admin(actor):
        return True
    if permission.endswith(".*"):
        family = permission[:-2]
        if family == "image.view":
            return holds(actor, "image.upload.*") or holds(actor, "image.delete.*")
        return any(has_permission(actor, f"{family}.{s}") for s in _SCOPES)
    return has_permission(actor, permission)


class HasPermission(BasePermission):
    """
    Reads ``required_permission`` from the view: a string, or a dict keyed by
    HTTP method. A method missing from the dict is denied (deny by default).
    """

    message = SAFE_MESSAGE

    def has_permission(self, request, view):
        actor = request.user
        if not actor or not actor.is_authenticated:
            return False
        required = getattr(view, "required_permission", None)
        if isinstance(required, dict):
            required = required.get(request.method)
        if not required:
            return False
        if isinstance(required, str):
            required = (required,)
        return any(holds(actor, p) for p in required)


class HasRelationship(BasePermission):
    """
    Object level check through the policy engine. The view maps the request
    onto the policy with ``get_policy_action(request)`` and
    ``get_policy_resource(obj)``.
    """

    message = SAFE_MESSAGE

    def has_object_permission(self, request, view, obj):
        return can(
            actor=request.user,
            action=view.get_policy_action(request),
            resource=view.get_policy_resource(obj),
        )


# Backwards compatible alias.
RequireRelationship = HasRelationship


class IsGlobalAdmin(BasePermission):
    message = SAFE_MESSAGE

    def has_permission(self, request, view):
        return is_system_admin(request.user)


class PermissionScopedMixin:
    """
    Gives a view the policy-aware permission classes and the consistent
    error format (401/403/404/422/429/500).
    """

    required_permission: str | dict | None = None
    #: maps HTTP method -> policy action, used by HasRelationship
    relationship_guard: dict[str, str] = {}

    def get_policy_action(self, request) -> str:
        return self.relationship_guard[request.method]

    def get_exception_handler(self):
        return scoped_exception_handler
