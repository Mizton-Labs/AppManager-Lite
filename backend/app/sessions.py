"""Server-side session store backed by SQLite.

Sessions are opaque random tokens. Each session carries a CSRF token that the
frontend must echo in the ``X-CSRF-Token`` header on state-changing requests.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import security
from .config import get_settings

SESSION_COOKIE_NAME = "app_session"
CSRF_HEADER_NAME = "X-CSRF-Token"
AUTH_METHODS = {"local", "oidc", "saml"}


def create_session(
    conn: sqlite3.Connection, user_id: int, *, auth_method: str = "local"
) -> dict[str, str]:
    if auth_method not in AUTH_METHODS:
        raise ValueError("Unknown session authentication method.")
    settings = get_settings()
    session_id = security.generate_token()
    csrf_token = security.generate_token()
    conn.execute(
        """
        INSERT INTO sessions (id, user_id, csrf_token, auth_method, expires_at)
        VALUES (?, ?, ?, ?, datetime('now', ?))
        """,
        (
            session_id,
            user_id,
            csrf_token,
            auth_method,
            f"+{settings.session_ttl_seconds} seconds",
        ),
    )
    return {"session_id": session_id, "csrf_token": csrf_token}


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT s.id, s.user_id, s.csrf_token, s.auth_method, s.expires_at
        FROM sessions s
        WHERE s.id = ? AND s.expires_at > datetime('now')
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "session_id": row["id"],
        "user_id": row["user_id"],
        "csrf_token": row["csrf_token"],
        "auth_method": row["auth_method"],
    }


def delete_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def delete_user_sessions(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_expired(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
