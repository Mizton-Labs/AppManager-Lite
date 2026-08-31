"""Administrator audit-log routes.

Read-only access to the ``audit_log`` table for administrators, grouped by
category (application / user / system). Listing is admin-only.

Also includes navigation-activity recording/listing (issue_local_032): a
separate, privacy-conscious log of which top-level sections/tabs a signed-in
user visits, kept apart from the security/administrative ``audit_log`` above.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, repository
from ..deps import get_current_user, get_db, require_admin, verify_csrf
from ..schemas import (
    AuditEntryOut,
    NavigationActivityOut,
    NavigationActivityPageOut,
    RecordNavigationRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["audit"])

# Cap how many rows a single request returns (newest first).
_DEFAULT_LIMIT = 200


@router.get("/audit", response_model=list[AuditEntryOut])
def list_audit(
    category: str | None = Query(default=None),
    _actor: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[AuditEntryOut]:
    if category is not None and category not in audit.CATEGORIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"category must be one of {audit.CATEGORIES}",
        )
    rows = repository.list_audit_events(
        conn, category=category, limit=_DEFAULT_LIMIT
    )
    return [AuditEntryOut(**row) for row in rows]


@router.post(
    "/audit/navigation", status_code=status.HTTP_204_NO_CONTENT
)
def record_navigation(
    payload: RecordNavigationRequest,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """Record one navigation event for the calling (session-derived) user.

    Best-effort: any recording failure is logged and swallowed here rather
    than surfaced as an error, since a navigation-tracking hiccup must never
    disrupt the page the user is actually trying to use. The destination is
    validated against a fixed allowlist (see
    ``repository.NAVIGATION_DESTINATIONS``); an unknown value is rejected with
    422 by request-body validation before this handler even runs (the
    frontend only ever sends allowlisted values, so this should never fire in
    practice) -- there is no way for the actor identity itself to be spoofed,
    since it always comes from the authenticated session, never the request
    body.
    """
    if actor.get("id") in (None, 0):
        # The synthetic auth-disabled identity (id 0) has no real account to
        # attribute activity to; silently skip rather than violate the
        # actor_id foreign key.
        return
    try:
        repository.record_navigation_activity(
            conn,
            actor_id=actor["id"],
            actor_username=actor.get("username", ""),
            destination=payload.destination,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Could not record navigation activity for actor id=%s", actor.get("id")
        )


@router.get("/audit/navigation", response_model=NavigationActivityPageOut)
def list_navigation(
    offset: int = Query(default=0, ge=0, le=450),
    limit: int = Query(default=50, ge=1, le=50),
    _actor: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> NavigationActivityPageOut:
    """A bounded page of navigation activity: at most 50 rows per page, over
    only the newest 500 stored rows (see repository.NAVIGATION_ACTIVITY_MAX_EVENTS)."""
    rows, total = repository.list_navigation_activity(conn, offset=offset, limit=limit)
    return NavigationActivityPageOut(
        items=[NavigationActivityOut(**row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
