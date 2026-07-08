"""Administrator server-provisioning routes (issue_015).

Provider (Proxmox) configuration, provisioning policy, and the registry of
server templates used to create user servers. The Proxmox API key is
write-only: it is stored when provided and never returned, logged, or
audited.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, proxmox, repository
from ..deps import get_db, require_admin, verify_csrf
from ..schemas import (
    CreateServerTemplateRequest,
    MessageOut,
    ProviderTemplatesOut,
    ProvisioningSettingsOut,
    ServerTemplateOut,
    UpdateProvisioningSettingsRequest,
    UpdateServerTemplateRequest,
)

router = APIRouter(tags=["provisioning"])

logger = logging.getLogger(__name__)

# Fields whose *names* may appear in audit details. The API key is
# deliberately reported as a presence change only.
_PROVIDER_FIELDS = (
    "provider_type",
    "proxmox_url",
    "proxmox_token_name",
    "proxmox_template_filter",
    "proxmox_templates_only",
    "proxmox_verify_tls",
)
_POLICY_FIELDS = (
    "provisioning_self_service",
    "provisioning_max_servers",
    "provisioning_allow_resource_edit",
    "provisioning_max_cpus",
    "provisioning_max_memory_gb",
    "provisioning_max_disk_gb",
)


def _provisioning_out(row: dict[str, Any]) -> ProvisioningSettingsOut:
    return ProvisioningSettingsOut(
        provider_type=row.get("provider_type", "") or "",
        proxmox_url=row.get("proxmox_url", "") or "",
        proxmox_token_name=row.get("proxmox_token_name", "") or "",
        proxmox_api_key_set=bool(row.get("proxmox_api_key", "")),
        proxmox_template_filter=row.get("proxmox_template_filter", "") or "",
        proxmox_templates_only=bool(row.get("proxmox_templates_only", 1)),
        proxmox_verify_tls=bool(row.get("proxmox_verify_tls", 1)),
        proxmox_conn_status=row.get("proxmox_conn_status", "") or "",
        proxmox_conn_log=row.get("proxmox_conn_log", "") or "",
        provisioning_self_service=bool(row.get("provisioning_self_service", 0)),
        provisioning_max_servers=int(row.get("provisioning_max_servers", 3)),
        provisioning_allow_resource_edit=bool(
            row.get("provisioning_allow_resource_edit", 0)
        ),
        provisioning_max_cpus=int(row.get("provisioning_max_cpus", 12)),
        provisioning_max_memory_gb=int(row.get("provisioning_max_memory_gb", 24)),
        provisioning_max_disk_gb=int(row.get("provisioning_max_disk_gb", 200)),
    )


def _provider_config(row: dict[str, Any]) -> dict[str, Any]:
    """Provider connection settings in the shape app.proxmox expects."""
    return {
        "proxmox_url": row.get("proxmox_url", ""),
        "proxmox_token_name": row.get("proxmox_token_name", ""),
        "proxmox_api_key": row.get("proxmox_api_key", ""),
        "proxmox_verify_tls": bool(row.get("proxmox_verify_tls", 1)),
        "proxmox_template_filter": row.get("proxmox_template_filter", ""),
        "proxmox_templates_only": bool(row.get("proxmox_templates_only", 1)),
    }


def _provider_configured(row: dict[str, Any]) -> bool:
    return bool(
        row.get("proxmox_url")
        and row.get("proxmox_token_name")
        and row.get("proxmox_api_key")
    )


@router.get("/settings/provisioning", response_model=ProvisioningSettingsOut)
def get_provisioning_settings(
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ProvisioningSettingsOut:
    return _provisioning_out(repository.get_settings_row(conn))


@router.patch("/settings/provisioning", response_model=ProvisioningSettingsOut)
def update_provisioning_settings(
    payload: UpdateProvisioningSettingsRequest,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ProvisioningSettingsOut:
    # Explicit JSON nulls are "unset" for our purposes: they neither change
    # columns nor count as touched fields (avoids fake audit entries and
    # needless connection tests).
    changes = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if v is not None
    }
    row = repository.update_provisioning_settings(conn, **changes)

    # When provider connection settings are touched (and complete), run a
    # connection test and persist the outcome so the UI can show it.
    provider_touched = any(
        f in changes for f in (*_PROVIDER_FIELDS, "proxmox_api_key")
    )
    if provider_touched:
        if _provider_configured(row):
            result = proxmox.test_connection(_provider_config(row))
            row = repository.update_provisioning_settings(
                conn,
                proxmox_conn_status=result.status,
                proxmox_conn_log=result.transcript,
            )
        else:
            row = repository.update_provisioning_settings(
                conn, proxmox_conn_status="", proxmox_conn_log=""
            )

    changed_names = [k for k in changes if k != "proxmox_api_key"]
    if "proxmox_api_key" in changes:
        changed_names.append("proxmox_api_key(updated)")
    logger.info(
        "Provisioning settings updated fields=%s by=%r",
        changed_names,
        admin.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="settings_update",
        actor=admin,
        target_type="settings",
        target_name="provisioning",
        detail=f"fields={changed_names}",
    )
    return _provisioning_out(row)


@router.get(
    "/settings/provisioning/provider-templates",
    response_model=ProviderTemplatesOut,
)
def list_provider_templates(
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> ProviderTemplatesOut:
    """Live template/VM list from the provider (for the verification dropdown)."""
    row = repository.get_settings_row(conn)
    if not _provider_configured(row):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The LXC/VM provider is not configured yet",
        )
    result = proxmox.list_templates(_provider_config(row))
    return ProviderTemplatesOut(
        status=result.status,
        log=result.transcript,
        templates=result.data or [],
    )


# ---------------------------------------------------------------------------
# Server templates
# ---------------------------------------------------------------------------


@router.get("/settings/server-templates", response_model=list[ServerTemplateOut])
def list_server_templates(
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ServerTemplateOut]:
    return [
        ServerTemplateOut(**t) for t in repository.list_server_templates(conn)
    ]


@router.post(
    "/settings/server-templates",
    response_model=ServerTemplateOut,
    status_code=201,
)
def create_server_template(
    payload: CreateServerTemplateRequest,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ServerTemplateOut:
    try:
        template = repository.create_server_template(
            conn,
            vmid=payload.vmid,
            name=payload.name,
            kind=payload.kind,
            admin_ssh_key_path=payload.admin_ssh_key_path,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="server_template_create",
        actor=admin,
        target_type="server_template",
        target_id=template["id"],
        target_name=template["name"],
        detail=f"vmid={template['vmid']} kind={template['kind']}",
    )
    return ServerTemplateOut(**template)


@router.patch(
    "/settings/server-templates/{template_id}",
    response_model=ServerTemplateOut,
)
def update_server_template(
    template_id: int,
    payload: UpdateServerTemplateRequest,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> ServerTemplateOut:
    try:
        template = repository.update_server_template(
            conn,
            template_id,
            vmid=payload.vmid,
            name=payload.name,
            kind=payload.kind,
            admin_ssh_key_path=payload.admin_ssh_key_path,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    if template is None:
        raise HTTPException(status_code=404, detail="Server template not found")
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="server_template_update",
        actor=admin,
        target_type="server_template",
        target_id=template_id,
        target_name=template["name"],
    )
    return ServerTemplateOut(**template)


@router.delete(
    "/settings/server-templates/{template_id}", response_model=MessageOut
)
def delete_server_template(
    template_id: int,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    template = repository.get_server_template(conn, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Server template not found")
    repository.delete_server_template(conn, template_id)
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="server_template_delete",
        actor=admin,
        target_type="server_template",
        target_id=template_id,
        target_name=template["name"],
    )
    return MessageOut(detail="Server template deleted")
