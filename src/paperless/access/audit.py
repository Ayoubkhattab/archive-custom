"""
Audit service: append-only security audit trail.

Every sensitive change (privileges, relationships, account deletion, image
upload/delete, denied attempts on protected accounts) is written here. Rows
can be inserted but never updated or deleted, through the ORM (model guard)
or, on PostgreSQL, through SQL (database trigger, see migration 0008).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("paperless.audit")

_SECRET_KEYS = {"password", "token", "secret", "api_key", "llm_api_key", "code"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if str(k).lower() in _SECRET_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_redact(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _client_ip(request) -> str | None:
    if request is None:
        return None
    # REMOTE_ADDR only: forwarded headers are client controlled unless a
    # trusted proxy rewrote them, and this column must not be spoofable.
    return request.META.get("REMOTE_ADDR")


def record(
    *,
    actor,
    action: str,
    target_type: str,
    target_id: Any = None,
    outcome: str = "success",
    before: dict | None = None,
    after: dict | None = None,
    request=None,
) -> None:
    """
    Write one audit entry. Called from the service/view layer (never from
    signals), inside the same transaction as the change it describes, so a
    failed audit write rolls the change back.
    """
    from paperless.models import SecurityAuditLog

    meta = request.META if request is not None else {}
    SecurityAuditLog.objects.create(
        actor_id=getattr(actor, "pk", None),
        actor_username=(getattr(actor, "username", "") or "")[:150],
        action=action[:64],
        target_type=target_type[:32],
        target_id=str(target_id)[:64] if target_id is not None else "",
        outcome=outcome[:16],
        before_json=_redact(before or {}),
        after_json=_redact(after or {}),
        ip=_client_ip(request),
        user_agent=meta.get("HTTP_USER_AGENT", "")[:255],
        request_id=meta.get("HTTP_X_REQUEST_ID", "")[:64],
    )
