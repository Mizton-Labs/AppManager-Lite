"""Administrator settings routes (reverse-proxy configuration).

All routes require an admin session. The SSH key itself is never stored or
returned here -- only the *path* to a key file on the server.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, repository
from ..deps import get_db, require_admin, verify_csrf
from ..repository import TeamConflictError
from ..schemas import (
    BrandingSettingsOut,
    CreateTeamRequest,
    MessageOut,
    ReorderTeamsRequest,
    ReverseProxySettingsOut,
    TeamOut,
    UpdateBrandingSettingsRequest,
    UpdateReverseProxySettingsRequest,
    UpdateTeamRequest,
)

router = APIRouter(tags=["settings"])


def _settings_out(row: dict[str, Any]) -> ReverseProxySettingsOut:
    return ReverseProxySettingsOut(
        nginx_host=row.get("nginx_host", ""),
        nginx_user=row.get("nginx_user", ""),
        nginx_conf_path=row.get("nginx_conf_path", ""),
        ssh_key_path=row.get("ssh_key_path", ""),
        alias_template=row.get("alias_template", ""),
    )


def _branding_out(row: dict[str, Any]) -> BrandingSettingsOut:
    return BrandingSettingsOut(
        app_name=row.get("app_name", ""),
        app_logo=row.get("app_logo", ""),
        configured=bool(row.get("configured", 0)),
    )


@router.get("/settings/reverse-proxy", response_model=ReverseProxySettingsOut)
def get_reverse_proxy_settings(
    _actor: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ReverseProxySettingsOut:
    return _settings_out(repository.get_settings_row(conn))


@router.patch("/settings/reverse-proxy", response_model=ReverseProxySettingsOut)
def update_reverse_proxy_settings(
    payload: UpdateReverseProxySettingsRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ReverseProxySettingsOut:
    row = repository.update_settings_row(
        conn,
        nginx_host=payload.nginx_host,
        nginx_user=payload.nginx_user,
        nginx_conf_path=payload.nginx_conf_path,
        ssh_key_path=payload.ssh_key_path,
        alias_template=payload.alias_template,
    )
    # Record which fields changed -- never the key path contents beyond a flag.
    changed = [
        name
        for name in (
            "nginx_host",
            "nginx_user",
            "nginx_conf_path",
            "ssh_key_path",
            "alias_template",
        )
        if getattr(payload, name) is not None
    ]
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="settings_update",
        actor=actor,
        detail=f"reverse_proxy fields={','.join(changed) or 'none'}",
    )
    return _settings_out(row)


@router.get("/settings/branding", response_model=BrandingSettingsOut)
def get_branding_settings(
    _actor: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> BrandingSettingsOut:
    return _branding_out(repository.get_settings_row(conn))


@router.patch("/settings/branding", response_model=BrandingSettingsOut)
def update_branding_settings(
    payload: UpdateBrandingSettingsRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> BrandingSettingsOut:
    row = repository.update_settings_row(
        conn,
        app_name=payload.app_name,
        app_logo=payload.app_logo,
        configured=payload.configured,
    )
    changed = [
        name
        for name in ("app_name", "app_logo", "configured")
        if getattr(payload, name) is not None
    ]
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="settings_update",
        actor=actor,
        detail=f"branding fields={','.join(changed) or 'none'}",
    )
    return _branding_out(row)


# --- Team management (administrator-managed) --------------------------------


@router.get("/settings/teams", response_model=list[TeamOut])
def list_teams_admin(
    _actor: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[TeamOut]:
    return [TeamOut(**team) for team in repository.list_teams(conn)]


@router.post(
    "/settings/teams",
    response_model=TeamOut,
    status_code=status.HTTP_201_CREATED,
)
def create_team(
    payload: CreateTeamRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> TeamOut:
    try:
        team = repository.create_team(
            conn, name=payload.name, icon=payload.icon
        )
    except TeamConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="team_create",
        actor=actor,
        target_type="team",
        target_id=team["id"],
        target_name=team["name"],
    )
    return TeamOut(**team)


@router.patch("/settings/teams/{team_id}", response_model=TeamOut)
def update_team(
    team_id: int,
    payload: UpdateTeamRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> TeamOut:
    try:
        team = repository.update_team(
            conn, team_id, name=payload.name, icon=payload.icon
        )
    except TeamConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )
    changed = [
        name for name in ("name", "icon") if getattr(payload, name) is not None
    ]
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="team_update",
        actor=actor,
        target_type="team",
        target_id=team["id"],
        target_name=team["name"],
        detail=f"fields={','.join(changed) or 'none'}",
    )
    return TeamOut(**team)


@router.delete("/settings/teams/{team_id}", response_model=MessageOut)
def delete_team(
    team_id: int,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    existing = repository.get_team(conn, team_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Team not found"
        )
    repository.delete_team(conn, team_id)
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="team_delete",
        actor=actor,
        target_type="team",
        target_id=existing["id"],
        target_name=existing["name"],
    )
    return MessageOut(detail="Team deleted")


@router.post("/settings/teams/reorder", response_model=list[TeamOut])
def reorder_teams(
    payload: ReorderTeamsRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[TeamOut]:
    try:
        teams = repository.reorder_teams(conn, payload.team_ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="team_reorder",
        actor=actor,
        detail=f"count={len(teams)}",
    )
    return [TeamOut(**team) for team in teams]
