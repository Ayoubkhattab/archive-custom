"""
Permission service: applies the policy engine to concrete user changes.

The serializer (schema validator) runs first, so everything here works on
validated data: no raw client role/permission fields are ever trusted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q
from django.db.models import QuerySet
from rest_framework.exceptions import PermissionDenied

from paperless.access import constants as C
from paperless.access import hierarchy
from paperless.access.policy import Resource
from paperless.access.policy import decide
from paperless.access.policy import has_permission
from paperless.access.policy import is_system_admin

if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.contrib.auth.models import Group
    from django.contrib.auth.models import Permission
    from django.contrib.auth.models import User

FORBIDDEN_MESSAGE = "You do not have permission to perform this action."


def require(actor, action: str, resource: Resource, context=None) -> None:
    """Raise a 403 with a safe message unless the policy allows the action."""
    decision = decide(actor=actor, action=action, resource=resource, context=context)
    if not decision.allowed:
        raise PermissionDenied(decision.reason or FORBIDDEN_MESSAGE)


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def readable_users(actor, queryset: QuerySet[User]) -> QuerySet[User]:
    """Users the actor may see: everyone with ``user.read.any``, else self + children."""
    if is_system_admin(actor) or has_permission(actor, C.USER_READ_ANY):
        return queryset
    visible = (
        Q(pk=actor.pk) if has_permission(actor, C.USER_READ_SELF) else Q(pk__in=[])
    )
    if has_permission(actor, C.USER_READ_CHILD):
        visible |= Q(pk__in=hierarchy.get_direct_child_ids(actor))
    return queryset.filter(visible)


def effective_permissions(actor) -> list[str]:
    """Dotted permissions the actor holds (for the UI; never trusted by the server)."""
    if actor is None or not actor.is_authenticated:
        return []
    if is_system_admin(actor):
        return list(C.ALL_PERMISSIONS)
    return [p for p in C.ALL_PERMISSIONS if has_permission(actor, p)]


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def _perm_names(perms: Iterable[Permission]) -> set[str]:
    return {f"{p.content_type.app_label}.{p.codename}" for p in perms}


def _check_grants(actor, *, added_perms: set[Permission], added_groups: set[Group]):
    """You can only hand out what you hold yourself."""
    if actor.is_superuser:
        return
    for perm in sorted(_perm_names(added_perms)):
        if not actor.has_perm(perm):
            raise PermissionDenied(
                f"Cannot grant permissions you do not have: {perm.split('.', 1)[1]}",
            )
    if added_groups and not is_system_admin(actor):
        mine = set(actor.groups.values_list("pk", flat=True))
        if any(g.pk not in mine for g in added_groups):
            raise PermissionDenied("Cannot assign groups you do not belong to")
        # Being a member already means holding the group's permissions, but
        # double check in case of direct DB edits.
        for group in added_groups:
            for perm in _perm_names(group.permissions.select_related("content_type")):
                if not actor.has_perm(perm):
                    raise PermissionDenied("Cannot assign groups you do not belong to")


def _check_flags(actor, target: User | None, data: dict) -> None:
    if "is_superuser" in data:
        current = bool(target and target.is_superuser)
        if bool(data["is_superuser"]) != current and not actor.is_superuser:
            raise PermissionDenied(
                "Superuser status can only be changed by a superuser",
            )
    if "is_staff" in data:
        current = bool(target and target.is_staff)
        if bool(data["is_staff"]) != current and not is_system_admin(actor):
            raise PermissionDenied(
                "Staff status can only be changed by an administrator",
            )


def _check_parent(actor, target: User | None, new_parent: User | None) -> None:
    resource = Resource.user(target) if target else Resource("user", None)
    require(actor, C.Action.CHANGE_RELATIONSHIP, resource)
    if target is not None and hierarchy.would_create_cycle(target, new_parent):
        raise PermissionDenied("This parent would create a cycle in the hierarchy")
    if new_parent is not None and not new_parent.is_active:
        raise PermissionDenied("The parent account is inactive")


def privileged_changes(target: User, data: dict) -> dict:
    """Which privileged fields actually change, with before/after values."""
    changes: dict = {}
    for flag in ("is_superuser", "is_staff"):
        if flag in data and bool(data[flag]) != getattr(target, flag):
            changes[flag] = {"from": getattr(target, flag), "to": bool(data[flag])}
    if "groups" in data:
        before = set(target.groups.all())
        after = set(data["groups"])
        if before != after:
            changes["groups"] = {
                "added": sorted(g.name for g in after - before),
                "removed": sorted(g.name for g in before - after),
            }
    if "user_permissions" in data:
        before = set(target.user_permissions.all())
        after = set(data["user_permissions"])
        if before != after:
            changes["user_permissions"] = {
                "added": sorted(p.codename for p in after - before),
                "removed": sorted(p.codename for p in before - after),
            }
    if "parent" in data:
        old = hierarchy.get_parent_id(target.pk)
        new = getattr(data["parent"], "pk", None)
        if old != new:
            changes["parent"] = {"from": old, "to": new}
    return changes


def check_user_update(actor, target: User, data: dict) -> dict:
    """
    Authorize an update of ``target`` with validated ``data``.
    Returns the privileged changes (for the audit log).
    """
    # Relationship + role check for the update itself (includes the
    # descendant-to-ancestor guard and superuser protection).
    require(actor, C.Action.UPDATE, Resource.user(target))

    changes = privileged_changes(target, data)
    if not changes:
        return changes

    require(actor, C.Action.CHANGE_PRIVILEGES, Resource.user(target))
    _check_flags(actor, target, data)
    if "parent" in changes:
        _check_parent(actor, target, data.get("parent"))
    added_perms = (
        set(data.get("user_permissions", []))
        - set(
            target.user_permissions.all(),
        )
        if "user_permissions" in data
        else set()
    )
    added_groups = (
        set(data.get("groups", []))
        - set(
            target.groups.all(),
        )
        if "groups" in data
        else set()
    )
    _check_grants(actor, added_perms=added_perms, added_groups=added_groups)
    return changes


def check_user_create(actor, data: dict) -> None:
    require(actor, C.Action.CREATE, Resource("user", None))
    _check_flags(actor, None, data)
    if data.get("parent") is not None and data["parent"].pk != actor.pk:
        _check_parent(actor, None, data["parent"])
    _check_grants(
        actor,
        added_perms=set(data.get("user_permissions", []) or []),
        added_groups=set(data.get("groups", []) or []),
    )


def check_user_delete(actor, target: User) -> None:
    require(actor, C.Action.DELETE, Resource.user(target))


def ancestors_in_group(actor, group: Group) -> bool:
    """Would changing this group change the authority of one of my ancestors?"""
    ancestor_ids = hierarchy.get_ancestor_ids(actor.pk)
    return bool(ancestor_ids) and group.user_set.filter(pk__in=ancestor_ids).exists()
