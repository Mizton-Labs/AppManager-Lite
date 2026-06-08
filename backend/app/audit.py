"""Audit trail helper.

Records user- and system-driven actions to the ``audit_log`` table so an
administrator can review them in the UI, grouped by category. This is the
queryable source of truth for the audit view; the text log (``logs/app.log``)
is retained for operations/debugging.

Secrets (passwords, generated passwords, password hashes, session identifiers,
CSRF tokens) must never be passed to ``record`` -- the same contract as the
application's logging.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import repository

logger = logging.getLogger(__name__)

# Categories map 1:1 to the audit-view tabs.
CATEGORY_APPLICATION = "application"
CATEGORY_USER = "user"
CATEGORY_SYSTEM = "system"
CATEGORIES = (CATEGORY_APPLICATION, CATEGORY_USER, CATEGORY_SYSTEM)


def record(
    conn: sqlite3.Connection,
    *,
    category: str,
    action: str,
    actor: dict[str, Any] | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    target_name: str | None = None,
    detail: str = "",
) -> None:
    """Write one audit row. Never raises: an audit failure must not break the
    user action that triggered it (the error is logged and swallowed).

    ``actor`` is the caller dict (as returned by ``get_current_user``); its
    ``id`` and ``username`` are recorded. The auth-disabled synthetic identity
    has id 0, which is stored as a null actor id.
    """
    actor_id: int | None = None
    actor_username: str | None = None
    if actor is not None:
        raw_id = actor.get("id")
        actor_id = raw_id if raw_id else None
        actor_username = actor.get("username")

    try:
        repository.insert_audit_event(
            conn,
            category=category,
            action=action,
            actor_id=actor_id,
            actor_username=actor_username,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            detail=detail,
        )
    except Exception:  # pragma: no cover - defensive; auditing is best-effort
        logger.exception(
            "Failed to record audit event category=%s action=%s",
            category,
            action,
        )
