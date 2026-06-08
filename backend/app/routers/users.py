"""Administrator user-management routes.

All routes require an admin session (or run open when authentication is
disabled). Guardrails prevent removing the last active administrator.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, repository, security, sessions
from ..deps import get_db, require_admin, verify_csrf
from ..schemas import (
    CreateUserRequest,
    GeneratedPasswordOut,
    MessageOut,
    UpdateUserRequest,
    UserOut,
)

router = APIRouter(tags=["users"])

logger = logging.getLogger(__name__)


def _user_out(user: dict[str, Any]) -> UserOut:
    return UserOut(
        id=user["id"],
        username=user["username"],
        role=user["role"],
        is_active=user["is_active"],
        must_change_password=user["must_change_password"],
        self_service=user["self_service"],
        apps_server=user["apps_server"],
        teams=user["teams"],
    )


def _validate_teams(conn: sqlite3.Connection, teams: list[str]) -> None:
    known = set(repository.list_team_names(conn))
    unknown = [t for t in teams if t not in known]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown team(s): {', '.join(unknown)}",
        )


def _guard_last_admin(
    conn: sqlite3.Connection, target: dict[str, Any], *, removing: bool
) -> None:
    """Block changes that would remove the final active administrator."""
    if target["role"] != "admin":
        return
    active_admins = repository.count_admins(conn, active_only=True)
    target_is_active_admin = target["is_active"]
    if removing and target_is_active_admin and active_admins <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the last active administrator",
        )


@router.get("/teams", response_model=list[str])
def list_teams(
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[str]:
    return repository.list_team_names(conn)


@router.get("/users", response_model=list[UserOut])
def list_users(
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[UserOut]:
    return [_user_out(u) for u in repository.list_users(conn)]


@router.post("/users", response_model=GeneratedPasswordOut, status_code=201)
def create_user(
    payload: CreateUserRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> GeneratedPasswordOut:
    _validate_teams(conn, payload.teams)
    password = security.generate_password()
    try:
        user = repository.create_user(
            conn,
            username=payload.username,
            password=password,
            role=payload.role,
            teams=payload.teams,
            must_change_password=True,
            self_service=payload.self_service,
            apps_server=payload.apps_server,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    logger.info(
        "User created id=%s username=%r role=%s teams=%s self_service=%s by=%r",
        user["id"],
        user["username"],
        user["role"],
        payload.teams,
        user["self_service"],
        actor.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="create",
        actor=actor,
        target_type="user",
        target_id=user["id"],
        target_name=user["username"],
        detail=f"role={user['role']} teams={payload.teams} "
        f"self_service={user['self_service']} apps_server={user['apps_server']!r}",
    )
    return GeneratedPasswordOut(user=_user_out(user), password=password)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> UserOut:
    target = repository.get_user_by_id(conn, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.teams is not None:
        _validate_teams(conn, payload.teams)

    # Determine whether this change removes the last active admin.
    demoting = payload.role is not None and payload.role != "admin"
    disabling = payload.is_active is False
    if (demoting or disabling) and target["role"] == "admin":
        active_admins = repository.count_admins(conn, active_only=True)
        if target["is_active"] and active_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last active administrator",
            )

    updated = repository.update_user(
        conn,
        user_id,
        role=payload.role,
        teams=payload.teams,
        is_active=payload.is_active,
        self_service=payload.self_service,
        apps_server=payload.apps_server,
    )
    assert updated is not None
    if payload.is_active is False:
        sessions.delete_user_sessions(conn, user_id)
    logger.info(
        "User updated id=%s username=%r role=%s is_active=%s teams=%s "
        "self_service=%s by=%r",
        user_id,
        target["username"],
        payload.role,
        payload.is_active,
        payload.teams,
        payload.self_service,
        admin.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="update",
        actor=admin,
        target_type="user",
        target_id=user_id,
        target_name=target["username"],
        detail=f"role={payload.role} is_active={payload.is_active} "
        f"teams={payload.teams} self_service={payload.self_service}",
    )
    return _user_out(updated)


@router.post("/users/{user_id}/reset-password", response_model=GeneratedPasswordOut)
def reset_user_password(
    user_id: int,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> GeneratedPasswordOut:
    target = repository.get_user_by_id(conn, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    password = security.generate_password()
    repository.set_password(conn, user_id, password, must_change_password=True)
    sessions.delete_user_sessions(conn, user_id)
    refreshed = repository.get_user_by_id(conn, user_id)
    assert refreshed is not None
    logger.info(
        "Password reset id=%s username=%r by=%r",
        user_id,
        target["username"],
        actor.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="password_reset",
        actor=actor,
        target_type="user",
        target_id=user_id,
        target_name=target["username"],
    )
    return GeneratedPasswordOut(user=_user_out(refreshed), password=password)


@router.delete("/users/{user_id}", response_model=MessageOut)
def delete_user(
    user_id: int,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    target = repository.get_user_by_id(conn, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if admin.get("id") == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )
    _guard_last_admin(conn, target, removing=True)
    repository.delete_user(conn, user_id)
    logger.warning(
        "User deleted id=%s username=%r by=%r",
        user_id,
        target["username"],
        admin.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="delete",
        actor=admin,
        target_type="user",
        target_id=user_id,
        target_name=target["username"],
    )
    return MessageOut(detail="User deleted")
