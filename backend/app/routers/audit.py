"""Administrator audit-log routes.

Read-only access to the ``audit_log`` table for administrators, grouped by
category (application / user / system). Listing is admin-only.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, repository
from ..deps import get_db, require_admin
from ..schemas import AuditEntryOut

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
