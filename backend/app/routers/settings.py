"""Administrator settings routes (reverse-proxy configuration).

All routes require an admin session. The SSH key itself is never stored or
returned here -- only the *path* to a key file on the server.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, repository, reverse_proxy
from ..deps import get_db, require_admin, verify_csrf
from ..repository import TeamConflictError
from ..schemas import (
    BrandingSettingsOut,
    BundleTemplateOut,
    CloneBundleTemplateRequest,
    CreateBundleTemplateRequest,
    CreateTeamRequest,
    MessageOut,
    ReorderTeamsRequest,
    ReverseProxySettingsOut,
    SetBundleTemplateEnabledRequest,
    TeamOut,
    UpdateBundleTemplateRequest,
    UpdateBrandingSettingsRequest,
    UpdateReverseProxySettingsRequest,
    UpdateTeamRequest,
)

router = APIRouter(tags=["settings"])


def _settings_out(
    row: dict[str, Any], protected_alias_result: reverse_proxy.PushResult | None = None
) -> ReverseProxySettingsOut:
    return ReverseProxySettingsOut(
        nginx_host=row.get("nginx_host", ""),
        nginx_user=row.get("nginx_user", ""),
        nginx_conf_path=row.get("nginx_conf_path", ""),
        ssh_key_path=row.get("ssh_key_path", ""),
        reverse_proxy_ssh_key_id=row.get("reverse_proxy_ssh_key_id"),
        appmanager_proxy_host=row.get("appmanager_proxy_host", ""),
        appmanager_proxy_port=row.get("appmanager_proxy_port", ""),
        alias_template=row.get("alias_template", ""),
        protected_alias_auth_status=(
            "" if protected_alias_result is None else protected_alias_result.status
        ),
        protected_alias_auth_log=(
            "" if protected_alias_result is None else protected_alias_result.transcript
        ),
    )


def _branding_out(row: dict[str, Any]) -> BrandingSettingsOut:
    return BrandingSettingsOut(
        app_name=row.get("app_name", ""),
        app_logo=row.get("app_logo", ""),
        collaborators=repository.parse_collaborators(
            row.get("collaborators", "[]")
        ),
        configured=bool(row.get("configured", 0)),
    )


def _bundle_out(template: dict[str, Any]) -> BundleTemplateOut:
    return BundleTemplateOut(**template)


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
    if payload.reverse_proxy_ssh_key_id is not None and (
        repository.get_ssh_key(conn, payload.reverse_proxy_ssh_key_id) is None
    ):
        raise HTTPException(status_code=400, detail="Unknown SSH key")
    row = repository.update_settings_row(
        conn,
        nginx_host=payload.nginx_host,
        nginx_user=payload.nginx_user,
        nginx_conf_path=payload.nginx_conf_path,
        ssh_key_path=payload.ssh_key_path,
        reverse_proxy_ssh_key_id=payload.reverse_proxy_ssh_key_id,
        appmanager_proxy_host=payload.appmanager_proxy_host,
        appmanager_proxy_port=payload.appmanager_proxy_port,
        alias_template=payload.alias_template,
    )
    # Resolve the effective key path on a copy so the response still reflects
    # the stored raw path / selected key id.
    op_row = dict(row)
    op_row["ssh_key_path"] = repository.reverse_proxy_key_path(conn, row)
    protected_alias_result = reverse_proxy.ensure_proxy_auth_config(op_row)
    # Record which fields changed -- never the key path contents beyond a flag.
    changed = [
        name
        for name in (
            "nginx_host",
            "nginx_user",
            "nginx_conf_path",
            "ssh_key_path",
            "reverse_proxy_ssh_key_id",
            "appmanager_proxy_host",
            "appmanager_proxy_port",
            "alias_template",
        )
        if getattr(payload, name) is not None
    ]
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="settings_update",
        actor=actor,
        detail=(
            f"reverse_proxy fields={','.join(changed) or 'none'} "
            f"protected_alias_auth={protected_alias_result.status}"
        ),
    )
    return _settings_out(row, protected_alias_result)


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
        collaborators=(
            None
            if payload.collaborators is None
            else json.dumps(payload.collaborators)
        ),
        configured=payload.configured,
    )
    changed = [
        name
        for name in ("app_name", "app_logo", "collaborators", "configured")
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


# --- Bundle templates (administrator-managed) ------------------------------


@router.get("/settings/bundle-templates", response_model=list[BundleTemplateOut])
def list_bundle_templates(
    _actor: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[BundleTemplateOut]:
    return [_bundle_out(t) for t in repository.list_bundle_templates(conn)]


@router.post(
    "/settings/bundle-templates",
    response_model=BundleTemplateOut,
    status_code=status.HTTP_201_CREATED,
)
def create_bundle_template(
    payload: CreateBundleTemplateRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> BundleTemplateOut:
    try:
        template = repository.create_bundle_template(
            conn,
            name=payload.name,
            content=payload.content,
            mappings=[m.model_dump() for m in payload.mappings],
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A bundle template with that name already exists",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="bundle_template_create",
        actor=actor,
        target_type="bundle_template",
        target_id=template["id"],
        target_name=template["name"],
    )
    return _bundle_out(template)


@router.patch(
    "/settings/bundle-templates/{template_id}", response_model=BundleTemplateOut
)
def update_bundle_template(
    template_id: int,
    payload: UpdateBundleTemplateRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> BundleTemplateOut:
    existing = repository.get_bundle_template(conn, template_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Bundle template not found")
    if existing["is_builtin"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Built-in templates cannot be edited; clone it first.",
        )
    try:
        template = repository.update_bundle_template(
            conn,
            template_id,
            name=payload.name,
            content=payload.content,
            mappings=(None if payload.mappings is None else [m.model_dump() for m in payload.mappings]),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A bundle template with that name already exists",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if template is None:
        raise HTTPException(status_code=404, detail="Bundle template not found")
    changed = [
        name for name in ("name", "content", "mappings") if getattr(payload, name) is not None
    ]
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="bundle_template_update",
        actor=actor,
        target_type="bundle_template",
        target_id=template["id"],
        target_name=template["name"],
        detail=f"fields={','.join(changed) or 'none'}",
    )
    return _bundle_out(template)


@router.delete("/settings/bundle-templates/{template_id}", response_model=MessageOut)
def delete_bundle_template(
    template_id: int,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    existing = repository.get_bundle_template(conn, template_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Bundle template not found")
    if existing["is_builtin"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Built-in templates cannot be deleted; disable it instead.",
        )
    repository.delete_bundle_template(conn, template_id)
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="bundle_template_delete",
        actor=actor,
        target_type="bundle_template",
        target_id=existing["id"],
        target_name=existing["name"],
    )
    return MessageOut(detail="Bundle template deleted")


@router.post(
    "/settings/bundle-templates/{template_id}/clone",
    response_model=BundleTemplateOut,
    status_code=status.HTTP_201_CREATED,
)
def clone_bundle_template(
    template_id: int,
    payload: CloneBundleTemplateRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> BundleTemplateOut:
    if repository.get_bundle_template(conn, template_id) is None:
        raise HTTPException(status_code=404, detail="Bundle template not found")
    try:
        clone = repository.clone_bundle_template(
            conn, template_id, new_name=payload.name
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A bundle template with that name already exists",
        ) from exc
    assert clone is not None
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="bundle_template_clone",
        actor=actor,
        target_type="bundle_template",
        target_id=clone["id"],
        target_name=clone["name"],
        detail=f"cloned_from={template_id}",
    )
    return _bundle_out(clone)


@router.patch(
    "/settings/bundle-templates/{template_id}/enabled",
    response_model=BundleTemplateOut,
)
def set_bundle_template_enabled(
    template_id: int,
    payload: SetBundleTemplateEnabledRequest,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> BundleTemplateOut:
    template = repository.set_bundle_template_enabled(
        conn, template_id, payload.enabled
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Bundle template not found")
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="bundle_template_enable",
        actor=actor,
        target_type="bundle_template",
        target_id=template["id"],
        target_name=template["name"],
        detail=f"enabled={payload.enabled}",
    )
    return _bundle_out(template)


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
