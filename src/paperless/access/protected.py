"""
Protected system accounts (the admin seeded on first start-up).

* hidden from every users listing (``/api/users/``, Django admin, owner
  pickers), for everyone, including other superusers;
* immune to every mutation through the policy engine: update, delete,
  privilege or parent changes, disabling 2FA.

The account itself still signs in and manages its own profile, password and
2FA through ``/api/profile/`` and ``/api/profile/totp/``.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.db.models import QuerySet


def is_protected(user: User | int | None) -> bool:
    from paperless.models import ProtectedAccount

    user_id = getattr(user, "pk", user)
    if user_id is None:
        return False
    return ProtectedAccount.objects.filter(user_id=user_id).exists()


def protected_user_ids() -> list[int]:
    from paperless.models import ProtectedAccount

    return list(ProtectedAccount.objects.values_list("user_id", flat=True))


def exclude_protected(queryset: QuerySet[User]) -> QuerySet[User]:
    return queryset.exclude(protected_account__isnull=False)


def protect_account(user: User, reason: str = "seed_admin") -> bool:
    """Idempotent. Returns True if the account was newly protected."""
    from paperless.models import ProtectedAccount

    _, created = ProtectedAccount.objects.get_or_create(
        user=user,
        defaults={"reason": reason},
    )
    return created


def protect_seed_admin(username: str | None = None) -> bool:
    """
    Protect the seeded admin (``PAPERLESS_ADMIN_USER``, default "admin") if
    it exists and is a superuser. Called by every seed path on start-up.
    """
    from django.contrib.auth.models import User

    username = username or os.getenv("PAPERLESS_ADMIN_USER", "admin")
    user = User.objects.filter(username=username, is_superuser=True).first()
    if user is None:
        return False
    protect_account(user, "seed_admin")
    return True
