"""FastAPI dependencies: database connection, authentication, CSRF, roles."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from . import repository, sessions
from .config import get_settings
from .db import connect

logger = logging.getLogger(__name__)

# Synthetic identity used only when authentication is disabled
# (APP_ENABLE_AUTH=0). It is never persisted.
_ANONYMOUS_ADMIN: dict[str, Any] = {
    "id": 0,
    "username": "local",
    "user_id": "local",
    "role": "admin",
    "is_active": True,
    "must_change_password": False,
    "self_service": True,
    "teams": [],
}


def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_current_user(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.enable_auth:
        return dict(_ANONYMOUS_ADMIN)

    session_id = request.cookies.get(sessions.SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    session = sessions.get_session(conn, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired"
        )
    user = repository.get_user_by_id(conn, session["user_id"])
    if user is None or not user["is_active"]:
        sessions.delete_session(conn, session_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable"
        )
    user["_session"] = session
    return user


def require_admin(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    if user["role"] != "admin":
        logger.warning(
            "Administrator access denied username=%r role=%s",
            user.get("username"),
            user.get("role"),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return user


def verify_csrf(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> None:
    """Enforce CSRF on state-changing requests when auth is enabled."""
    settings = get_settings()
    if not settings.enable_auth:
        return
    session = user.get("_session")
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    header = request.headers.get(sessions.CSRF_HEADER_NAME, "")
    if not header or not secrets.compare_digest(header, session["csrf_token"]):
        logger.warning(
            "CSRF validation failed username=%r path=%s",
            user.get("username"),
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token"
        )
