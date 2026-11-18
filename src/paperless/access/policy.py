"""
The central policy engine.

    can(actor=..., action=..., resource=..., context=...) -> bool

Decision order (first matching rule wins, everything else is denied):

    0. unauthenticated / inactive actor                           -> deny
       Hard guards that no grant lifts:
       - changing your own privileges / parent / deleting yourself -> deny
         (no self escalation; Django superusers excepted)
       - mutating a Django superuser account as a non-superuser    -> deny
         (otherwise system.manage.all could take over root)
    1. global admin (``system.manage.all`` or Django superuser)    -> allow
    2. self-scoped: ``<family>.self``                              -> allow
    3. direct child: ``<family>.child``                            -> allow
    4. descendant mutating an ancestor (father, grandfather, ...)  -> deny
       immediately, even with ``<family>.any``. This relationship check
       uses only the hierarchy, never roles.
    5. anyone else (unrelated, grandchildren): ``<family>.any``    -> allow
    6. deny.

The engine is synchronous (Django ORM); ``can`` is the Python equivalent of
the requested ``can(): Promise<boolean>``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.contrib.auth.backends import ModelBackend

from paperless.access import constants as C
from paperless.access import hierarchy

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger("paperless.access")

_model_backend = ModelBackend()


@dataclass(frozen=True)
class Resource:
    """
    What is being accessed.

    ``type`` is one of "user", "pdf", "image".
    ``owner_id`` is the account the resource belongs to: for "user" that is
    the target account itself, for "pdf"/"image" the owner of the object.
    """

    type: str
    owner_id: int | None
    obj: Any = None

    @classmethod
    def user(cls, target: User | None) -> Resource:
        return cls("user", getattr(target, "pk", None), target)

    @classmethod
    def pdf(cls, document) -> Resource:
        return cls("pdf", getattr(document, "owner_id", None), document)

    @classmethod
    def image(cls, image=None, *, owner_id: int | None = None) -> Resource:
        if image is not None:
            owner_id = image.owner_id
        return cls("image", owner_id, image)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    rule: str = ""
    details: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.allowed


def _allow(rule: str, reason: str = "allowed") -> Decision:
    return Decision(allowed=True, reason=reason, rule=rule)


def _deny(rule: str, reason: str) -> Decision:
    return Decision(allowed=False, reason=reason, rule=rule)


# (resource type, action) -> permission family. ``None`` scopes mean the
# action is reserved for global admins.
_FAMILIES: dict[tuple[str, str], str | None] = {
    ("user", C.Action.READ): "user.read",
    ("user", C.Action.UPDATE): "user.update",
    ("user", C.Action.DELETE): "user.delete",
    ("user", C.Action.CHANGE_PRIVILEGES): "user.update",
    ("user", C.Action.CHANGE_RELATIONSHIP): None,
    ("user", C.Action.MANAGE_SECURITY): None,
    ("pdf", C.Action.VIEW): "pdf.view",
    ("image", C.Action.UPLOAD): "image.upload",
    ("image", C.Action.DELETE): "image.delete",
    ("image", C.Action.VIEW): "image.view",  # derived, see _has_scope
}

# Things you may never do to yourself, whatever you hold.
_NEVER_ON_SELF = {
    ("user", C.Action.DELETE),
    ("user", C.Action.CHANGE_PRIVILEGES),
    ("user", C.Action.CHANGE_RELATIONSHIP),
}


def implicit_permissions() -> frozenset[str]:
    """Self-scoped permissions every active account holds without a grant."""
    return frozenset(getattr(settings, "ACCESS_IMPLICIT_PERMISSIONS", ()))


def is_system_admin(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    # Ask ModelBackend directly: the SystemManageAll backend answers "yes" to
    # everything for admins, so going through user.has_perm would be circular.
    return _model_backend.has_perm(user, C.django_perm(C.SYSTEM_MANAGE_ALL))


def has_permission(actor, permission: str) -> bool:
    """RBAC check for one dotted permission name (no relationship logic)."""
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    if not actor.is_active:
        return False
    if permission in implicit_permissions():
        return True
    if actor.has_perm(C.django_perm(permission)):
        return True
    return any(actor.has_perm(p) for p in C.LEGACY_EQUIVALENTS.get(permission, ()))


def _has_scope(actor, family: str, scope: str) -> bool:
    if family == "image.view":
        # Seeing an image is implied by being allowed to manage it.
        return has_permission(actor, f"image.upload.{scope}") or has_permission(
            actor,
            f"image.delete.{scope}",
        )
    return has_permission(actor, f"{family}.{scope}")


def decide(*, actor, action: str, resource: Resource, context=None) -> Decision:
    context = context or {}

    # 0. authentication
    if actor is None or not getattr(actor, "is_authenticated", False):
        return _deny("unauthenticated", "Authentication required")
    if not actor.is_active:
        return _deny("inactive", "Account is inactive")

    key = (resource.type, action)
    if key not in _FAMILIES and not (
        resource.type == "user" and action == C.Action.CREATE
    ):
        return _deny("unknown-action", "Unknown action")

    target_id = resource.owner_id
    is_self = target_id is not None and target_id == actor.pk
    mutating = action in C.Action.MUTATING

    # Guards that no grant can lift:
    # (a) nobody changes their own authority (no self escalation);
    if is_self and key in _NEVER_ON_SELF and not actor.is_superuser:
        return _deny("self-escalation", "You cannot change this on your own account")
    # (b) superuser accounts are only mutable by superusers, otherwise
    #     system.manage.all could take over root via a password reset.
    if (
        resource.type == "user"
        and mutating
        and resource.obj is not None
        and getattr(resource.obj, "is_superuser", False)
        and not actor.is_superuser
    ):
        return _deny(
            "superuser-protected",
            "Superusers can only be modified by other superusers",
        )

    # 1. global admin
    if is_system_admin(actor):
        return _allow("global-admin")

    # Creating an account always creates a child of the actor.
    if resource.type == "user" and action == C.Action.CREATE:
        if has_permission(actor, C.USER_CREATE_CHILD):
            return _allow("create-child")
        return _deny("no-permission", "You do not have permission to do this")

    family = _FAMILIES[key]
    if family is None:
        # Reserved for global admins, except managing your own 2FA.
        if is_self and action == C.Action.MANAGE_SECURITY:
            return _allow("self")
        return _deny("admin-only", "You do not have permission to do this")

    # 2. self-scoped
    if is_self:
        if _has_scope(actor, family, "self"):
            return _allow("self")
        return _deny("no-permission", "You do not have permission to do this")

    # 3. direct child
    if (
        target_id is not None
        and hierarchy.is_direct_child(target_id, actor)
        and _has_scope(actor, family, "child")
    ):
        return _allow("direct-child")

    # 4. descendant acting on an ancestor: denied immediately, whatever
    #    general (".any") permission the son holds. Reads stay governed by
    #    ".any" so a son can still pick his father in sharing dialogs.
    if mutating and hierarchy.is_ancestor(target_id, actor):
        return _deny(
            "descendant-to-ancestor",
            "You cannot modify an account above you in the hierarchy",
        )

    # 5. unrelated (incl. grandchildren) requires ".any"
    if _has_scope(actor, family, "any"):
        return _allow("any")

    # 6. deny by default
    return _deny("default-deny", "You do not have permission to do this")


def can(*, actor, action: str, resource: Resource, context=None) -> bool:
    decision = decide(actor=actor, action=action, resource=resource, context=context)
    if not decision.allowed:
        logger.debug(
            "Access denied: actor=%s action=%s resource=%s:%s rule=%s",
            getattr(actor, "pk", None),
            action,
            resource.type,
            resource.owner_id,
            decision.rule,
        )
    return decision.allowed
