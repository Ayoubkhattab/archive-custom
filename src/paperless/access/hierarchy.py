"""
Parent/child ("father/son") relationships between user accounts.

The parent of a user is ``UserOwnership.created_by``. Lookups are iterative,
bounded, and cycle-safe, so corrupted data can never cause infinite loops or
accidentally grant access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User

MAX_DEPTH = 64


def get_parent_id(user_id: int | None) -> int | None:
    from paperless.models import UserOwnership

    if user_id is None:
        return None
    return (
        UserOwnership.objects.filter(user_id=user_id)
        .values_list("created_by_id", flat=True)
        .first()
    )


def get_ancestor_ids(user_id: int | None) -> list[int]:
    """Parent first, then grandparent, ... (never includes ``user_id``)."""
    ancestors: list[int] = []
    seen = {user_id}
    current = get_parent_id(user_id)
    while current is not None and current not in seen and len(ancestors) < MAX_DEPTH:
        ancestors.append(current)
        seen.add(current)
        current = get_parent_id(current)
    return ancestors


def is_ancestor(candidate: User | int | None, of: User | int | None) -> bool:
    """True if ``candidate`` is the parent, grandparent, ... of ``of``."""
    candidate_id = getattr(candidate, "pk", candidate)
    of_id = getattr(of, "pk", of)
    if candidate_id is None or of_id is None or candidate_id == of_id:
        return False
    return candidate_id in get_ancestor_ids(of_id)


def is_direct_child(candidate: User | int | None, of: User | int | None) -> bool:
    """True if ``candidate`` was created by ``of``."""
    candidate_id = getattr(candidate, "pk", candidate)
    of_id = getattr(of, "pk", of)
    if candidate_id is None or of_id is None or candidate_id == of_id:
        return False
    return get_parent_id(candidate_id) == of_id


def get_direct_child_ids(user: User | int) -> list[int]:
    from paperless.models import UserOwnership

    user_id = getattr(user, "pk", user)
    return list(
        UserOwnership.objects.filter(created_by_id=user_id).values_list(
            "user_id",
            flat=True,
        ),
    )


def would_create_cycle(user: User | int, new_parent: User | int | None) -> bool:
    """Setting ``new_parent`` as parent of ``user`` must not close a loop."""
    user_id = getattr(user, "pk", user)
    parent_id = getattr(new_parent, "pk", new_parent)
    if parent_id is None:
        return False
    if parent_id == user_id:
        return True
    # A loop appears if ``user`` is already an ancestor of the new parent.
    return user_id in get_ancestor_ids(parent_id)
