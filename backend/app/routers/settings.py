"""Administrator settings routes (reverse-proxy configuration).

All routes require an admin session. The SSH key itself is never stored or
returned here -- only the *path* to a key file on the server.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends

from .. import audit, repository
from ..deps import get_db, require_admin, verify_csrf
from ..schemas import (
    BrandingSettingsOut,
    ReverseProxySettingsOut,
    UpdateBrandingSettingsRequest,
    UpdateReverseProxySettingsRequest,
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
