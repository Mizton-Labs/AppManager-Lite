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

from .. import (
    audit,
    jumpserver,
    keystore,
    proxmox,
    repository,
    servers,
    sshkeys,
)
from ..deps import get_current_user, get_db, require_admin, verify_csrf
from ..schemas import (
    CreateServerTemplateRequest,
    CreateSshKeyRequest,
    CreateUserServerRequest,
    JumpSyncEntry,
    JumpSyncOut,
    MessageOut,
    ProviderTemplatesOut,
    ProvisioningSettingsOut,
    ServerAccessOut,
    ServerTemplateOptionOut,
    ServerTemplateOut,
    SshKeyOut,
    UpdateProvisioningSettingsRequest,
    UpdateServerTemplateRequest,
    UpdateUserServerRequest,
    UserServerOut,
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
        jump_enabled=bool(row.get("jump_enabled", 0)),
        jump_host=row.get("jump_host", "") or "",
        jump_user=row.get("jump_user", "") or "",
        jump_ssh_key_id=row.get("jump_ssh_key_id"),
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
    if "jump_ssh_key_id" in changes and (
        repository.get_ssh_key(conn, changes["jump_ssh_key_id"]) is None
    ):
        raise HTTPException(status_code=400, detail="Unknown SSH key")

    # Enabling the jump server requires host, user, and key to be present in
    # the resulting state (reject "enabled but not configured").
    current = repository.get_settings_row(conn)
    effective_enabled = changes.get("jump_enabled", bool(current.get("jump_enabled", 0)))
    if effective_enabled:
        eff_host = changes.get("jump_host", current.get("jump_host", ""))
        eff_user = changes.get("jump_user", current.get("jump_user", ""))
        eff_key = changes.get("jump_ssh_key_id", current.get("jump_ssh_key_id"))
        if not (eff_host and eff_user and eff_key):
            raise HTTPException(
                status_code=400,
                detail="Enabling the jump server requires a host, user, and SSH key.",
            )
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
# Jump server: bulk onboarding of existing users
# ---------------------------------------------------------------------------


@router.post("/settings/jump-server/sync", response_model=JumpSyncOut)
def sync_jump_server_users(
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> JumpSyncOut:
    """Onboard every active user to the configured jump server (idempotent)."""
    config = jumpserver.load_config(conn)
    if not config.enabled:
        raise HTTPException(
            status_code=400, detail="The jump server is not enabled"
        )
    if not config.ready:
        raise HTTPException(
            status_code=400,
            detail="The jump server is enabled but not fully configured",
        )
    results: list[JumpSyncEntry] = []
    for user in repository.list_users(conn):
        if not user.get("is_active", True):
            continue
        status_str, detail = jumpserver.sync_user(conn, user)
        results.append(
            JumpSyncEntry(
                username=user["username"], status=status_str, detail=detail
            )
        )
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="jump_sync",
        actor=admin,
        target_type="jump_server",
        target_name=config.host,
        detail=f"users={len(results)}",
    )
    return JumpSyncOut(results=results)


# ---------------------------------------------------------------------------
# SSH key registry (Remote Access Config)
# ---------------------------------------------------------------------------


@router.get("/settings/ssh-keys", response_model=list[SshKeyOut])
def list_ssh_keys(
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[SshKeyOut]:
    return [SshKeyOut(**k) for k in repository.list_ssh_keys(conn)]


@router.post("/settings/ssh-keys", response_model=SshKeyOut, status_code=201)
def create_ssh_key(
    payload: CreateSshKeyRequest,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> SshKeyOut:
    public_key = ""
    fingerprint = ""
    enc = ""
    if payload.kind == "path":
        if not payload.path:
            raise HTTPException(
                status_code=400, detail="A key file path is required."
            )
    else:  # stored
        if not payload.private_key.strip():
            raise HTTPException(
                status_code=400, detail="A private key value is required."
            )
        try:
            public_key = sshkeys.public_key_from_private(payload.private_key)
        except sshkeys.SshKeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        fingerprint = sshkeys.fingerprint(public_key)
        enc = keystore.encrypt(payload.private_key.strip())
    try:
        key = repository.create_ssh_key(
            conn,
            name=payload.name,
            kind=payload.kind,
            path=payload.path,
            encrypted_private_key=enc,
            public_key=public_key,
            fingerprint=fingerprint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="ssh_key_create",
        actor=admin,
        target_type="ssh_key",
        target_id=key["id"],
        target_name=key["name"],
        detail=f"kind={key['kind']}",
    )
    return SshKeyOut(**key)


@router.delete("/settings/ssh-keys/{key_id}", response_model=MessageOut)
def delete_ssh_key(
    key_id: int,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    key = repository.get_ssh_key(conn, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="SSH key not found")
    refs = repository.ssh_key_references(conn, key_id)
    if refs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This key is still in use by: " + ", ".join(refs),
        )
    repository.delete_ssh_key(conn, key_id)
    # Remove any decrypted on-disk copy so no plaintext key lingers.
    servers.remove_materialized_key(key_id)
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="ssh_key_delete",
        actor=admin,
        target_type="ssh_key",
        target_id=key_id,
        target_name=key["name"],
    )
    return MessageOut(detail="SSH key deleted")


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
    if payload.admin_ssh_key_id is not None and (
        repository.get_ssh_key(conn, payload.admin_ssh_key_id) is None
    ):
        raise HTTPException(status_code=400, detail="Unknown SSH key")
    try:
        template = repository.create_server_template(
            conn,
            vmid=payload.vmid,
            name=payload.name,
            kind=payload.kind,
            admin_ssh_key_path=payload.admin_ssh_key_path,
            admin_ssh_key_id=payload.admin_ssh_key_id,
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
    if payload.admin_ssh_key_id is not None and (
        repository.get_ssh_key(conn, payload.admin_ssh_key_id) is None
    ):
        raise HTTPException(status_code=400, detail="Unknown SSH key")
    # Distinguish "field omitted" (leave unchanged) from an explicit null
    # (clear the assigned key), which Pydantic collapses to None.
    clear_key = (
        "admin_ssh_key_id" in payload.model_fields_set
        and payload.admin_ssh_key_id is None
    )
    try:
        template = repository.update_server_template(
            conn,
            template_id,
            vmid=payload.vmid,
            name=payload.name,
            kind=payload.kind,
            admin_ssh_key_path=payload.admin_ssh_key_path,
            admin_ssh_key_id=payload.admin_ssh_key_id,
            clear_admin_ssh_key_id=clear_key,
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


# ---------------------------------------------------------------------------
# User-facing helpers (Account page)
# ---------------------------------------------------------------------------


@router.get(
    "/account/server-templates", response_model=list[ServerTemplateOptionOut]
)
def list_account_server_templates(
    _: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ServerTemplateOptionOut]:
    """Template options for the Add Server form (no vmid or key paths)."""
    return [
        ServerTemplateOptionOut(id=t["id"], name=t["name"], kind=t["kind"])
        for t in repository.list_server_templates(conn)
    ]


@router.get("/account/server-access", response_model=ServerAccessOut)
def get_account_server_access(
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ServerAccessOut:
    row = repository.get_settings_row(conn)
    if not _provider_configured(row):
        return ServerAccessOut(
            can_create=False, reason="The LXC/VM provider is not configured."
        )
    if user.get("role") == "admin":
        return ServerAccessOut(can_create=True)
    if not user.get("self_service"):
        return ServerAccessOut(
            can_create=False,
            reason="Only self-service accounts may create servers.",
        )
    if not bool(row.get("provisioning_self_service", 0)):
        return ServerAccessOut(
            can_create=False,
            reason="Self-service server provisioning is disabled.",
        )
    return ServerAccessOut(can_create=True)


# ---------------------------------------------------------------------------
# User servers
# ---------------------------------------------------------------------------


def _require_self_or_admin(actor: dict[str, Any], user_id: int) -> bool:
    """True when the actor is an admin; raises unless admin or the user."""
    if actor.get("role") == "admin":
        return True
    if actor.get("id") == user_id:
        return False
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not permitted for other users' servers",
    )


def _server_out(server: dict[str, Any]) -> UserServerOut:
    return UserServerOut(**server)


def _check_resource_quota(
    row: dict[str, Any],
    usage: dict[str, int],
    additional: dict[str, int],
) -> None:
    """400 when ``usage + additional`` exceeds the per-user policy caps."""
    caps = {
        "cpus": int(row.get("provisioning_max_cpus", 12)),
        "memory_gb": int(row.get("provisioning_max_memory_gb", 24)),
        "disk_gb": int(row.get("provisioning_max_disk_gb", 200)),
    }
    labels = {"cpus": "CPUs", "memory_gb": "memory (GB)", "disk_gb": "disk (GB)"}
    for key, cap in caps.items():
        wanted = usage.get(key, 0) + additional.get(key, 0)
        if wanted > cap:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Per-user {labels[key]} limit exceeded: "
                    f"{wanted} > {cap}. Ask an administrator to adjust "
                    "resources or limits."
                ),
            )


@router.get("/users/{user_id}/servers", response_model=list[UserServerOut])
def list_user_servers(
    user_id: int,
    actor: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[UserServerOut]:
    _require_self_or_admin(actor, user_id)
    if repository.get_user_by_id(conn, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    return [_server_out(s) for s in repository.list_user_servers(conn, user_id)]


@router.post(
    "/users/{user_id}/servers", response_model=UserServerOut, status_code=201
)
def create_user_server(
    user_id: int,
    payload: CreateUserServerRequest,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> UserServerOut:
    is_admin = _require_self_or_admin(actor, user_id)
    target = repository.get_user_by_id(conn, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    row = repository.get_settings_row(conn)

    if not is_admin:
        if not actor.get("self_service"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only self-service users may create their own servers",
            )
        if not bool(row.get("provisioning_self_service", 0)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Self-service server provisioning is disabled",
            )

    if not _provider_configured(row):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The LXC/VM provider is not configured yet",
        )
    template = repository.get_server_template(conn, payload.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Server template not found")

    try:
        name = servers.validate_server_name(payload.name)
        os_users = servers.parse_os_users(payload.pubkey_users)
    except servers.ServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.install_pubkey and not os_users:
        # Default to the owner's derived user-id, but only when it is a
        # valid OS username; otherwise skip rather than fail the provision.
        try:
            os_users = servers.parse_os_users(target.get("user_id", ""))
        except servers.ServerError:
            os_users = []

    if any(
        s["name"].lower() == name.lower()
        for s in repository.list_user_servers(conn, user_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A server named '{name}' already exists for this user",
        )

    provider_config = _provider_config(row)

    # Quota enforcement for non-admin creators: count and (best-effort)
    # pre-clone resource check based on the template's configuration.
    if not is_admin:
        max_servers = int(row.get("provisioning_max_servers", 3))
        if repository.count_user_servers(conn, user_id) >= max_servers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Server limit reached ({max_servers} per user)",
            )
        probe = proxmox.ProxmoxResult()
        source = proxmox.find_guest(
            provider_config, template["vmid"], result=probe
        )
        template_resources = None
        if source is not None:
            template_resources = proxmox.get_guest_resources(
                provider_config,
                source["node"],
                template["vmid"],
                source["kind"],
                result=probe,
            )
        if not template_resources:
            # Fail closed: without the template's resource footprint the
            # per-user caps cannot be verified for a non-admin creator.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Could not verify resource quotas against the provider; "
                    "try again or ask an administrator to create the server"
                ),
            )
        usage = repository.sum_user_server_resources(conn, user_id)
        _check_resource_quota(row, usage, template_resources)

    key = repository.get_user_ssh_key(conn, user_id)
    admin_key_path = servers.resolve_ssh_key(
        conn,
        template.get("admin_ssh_key_id"),
        fallback_path=(template.get("admin_ssh_key_path") or "").strip(),
    )
    outcome = servers.create_server(
        provider_config=provider_config,
        template=template,
        name=name,
        owner_public_key=(key or {}).get("public_key", ""),
        install_pubkey=payload.install_pubkey,
        os_users=os_users,
        admin_key_path=admin_key_path,
    )
    # Persist which registry key was used, so rotation can reuse it.
    resources = outcome.get("resources") or {}
    # A record is "failed" only when no guest was produced; when the clone
    # exists (even if a later step errored) it consumes real capacity and
    # must be tracked and quota-counted. The transcript carries any errors.
    record_status = "created" if outcome.get("vmid") else "failed"
    row_kwargs: dict[str, Any] = {
        "user_id": user_id,
        "hostname": servers.hostname_for(name),
        "template_id": template["id"],
        "template_name": template["name"],
        "vmid": outcome.get("vmid"),
        "node": outcome.get("node", ""),
        "kind": outcome.get("kind", template["kind"]),
        "ip_address": outcome.get("ip_address", ""),
        "cpus": resources.get("cpus", 0),
        "memory_gb": resources.get("memory_gb", 0),
        "disk_gb": resources.get("disk_gb", 0),
        "admin_modified": is_admin,
        "admin_ssh_key_id": template.get("admin_ssh_key_id"),
        "status": record_status,
        "last_log": outcome["transcript"],
    }
    try:
        server = repository.create_user_server(conn, name=name, **row_kwargs)
    except ValueError:
        # Concurrent create raced past the pre-check; never lose the record
        # (and its transcript) of an already-cloned guest.
        fallback = f"{name}-{outcome.get('vmid') or 'retry'}"[:40]
        server = repository.create_user_server(
            conn, name=fallback, **row_kwargs
        )
    logger.info(
        "Server creation %s user=%s name=%r vmid=%s by=%r",
        outcome["status"],
        target["username"],
        name,
        outcome.get("vmid"),
        actor.get("username"),
    )
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="server_create",
        actor=actor,
        target_type="user_server",
        target_id=server["id"],
        target_name=f"{target['username']}:{name}",
        detail=(
            f"status={server['status']} template={template['name']} "
            f"vmid={server['vmid']} ip={server['ip_address'] or '-'}"
        ),
    )
    return _server_out(server)


@router.patch(
    "/users/{user_id}/servers/{server_id}", response_model=UserServerOut
)
def update_user_server(
    user_id: int,
    server_id: int,
    payload: UpdateUserServerRequest,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> UserServerOut:
    is_admin = _require_self_or_admin(actor, user_id)
    server = repository.get_user_server(conn, user_id, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    row = repository.get_settings_row(conn)

    updates: dict[str, Any] = {}
    if payload.ip_address is not None:
        # The stored IP later drives SSH key propagation, so rewriting it is
        # restricted: admins may correct any record; owners only supply the
        # manual IP of their own VMs (LXC addresses are auto-discovered) and
        # must be self-service accounts.
        if not is_admin:
            if not actor.get("self_service"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only self-service users may set server IPs",
                )
            if server["kind"] != "vm":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="LXC addresses are discovered automatically",
                )
        try:
            updates["ip_address"] = servers.validate_ip(payload.ip_address)
        except servers.ServerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    resource_change = {
        k: v
        for k, v in (
            ("cpus", payload.cpus),
            ("memory_gb", payload.memory_gb),
            ("disk_gb", payload.disk_gb),
        )
        if v is not None
    }
    if resource_change:
        if server["kind"] != "lxc" or server["status"] == "failed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Resources can only be changed on LXC servers",
            )
        if not server["vmid"] or not server["node"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This server is managed outside AppManager",
            )
        if not is_admin:
            if not actor.get("self_service") or not bool(
                row.get("provisioning_allow_resource_edit", 0)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Resource changes are not enabled for your account",
                )
            if server["admin_modified"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "An administrator manages this server's resources; "
                        "ask an administrator to change them"
                    ),
                )
            usage = repository.sum_user_server_resources(conn, user_id)
            additional = {
                key: resource_change.get(key, server[key]) - server[key]
                for key in ("cpus", "memory_gb", "disk_gb")
            }
            _check_resource_quota(row, usage, additional)

        result = proxmox.ProxmoxResult()
        ok = proxmox.set_lxc_resources(
            _provider_config(row),
            server["node"],
            server["vmid"],
            cpus=resource_change.get("cpus"),
            memory_gb=resource_change.get("memory_gb"),
            disk_gb_target=resource_change.get("disk_gb"),
            current_disk_gb=server["disk_gb"],
            result=result,
        )
        updated_log = (server["last_log"] + "\n\n--- resource change ---\n"
                       + result.transcript).strip()
        if not ok:
            repository.update_user_server(
                conn, user_id, server_id, last_log=updated_log
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Resource change failed: {result.transcript[-400:]}",
            )
        updates.update(resource_change)
        updates["last_log"] = updated_log
        if is_admin:
            updates["admin_modified"] = True

    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Nothing to update"
        )
    updated = repository.update_user_server(conn, user_id, server_id, **updates)
    if updated is None:  # concurrently deleted
        raise HTTPException(status_code=404, detail="Server not found")
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="server_update",
        actor=actor,
        target_type="user_server",
        target_id=server_id,
        target_name=f"{updated['name']}",
        detail=", ".join(
            f"{k}={v}" for k, v in updates.items() if k != "last_log"
        ),
    )
    return _server_out(updated)


@router.delete(
    "/users/{user_id}/servers/{server_id}", response_model=MessageOut
)
def delete_user_server(
    user_id: int,
    server_id: int,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    is_admin = _require_self_or_admin(actor, user_id)
    server = repository.get_user_server(conn, user_id, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if not is_admin and not actor.get("self_service"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only self-service users may remove their server records",
        )
    repository.delete_user_server(conn, user_id, server_id)
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="server_delete",
        actor=actor,
        target_type="user_server",
        target_id=server_id,
        target_name=server["name"],
        detail="record removed; the guest itself is not touched",
    )
    return MessageOut(
        detail=(
            "Server record removed. The LXC/VM itself was not deleted; "
            "manage it in Proxmox."
        )
    )
