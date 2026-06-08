"""Authentication and self-service account routes."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import audit, repository, security, sessions
from ..config import get_settings
from ..deps import get_current_user, get_db, verify_csrf
from ..schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MessageOut,
    SessionOut,
    UserOut,
)

router = APIRouter(tags=["auth"])

logger = logging.getLogger(__name__)


def _set_session_cookie(response: Response, session_id: str) -> None:
    settings = get_settings()
    path = settings.base_prefix + "/" if settings.base_prefix else "/"
    response.set_cookie(
        key=sessions.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path=path,
    )


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    path = settings.base_prefix + "/" if settings.base_prefix else "/"
    response.delete_cookie(
        key=sessions.SESSION_COOKIE_NAME,
        path=path,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
    )


def _user_out(user: dict[str, Any]) -> UserOut:
    return UserOut(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        is_active=user["is_active"],
        must_change_password=user["must_change_password"],
        self_service=user["self_service"],
        teams=user["teams"],
    )


def _branding(conn: sqlite3.Connection) -> dict[str, Any]:
    """Branding fields included in every session response (readable pre-auth)."""
    row = repository.get_settings_row(conn)
    return {
        "app_name": row.get("app_name", "") or "",
        "app_logo": row.get("app_logo", "") or "",
        "configured": bool(row.get("configured", 0)),
    }


@router.get("/session", response_model=SessionOut)
def read_session(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> SessionOut:
    settings = get_settings()
    branding = _branding(conn)
    if not settings.enable_auth:
        return SessionOut(authenticated=True, enable_auth=False, **branding)
    session_id = request.cookies.get(sessions.SESSION_COOKIE_NAME)
    if not session_id:
        return SessionOut(authenticated=False, enable_auth=True, **branding)
    session = sessions.get_session(conn, session_id)
    if session is None:
        return SessionOut(authenticated=False, enable_auth=True, **branding)
    user = repository.get_user_by_id(conn, session["user_id"])
    if user is None or not user["is_active"]:
        return SessionOut(authenticated=False, enable_auth=True, **branding)
    return SessionOut(
        authenticated=True,
        enable_auth=True,
        user=_user_out(user),
        csrf_token=session["csrf_token"],
        **branding,
    )


@router.post("/auth/login", response_model=SessionOut)
def login(
    payload: LoginRequest,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
) -> SessionOut:
    settings = get_settings()
    if not settings.enable_auth:
        return SessionOut(authenticated=True, enable_auth=False, **_branding(conn))

    row = repository.get_user_by_username(conn, payload.username)
    # Always perform a hash verification to reduce username enumeration via timing.
    stored_hash = row["password_hash"] if row else security.hash_password("x")
    valid = security.verify_password(stored_hash, payload.password)
    if row is None or not valid or not bool(row["is_active"]):
        if row is None:
            reason = "unknown_user"
        elif not valid:
            reason = "bad_password"
        else:
            reason = "inactive_account"
        logger.warning("Login failed for username=%r (%s)", payload.username, reason)
        audit.record(
            conn,
            category=audit.CATEGORY_USER,
            action="login_failed",
            target_type="user",
            target_name=payload.username,
            detail=f"reason={reason}",
        )
        # get_db rolls back on the exception below, so persist this security
        # event explicitly before raising.
        conn.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    created = sessions.create_session(conn, row["id"])
    _set_session_cookie(response, created["session_id"])
    user = repository.get_user_by_id(conn, row["id"])
    assert user is not None
    logger.info("Login succeeded for username=%r (id=%s)", row["username"], row["id"])
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="login",
        actor=user,
        target_type="user",
        target_id=row["id"],
        target_name=row["username"],
    )
    return SessionOut(
        authenticated=True,
        enable_auth=True,
        user=_user_out(user),
        csrf_token=created["csrf_token"],
        **_branding(conn),
    )


@router.post("/auth/logout", response_model=MessageOut)
def logout(
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    session_id = request.cookies.get(sessions.SESSION_COOKIE_NAME)
    if session_id:
        sessions.delete_session(conn, session_id)
        logger.info("Logout: session invalidated")
        audit.record(
            conn,
            category=audit.CATEGORY_USER,
            action="logout",
        )
    _clear_session_cookie(response)
    return MessageOut(detail="Signed out")


@router.post("/account/password", response_model=MessageOut)
def change_own_password(
    payload: ChangePasswordRequest,
    response: Response,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    settings = get_settings()
    if not settings.enable_auth:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentication is disabled",
        )
    row = repository.get_user_by_username(conn, user["username"])
    if row is None or not security.verify_password(
        row["password_hash"], payload.current_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    if payload.new_password != payload.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password and confirmation do not match",
        )
    errors = security.validate_password(payload.new_password)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors)
        )
    if security.verify_password(row["password_hash"], payload.new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current password",
        )
    repository.set_password(
        conn, row["id"], payload.new_password, must_change_password=False
    )
    # Invalidate all other sessions; keep the caller signed in with a fresh one.
    sessions.delete_user_sessions(conn, row["id"])
    created = sessions.create_session(conn, row["id"])
    _set_session_cookie(response, created["session_id"])
    logger.info("Password changed by user=%r (id=%s)", user["username"], row["id"])
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="password_change",
        actor=user,
        target_type="user",
        target_id=row["id"],
        target_name=user["username"],
    )
    return MessageOut(detail="Password updated")
