"""Administrator server-provisioning routes (issue_015).

Provider (Proxmox) configuration, provisioning policy, and the registry of
server templates used to create user servers. The Proxmox API key is
write-only: it is stored when provided and never returned, logged, or
audited.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
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
    UpdateSshKeyRequest,
    CreateUserServerRequest,
    JumpSyncEntry,
    JumpSyncOut,
    JumpAccountModeRequest,
    JumpAccountModeOut,
    MessageOut,
    ProviderTemplatesOut,
    ProvisioningSettingsOut,
    OwnerServersOut,
    ResourceUsageOut,
    ServerAccessOut,
    ServersOverviewOut,
    ServerStatsOut,
    ServerStatsPointOut,
    ServerTemplateOptionOut,
    ServerTemplateOut,
    ServerUsageOut,
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
        jump_port=int(row.get("jump_port", 22) or 22),
        jump_ssh_key_id=row.get("jump_ssh_key_id"),
        jump_management_user=row.get("jump_management_user", "root") or "root",
        jump_account_mode=row.get("jump_account_mode", "per_user")
        or "per_user",
        jump_jumper_user=row.get("jump_jumper_user", "") or "",
        jump_bundle_override=bool(row.get("jump_bundle_override", 0)),
        jump_bundle_host=row.get("jump_bundle_host", "") or "",
        jump_bundle_port=int(row.get("jump_bundle_port", 22) or 22),
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

    # Enabling the jump server requires host, management user, and key to be
    # present in the resulting state (reject "enabled but not configured"). In
    # shared account mode a jumper user is also required.
    current = repository.get_settings_row(conn)
    effective_enabled = changes.get("jump_enabled", bool(current.get("jump_enabled", 0)))
    if effective_enabled:
        eff_host = changes.get("jump_host", current.get("jump_host", ""))
        eff_mgmt = changes.get(
            "jump_management_user",
            current.get("jump_management_user", "root"),
        ) or "root"
        eff_key = changes.get("jump_ssh_key_id", current.get("jump_ssh_key_id"))
        if not (eff_host and eff_mgmt and eff_key):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Enabling the jump server requires a host, management user, "
                    "and SSH key."
                ),
            )
        eff_mode = current.get("jump_account_mode", "per_user") or "per_user"
        if eff_mode == "shared":
            eff_jumper = changes.get(
                "jump_jumper_user", current.get("jump_jumper_user", "")
            )
            if not eff_jumper:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Shared jump account mode requires a jumper user name."
                    ),
                )

    # The bundle-address override needs its own host once enabled (this address
    # is only written into user SSH configs; AppManager never dials it).
    effective_bundle_override = changes.get(
        "jump_bundle_override", bool(current.get("jump_bundle_override", 0))
    )
    if effective_bundle_override:
        eff_bundle_host = changes.get(
            "jump_bundle_host", current.get("jump_bundle_host", "")
        )
        if not eff_bundle_host:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Enabling the bundle address override requires a bundle host."
                ),
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


def _sync_all_users(conn: sqlite3.Connection) -> tuple[bool, list[JumpSyncEntry]]:
    """Onboard every active user under the current config. Returns (ok, rows)."""
    results: list[JumpSyncEntry] = []
    all_ok = True
    for user in repository.list_users(conn):
        if not user.get("is_active", True):
            continue
        status_str, detail = jumpserver.sync_user(conn, user)
        if status_str not in ("onboarded", "disabled"):
            all_ok = False
        results.append(
            JumpSyncEntry(
                username=user["username"], status=status_str, detail=detail
            )
        )
    return all_ok, results


def _offboard_all_from(
    conn: sqlite3.Connection, config: jumpserver.JumpConfig, account: str
) -> None:
    """Best-effort removal of every user's key from a single bastion account.

    Used to clean up the previous model's location when switching account mode
    (e.g. drain the shared account when moving to per-user, or drain each
    per-user account when moving to shared).
    """
    if not account or not jumpserver.servers._OS_USER_RE.match(account):
        return
    for user in repository.list_users(conn):
        key = repository.get_user_ssh_key(conn, user["id"]) or {}
        pub = key.get("public_key", "")
        if not pub:
            continue
        try:
            jumpserver.offboard_user(
                config, os_user=account, public_key=pub,
                result=proxmox.ProxmoxResult(),
            )
        except Exception:  # noqa: BLE001 - best effort cleanup
            pass


@router.post(
    "/settings/jump-server/account-mode", response_model=JumpAccountModeOut
)
def change_jump_account_mode(
    payload: JumpAccountModeRequest,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> JumpAccountModeOut:
    """Switch the jump account model, re-syncing all users transactionally.

    Changing the model would leave the bastion inconsistent with the stored
    config, so the admin must acknowledge a re-sync. The new mode is applied,
    all users are re-synced, and the previous location is drained. If any user
    fails to sync, the change is fully reverted and the error is reported.
    """
    row = repository.get_settings_row(conn)
    prev_mode = row.get("jump_account_mode", "per_user") or "per_user"
    prev_jumper = row.get("jump_jumper_user", "") or ""
    new_mode = payload.account_mode
    new_jumper = payload.jumper_user or prev_jumper

    if not bool(row.get("jump_enabled", 0)):
        raise HTTPException(
            status_code=400, detail="The jump server is not enabled"
        )
    if new_mode == "shared" and not new_jumper:
        raise HTTPException(
            status_code=400,
            detail="Switching to shared mode requires a jumper user name.",
        )
    # A shared account named the same as any user's derived id would make the
    # per-user drain (and offboarding) ambiguous, risking data loss.
    if new_mode == "shared":
        collision = next(
            (
                u["username"]
                for u in repository.list_users(conn)
                if jumpserver.os_user_for(u) == new_jumper
            ),
            None,
        )
        if collision is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The jumper user '{new_jumper}' collides with the account "
                    f"of user {collision}. Choose a different shared account "
                    "name."
                ),
            )
    if new_mode == prev_mode and new_jumper == prev_jumper:
        raise HTTPException(
            status_code=400, detail="The jump account model is unchanged."
        )
    if not payload.acknowledge_sync:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Changing the jump account model re-syncs every user to the "
                "bastion. Acknowledge the re-sync to proceed."
            ),
        )

    # Apply the new model, then re-sync everyone under it.
    repository.update_provisioning_settings(
        conn, jump_account_mode=new_mode, jump_jumper_user=new_jumper
    )
    config = jumpserver.load_config(conn)
    if not config.ready:
        # Restore and bail before touching the bastion.
        repository.update_provisioning_settings(
            conn, jump_account_mode=prev_mode, jump_jumper_user=prev_jumper
        )
        raise HTTPException(
            status_code=400,
            detail="The jump server is enabled but not fully configured.",
        )

    all_ok, results = _sync_all_users(conn)

    if not all_ok:
        # Revert the mode; report why. Before restoring the config, drain any
        # keys already written under the NEW model so a failed switch cannot
        # widen access (e.g. a partial per_user->shared must not leave users in
        # the shared account after reverting to per-user).
        if new_mode == "shared" and new_jumper:
            _offboard_all_from(conn, config, new_jumper)
        repository.update_provisioning_settings(
            conn, jump_account_mode=prev_mode, jump_jumper_user=prev_jumper
        )
        failed = [r for r in results if r.status not in ("onboarded", "disabled")]
        reason = "; ".join(
            f"{r.username}: {r.detail or r.status}" for r in failed[:5]
        )
        audit.record(
            conn,
            category=audit.CATEGORY_SYSTEM,
            action="jump_account_mode_revert",
            actor=admin,
            target_type="jump_server",
            target_name=config.host,
            detail=f"from={prev_mode} to={new_mode} failed; reverted. {reason}"[:400],
        )
        return JumpAccountModeOut(
            account_mode=prev_mode,
            reverted=True,
            detail=(
                "Re-sync failed; the account model change was reverted. "
                f"{reason}"
            ),
            results=results,
        )

    # Success: drain the previous location so old key copies do not linger.
    # Never drain the account that is the CURRENT target under the new config
    # (guards against a jumper name that collides with a user's derived id,
    # which would otherwise wipe the keys we just installed).
    new_config = jumpserver.load_config(conn)
    keep = {
        jumpserver.target_account(new_config, u)
        for u in repository.list_users(conn)
    }
    if prev_mode == "shared" and prev_jumper and prev_jumper not in keep:
        # Left shared mode (or renamed the shared account): drain the old one.
        _offboard_all_from(conn, new_config, prev_jumper)
    elif prev_mode == "per_user" and new_mode == "shared":
        # Moved into a shared account: drain each user's old per-user account
        # (skipping any that is now the shared target via the ``keep`` guard).
        for user in repository.list_users(conn):
            account = jumpserver.os_user_for(user)
            if account not in keep:
                _offboard_all_from(conn, new_config, account)

    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="jump_account_mode_change",
        actor=admin,
        target_type="jump_server",
        target_name=config.host,
        detail=f"from={prev_mode} to={new_mode} jumper={new_jumper} users={len(results)}",
    )
    return JumpAccountModeOut(
        account_mode=new_mode, reverted=False, results=results
    )


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


@router.patch("/settings/ssh-keys/{key_id}", response_model=SshKeyOut)
def update_ssh_key(
    key_id: int,
    payload: UpdateSshKeyRequest,
    admin: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> SshKeyOut:
    """Edit a registry key (name, path, kind, or replace the stored key).

    Editing is allowed even while the key is referenced (it updates in place).
    Switching to a path key clears the stored secret; providing a new private
    key (or keeping kind=stored) re-encrypts it and recomputes the public key
    and fingerprint. Any on-disk materialized copy is removed when the secret
    or kind changes so the next use re-materializes fresh.
    """
    existing = repository.get_ssh_key(conn, key_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="SSH key not found")

    new_kind = payload.kind or existing["kind"]
    fields: dict[str, Any] = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.kind is not None:
        fields["kind"] = payload.kind

    secret_or_kind_changed = False
    if new_kind == "path":
        # A path key holds no secret material.
        if payload.path is not None:
            fields["path"] = payload.path
        # The resulting path must be non-empty (mirrors create-time validation);
        # otherwise the key would resolve to an empty `ssh -i` argument.
        effective_path = (
            payload.path if payload.path is not None else existing["path"]
        )
        if not (effective_path or "").strip():
            raise HTTPException(
                status_code=400, detail="A key file path is required."
            )
        if existing["kind"] != "path":
            # Clear stored secret / public material when leaving stored.
            fields["encrypted_private_key"] = ""
            fields["public_key"] = ""
            fields["fingerprint"] = ""
            secret_or_kind_changed = True
    else:  # stored
        fields["path"] = ""
        if payload.private_key is not None and payload.private_key.strip():
            try:
                public_key = sshkeys.public_key_from_private(payload.private_key)
            except sshkeys.SshKeyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            fields["encrypted_private_key"] = keystore.encrypt(
                payload.private_key.strip()
            )
            fields["public_key"] = public_key
            fields["fingerprint"] = sshkeys.fingerprint(public_key)
            secret_or_kind_changed = True
        elif existing["kind"] != "stored" or not existing.get("has_private_key"):
            # Switching to stored (or a stored key with no material) needs a key.
            raise HTTPException(
                status_code=400, detail="A private key value is required."
            )

    try:
        key = repository.update_ssh_key(conn, key_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if key is None:
        raise HTTPException(status_code=404, detail="SSH key not found")
    if secret_or_kind_changed:
        # Drop any stale decrypted copy so the next resolve re-materializes.
        servers.remove_materialized_key(key_id)
    audit.record(
        conn,
        category=audit.CATEGORY_SYSTEM,
        action="ssh_key_update",
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
            main_os_user=payload.main_os_user,
            enable_sudo=payload.enable_sudo,
            enable_trusted_access=payload.enable_trusted_access,
            is_apps_server=payload.is_apps_server,
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
            main_os_user=payload.main_os_user,
            enable_sudo=payload.enable_sudo,
            enable_trusted_access=payload.enable_trusted_access,
            is_apps_server=payload.is_apps_server,
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
        ServerTemplateOptionOut(
            id=t["id"],
            name=t["name"],
            kind=t["kind"],
            is_apps_server=bool(t.get("is_apps_server", False)),
        )
        for t in repository.list_server_templates(conn)
    ]


@router.get("/account/server-access", response_model=ServerAccessOut)
def get_account_server_access(
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ServerAccessOut:
    row = repository.get_settings_row(conn)
    # issue_017: a self-service user may edit their own servers' resources only
    # when the admin has enabled it; admins may always edit.
    allow_resource_edit = user.get("role") == "admin" or (
        bool(user.get("self_service"))
        and bool(row.get("provisioning_allow_resource_edit", 0))
    )
    if not _provider_configured(row):
        return ServerAccessOut(
            can_create=False,
            reason="The LXC/VM provider is not configured.",
            allow_resource_edit=allow_resource_edit,
        )
    if user.get("role") == "admin":
        return ServerAccessOut(
            can_create=True, allow_resource_edit=allow_resource_edit
        )
    if not user.get("self_service"):
        return ServerAccessOut(
            can_create=False,
            reason="Only self-service accounts may create servers.",
            allow_resource_edit=allow_resource_edit,
        )
    if not bool(row.get("provisioning_self_service", 0)):
        return ServerAccessOut(
            can_create=False,
            reason="Self-service server provisioning is disabled.",
            allow_resource_edit=allow_resource_edit,
        )
    return ServerAccessOut(
        can_create=True, allow_resource_edit=allow_resource_edit
    )


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


def _server_out(
    server: dict[str, Any], *, include_error: bool = False
) -> UserServerOut:
    """Serialize a server row, deriving the deferred-deletion flags.

    ``include_error`` controls whether the destroy-failure detail is exposed:
    only administrators (who own the recovery flow) see it; owners never do.
    """
    requested_at = server.get("deletion_requested_at", "") or ""
    error = server.get("deletion_error", "") or ""
    data = dict(server)
    data["deletion_requested_at"] = requested_at
    data["deletion_pending"] = bool(requested_at)
    data["deletion_failed"] = bool(error)
    data["deletion_error"] = error if include_error else ""
    return UserServerOut(**data)


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


# Deferred-deletion grace window: a requested deletion can be cancelled for
# this long before the sweep destroys the guest (issue_015-r4 F1).
_DELETION_GRACE_SECONDS = 24 * 60 * 60
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# The lazy sweep runs on the request path (and at startup). To bound worst-case
# request latency and avoid a backlog of due deletions compounding within one
# request, at most this many guests are destroyed per sweep call; the rest
# settle on the next trigger. Kept at 1 so a single list request never blocks on
# more than one stop+destroy round-trip (each bounded by the Proxmox task
# budgets); a backlog drains across successive requests/startup. A single-flight
# lock ensures concurrent list requests do not each launch a blocking sweep.
_SWEEP_MAX_PER_CALL = 1
_sweep_lock = threading.Lock()


def _utcnow_str() -> str:
    """Current UTC time in the same textual format SQLite's datetime('now')."""
    return datetime.now(timezone.utc).strftime(_TS_FORMAT)


def _parse_ts(value: str) -> datetime | None:
    """Parse a stored timestamp as UTC; None when unparseable/empty."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, _TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        # Tolerate fractional seconds or an ISO 'T' separator just in case.
        try:
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _deletion_due(requested_at: str, *, now: datetime | None = None) -> bool:
    """True when a deletion request has passed the grace window."""
    ts = _parse_ts(requested_at)
    if ts is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - ts).total_seconds() >= _DELETION_GRACE_SECONDS


def _destroy_and_settle(
    conn: sqlite3.Connection, row: dict[str, Any]
) -> None:
    """Destroy one server's guest and settle its record.

    On success the record is removed. On failure the record is kept and marked
    with ``deletion_error`` (moving it into the admin-only recovery list) and
    the destroy transcript is appended to ``last_log``. Never raises.
    """
    settings_row = repository.get_settings_row(conn)
    if not _provider_configured(settings_row):
        # Can't destroy without a provider; record the reason and keep the row
        # so an admin can act once the provider is configured.
        repository.update_user_server(
            conn, row["user_id"], row["id"],
            deletion_error="The LXC/VM provider is not configured; "
            "cannot destroy the guest.",
        )
        return
    outcome = servers.destroy_server(
        provider_config=_provider_config(settings_row),
        node=row.get("node", ""),
        vmid=row.get("vmid"),
        kind=row.get("kind", "lxc"),
    )
    transcript = outcome.get("transcript", "")
    if outcome.get("status") == "ok":
        repository.delete_user_server(conn, row["user_id"], row["id"])
        logger.info(
            "Deferred deletion destroyed server id=%s (user=%s, vmid=%s)",
            row["id"], row["user_id"], row.get("vmid"),
        )
        return
    # Failed: keep the row, record the error, append the transcript.
    merged_log = (row.get("last_log", "") +
                  "\n\n--- deletion (destroy failed) ---\n" +
                  transcript).strip()
    repository.update_user_server(
        conn, row["user_id"], row["id"],
        deletion_error=(transcript.splitlines()[-1] if transcript else
                        "destroy failed"),
        last_log=merged_log,
    )
    logger.warning(
        "Deferred deletion FAILED for server id=%s (user=%s, vmid=%s); "
        "kept in admin list for recovery",
        row["id"], row["user_id"], row.get("vmid"),
    )


def expire_pending_server_deletions(conn: sqlite3.Connection) -> int:
    """Destroy guests whose deletion grace window has elapsed (lazy sweep).

    Called opportunistically (app startup and on each server-list request),
    mirroring the sessions.purge_expired precedent - there is no background
    scheduler. Returns the number of servers actioned. Best-effort and
    self-contained: a failure on one server is recorded and never blocks the
    others or the triggering request.

    Bounded and single-flight: at most ``_SWEEP_MAX_PER_CALL`` guests are
    destroyed per call, and if another sweep is already running this call
    returns immediately (0) instead of piling on duplicate blocking work.
    """
    if not _sweep_lock.acquire(blocking=False):
        # Another request/startup is already sweeping; don't duplicate the
        # (blocking, network-bound) work or hold up this request.
        return 0
    try:
        return _run_deletion_sweep(conn)
    finally:
        _sweep_lock.release()


def _run_deletion_sweep(conn: sqlite3.Connection) -> int:
    actioned = 0
    try:
        pending = repository.list_servers_pending_deletion(conn)
    except Exception:  # noqa: BLE001 - a sweep must never break the caller
        logger.exception("Could not list servers pending deletion")
        return 0
    now = datetime.now(timezone.utc)
    for row in pending:
        if actioned >= _SWEEP_MAX_PER_CALL:
            # Defer the remaining due rows to the next trigger to bound the
            # latency this one request/startup pass can incur.
            break
        # Skip rows already marked failed: they await admin action, not a
        # re-destroy on every list call (avoids hammering a broken provider).
        if row.get("deletion_error"):
            continue
        requested_at = row.get("deletion_requested_at", "")
        if _parse_ts(requested_at) is None:
            # A non-empty but unparseable timestamp would otherwise leave the
            # server "pending" forever and invisible to admins. Surface it as a
            # recoverable error rather than silently never destroying it.
            try:
                repository.update_user_server(
                    conn, row["user_id"], row["id"],
                    deletion_error=(
                        "Deletion timestamp is unreadable; cannot determine the "
                        "grace window. Cancel and re-request, or force-remove."
                    ),
                )
                actioned += 1
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Could not flag malformed deletion timestamp for id=%s",
                    row["id"],
                )
            continue
        if not _deletion_due(requested_at, now=now):
            continue
        try:
            _destroy_and_settle(conn, row)
            actioned += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "Unexpected error settling deletion for server id=%s", row["id"]
            )
    return actioned


# issue_017: cap the number of inline provider reads performed while listing a
# user's servers, so a large backlog of unrecorded specs cannot make one list
# request issue an unbounded number of synchronous Proxmox calls.
_MAX_BACKFILL_PER_REQUEST = 8


def _backfill_server_resources(
    conn: sqlite3.Connection,
    user_id: int,
    rows: list[dict[str, Any]],
) -> None:
    """Populate missing (0) CPU/memory/disk specs from the provider.

    Best-effort and fully guarded. Only targets rows that have a vmid+node and
    whose recorded cpus is 0; reference servers (no vmid) are left untouched.
    Successful reads are persisted and the in-memory row dicts are updated so
    the response reflects the refreshed values immediately.
    """
    candidates = [
        r
        for r in rows
        if int(r.get("cpus") or 0) == 0
        and r.get("vmid")
        and (r.get("node") or "")
        and r.get("kind") == "lxc"
        and r.get("status") != "failed"
    ]
    if not candidates:
        return
    # Bound the synchronous provider work per request: at most a handful of
    # reads run inline; the rest are picked up on subsequent list refreshes as
    # earlier ones are persisted (and drop out of the candidate set).
    candidates = candidates[:_MAX_BACKFILL_PER_REQUEST]
    try:
        row = repository.get_settings_row(conn)
        if not _provider_configured(row):
            return
        config = _provider_config(row)
    except Exception:  # noqa: BLE001
        logger.exception("Resource backfill: could not load provider config")
        return
    for server in candidates:
        try:
            result = proxmox.ProxmoxResult()
            specs = proxmox.get_guest_resources(
                config,
                server["node"],
                int(server["vmid"]),
                server["kind"],
                result=result,
            )
            if not specs:
                continue
            repository.update_user_server(
                conn,
                user_id,
                server["id"],
                cpus=int(specs["cpus"]),
                memory_gb=int(specs["memory_gb"]),
                disk_gb=int(specs["disk_gb"]),
            )
            server["cpus"] = int(specs["cpus"])
            server["memory_gb"] = int(specs["memory_gb"])
            server["disk_gb"] = int(specs["disk_gb"])
        except Exception:  # noqa: BLE001
            logger.exception(
                "Resource backfill failed for server id=%s", server.get("id")
            )


@router.get("/users/{user_id}/servers", response_model=list[UserServerOut])
def list_user_servers(
    user_id: int,
    actor: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[UserServerOut]:
    is_admin = _require_self_or_admin(actor, user_id)
    if repository.get_user_by_id(conn, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    # Lazy deferred-deletion sweep (no background scheduler): destroy any guest
    # whose 24h grace window has elapsed before returning the list, so the
    # caller sees an up-to-date view. Best-effort; never blocks the response.
    expire_pending_server_deletions(conn)
    rows = repository.list_user_servers(conn, user_id)
    # issue_017: lazily backfill resource specs from the provider for any guest
    # that has a vmid+node but whose recorded specs are 0 (e.g. older records or
    # clones whose specs weren't reported at creation). Persist so the "always
    # show resources" card is populated on subsequent loads. Fully guarded: a
    # provider error must never fail or slow the list beyond the attempt.
    _backfill_server_resources(conn, user_id, rows)
    if is_admin:
        # Admins see everything, including servers whose destroy failed, with
        # the failure detail for recovery.
        return [_server_out(s, include_error=True) for s in rows]
    # Owners never see failed-destroy rows (those are the admin's to resolve)
    # nor the error detail.
    return [
        _server_out(s)
        for s in rows
        if not (s.get("deletion_error") or "")
    ]


@router.get("/users/{user_id}/servers/usage", response_model=ServerUsageOut)
def get_user_server_usage(
    user_id: int,
    actor: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ServerUsageOut:
    """Per-user provisioning usage vs. limits (for the create-form quota bars).

    Self-or-admin. The committed usage mirrors quota enforcement exactly
    (``count_user_servers`` and ``sum_user_server_resources``, which exclude
    admin-set servers and never-cloned failures). Administrators are exempt
    from per-user caps, so their result is flagged ``unlimited``.
    """
    _require_self_or_admin(actor, user_id)
    target = repository.get_user_by_id(conn, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    row = repository.get_settings_row(conn)
    used_servers = repository.count_user_servers(conn, user_id)
    res = repository.sum_user_server_resources(conn, user_id)
    if target.get("role") == "admin":
        # Admins have no per-user caps; report usage with no limit so the UI
        # can show a "no restrictions" note instead of bars.
        return ServerUsageOut(
            unlimited=True,
            servers=ResourceUsageOut(used=used_servers, limit=0),
            cpus=ResourceUsageOut(used=int(res["cpus"]), limit=0),
            memory_gb=ResourceUsageOut(used=int(res["memory_gb"]), limit=0),
            disk_gb=ResourceUsageOut(used=int(res["disk_gb"]), limit=0),
        )
    return ServerUsageOut(
        unlimited=False,
        servers=ResourceUsageOut(
            used=used_servers,
            limit=int(row.get("provisioning_max_servers", 3)),
        ),
        cpus=ResourceUsageOut(
            used=int(res["cpus"]),
            limit=int(row.get("provisioning_max_cpus", 12)),
        ),
        memory_gb=ResourceUsageOut(
            used=int(res["memory_gb"]),
            limit=int(row.get("provisioning_max_memory_gb", 24)),
        ),
        disk_gb=ResourceUsageOut(
            used=int(res["disk_gb"]),
            limit=int(row.get("provisioning_max_disk_gb", 200)),
        ),
    )


@router.get("/servers/overview", response_model=ServersOverviewOut)
def servers_overview(
    actor: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ServersOverviewOut:
    """All servers the caller may see, grouped by owner (issue_015-r5 F2).

    Administrators see every user's servers (with destroy-failure detail);
    a non-admin sees only their own group. The lazy deferred-deletion sweep is
    run first so the view is current.
    """
    is_admin = actor.get("role") == "admin"
    expire_pending_server_deletions(conn)
    groups: dict[int, OwnerServersOut] = {}

    def _group(uid: int, username: str) -> OwnerServersOut:
        if uid not in groups:
            groups[uid] = OwnerServersOut(
                user_id=uid,
                username=username,
                derived_user_id=repository.derive_user_id(username or ""),
            )
        return groups[uid]

    if is_admin:
        for srv in repository.list_all_servers(conn):
            grp = _group(srv["user_id"], srv.get("owner_username", ""))
            grp.servers.append(_server_out(srv, include_error=True))
    else:
        username = actor.get("username", "")
        grp = _group(actor["id"], username)
        for srv in repository.list_user_servers(conn, actor["id"]):
            grp.servers.append(_server_out(srv))
    owners = sorted(groups.values(), key=lambda g: g.username.lower())
    return ServersOverviewOut(is_admin=is_admin, owners=owners)


@router.get(
    "/users/{user_id}/servers/{server_id}/stats",
    response_model=ServerStatsOut,
)
def get_user_server_stats(
    user_id: int,
    server_id: int,
    timeframe: str = "hour",
    actor: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ServerStatsOut:
    """Historical CPU/memory/disk/network usage for a server (Proxmox rrddata).

    Self-or-admin. Read-only. Returns ``available=false`` with a reason when the
    server has no guest yet, the provider is unconfigured, the timeframe is
    invalid, or the read fails - the caller renders an empty chart rather than
    erroring.
    """
    _require_self_or_admin(actor, user_id)
    server = repository.get_user_server(conn, user_id, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if timeframe not in proxmox.RRD_TIMEFRAMES:
        return ServerStatsOut(
            available=False, detail="Invalid timeframe.", timeframe="hour"
        )
    if server.get("vmid") is None:
        return ServerStatsOut(
            available=False,
            detail="This server has no running guest to report stats for.",
            timeframe=timeframe,
        )
    row = repository.get_settings_row(conn)
    if not _provider_configured(row):
        return ServerStatsOut(
            available=False,
            detail="The LXC/VM provider is not configured.",
            timeframe=timeframe,
        )
    result = proxmox.ProxmoxResult()
    samples = proxmox.get_guest_rrddata(
        _provider_config(row),
        server.get("node", ""),
        server["vmid"],
        server.get("kind", "lxc"),
        timeframe=timeframe,
        result=result,
    )
    if samples is None:
        return ServerStatsOut(
            available=False,
            detail="Could not read usage statistics from the provider.",
            timeframe=timeframe,
        )
    points = [
        ServerStatsPointOut(
            time=int(s["time"]),
            cpu_pct=round(s["cpu"] * 100, 2),
            mem=s["mem"],
            maxmem=s["maxmem"],
            disk=s["disk"],
            maxdisk=s["maxdisk"],
            netin=s["netin"],
            netout=s["netout"],
        )
        for s in samples
    ]
    return ServerStatsOut(available=True, timeframe=timeframe, points=points)


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

    template_main_user = (template.get("main_os_user") or "").strip()
    # The server's full name always carries a static prefix so every server
    # follows the same "<template>-<owner-id>-<suffix>" convention (matching the
    # auto-provision naming). The request's `name` is only the user-chosen
    # suffix; the prefix is derived from the template and the TARGET user, for
    # admin- and self-service-initiated creations alike. The template portion is
    # slugified so an oddly-named template can never compose an invalid name.
    derived_uid = target.get("user_id") or repository.derive_user_id(
        target.get("username", "") or ""
    )
    if not derived_uid:
        raise HTTPException(
            status_code=400,
            detail=(
                "This user has no valid derived id to build a server name; an "
                "administrator should adjust the username."
            ),
        )
    prefix = servers.server_name_prefix(template.get("name", ""), derived_uid)
    suffix = payload.name.strip()
    try:
        os_users = servers.parse_os_users(payload.pubkey_users)
    except servers.ServerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not suffix:
        raise HTTPException(
            status_code=400, detail="A server name suffix is required."
        )
    try:
        name = servers.validate_server_name(prefix + suffix)
    except servers.ServerError as exc:
        available = servers.MAX_SERVER_NAME_LEN - len(prefix)
        if available <= 0:
            detail = (
                "The template and account names already exceed the server-name "
                f"limit ({servers.MAX_SERVER_NAME_LEN}); an administrator must "
                "shorten the template name."
            )
        else:
            detail = (
                f"The full server name '{prefix}{suffix}' is invalid: {exc} "
                f"The suffix may be at most {available} character(s) and may "
                "contain letters, digits, spaces, dots, dashes, and underscores."
            )
        raise HTTPException(status_code=400, detail=detail) from exc
    if payload.install_pubkey:
        if template_main_user:
            # A template main user overrides the request/default: the user's
            # key is installed ONLY for that OS user (issue_015-r2).
            os_users = [template_main_user]
        elif not os_users:
            # Default to the owner's derived user-id, but only when it is a
            # valid OS username; otherwise skip rather than fail the provision.
            try:
                os_users = servers.parse_os_users(target.get("user_id", ""))
            except servers.ServerError:
                os_users = []

    # Server names are globally unique (case-insensitive): the composed name
    # already embeds owner and template, so a collision means the suffix must
    # change.
    if repository.server_name_exists(conn, name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A server named '{name}' already exists. Choose a different "
                "name suffix."
            ),
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

    server = _clone_and_persist_server(
        conn,
        actor=actor,
        target=target,
        template=template,
        provider_config=provider_config,
        name=name,
        os_users=os_users,
        install_pubkey=payload.install_pubkey,
        template_main_user=template_main_user,
        admin_modified=is_admin,
    )
    return _server_out(server)


def _clone_and_persist_server(
    conn: sqlite3.Connection,
    *,
    actor: dict[str, Any],
    target: dict[str, Any],
    template: dict[str, Any],
    provider_config: dict[str, Any],
    name: str,
    os_users: list[str],
    install_pubkey: bool,
    template_main_user: str,
    admin_modified: bool,
) -> dict[str, Any]:
    """Clone the guest, persist the server record, reconcile the trusted mesh.

    Shared by the create-server endpoint and create-user auto-provisioning.
    Callers must have already validated auth, quota, the provider, the name,
    and the template. This raises only on genuinely unexpected errors; a
    provider/clone failure is captured in the returned record's ``failed``
    status and transcript.
    """
    user_id = int(target["id"])
    key = repository.get_user_ssh_key(conn, user_id)
    owner_uid = target.get("user_id") or repository.derive_user_id(
        target.get("username", "") or ""
    )
    admin_key_path = servers.resolve_ssh_key(
        conn,
        template.get("admin_ssh_key_id"),
        fallback_path=(template.get("admin_ssh_key_path") or "").strip(),
    )
    # Only ensure/create the account with a bash login shell when os_users is
    # confidently "the template's configured main OS user" - i.e. the template
    # sets main_os_user (an admin-controlled, single-value setting) and the
    # resolved os_users is exactly that. Never for a caller-supplied/free-form
    # user list (e.g. self-service pubkey_users), which could otherwise let a
    # non-admin request auto-create arbitrary OS accounts.
    ensure_shell = (
        servers.DEFAULT_ACCOUNT_SHELL
        if template_main_user and os_users == [template_main_user]
        else None
    )
    outcome = servers.create_server(
        provider_config=provider_config,
        template=template,
        name=name,
        owner_public_key=(key or {}).get("public_key", ""),
        install_pubkey=install_pubkey,
        os_users=os_users,
        admin_key_path=admin_key_path,
        enable_sudo=bool(template.get("enable_sudo", True)),
        owner_marker=f"AppManager-managed:{owner_uid}",
        ensure_account_shell=ensure_shell,
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
        "admin_modified": admin_modified,
        "admin_ssh_key_id": template.get("admin_ssh_key_id"),
        "status": record_status,
        "last_log": outcome["transcript"],
    }
    try:
        server = repository.create_user_server(conn, name=name, **row_kwargs)
    except ValueError:
        # Concurrent create raced past the pre-check; never lose the record
        # (and its transcript) of an already-cloned guest. Append the unique
        # vmid, reserving space so the discriminator is not truncated away even
        # when the composed name is already at the length limit.
        disc = str(outcome.get("vmid") or "retry")
        head = name[: max(0, servers.MAX_SERVER_NAME_LEN - len(disc) - 1)]
        fallback = f"{head}-{disc}"[: servers.MAX_SERVER_NAME_LEN]
        try:
            server = repository.create_user_server(
                conn, name=fallback, **row_kwargs
            )
        except ValueError:
            # Extremely unlikely (same vmid colliding); fall back to a globally
            # unique-enough name rather than 500 and lose the guest record.
            fallback = f"{head}-{disc}-{id(outcome) & 0xffff:x}"[
                : servers.MAX_SERVER_NAME_LEN
            ]
            server = repository.create_user_server(
                conn, name=fallback, **row_kwargs
            )
    # Trusted-access mesh: once the new server record exists, reconcile the SSH
    # mesh across all of this user's trusted servers created from templates that
    # enable trusted access. Best-effort and fully guarded: a mesh failure must
    # never propagate and lose the server record for the already-cloned guest.
    if server["status"] == "created" and server["ip_address"] and bool(
        template.get("enable_trusted_access", False)
    ):
        server = _reconcile_and_record_mesh(
            conn, user_id, server, template_main_user, admin_key_path
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
    return server


def provision_default_servers(
    conn: sqlite3.Connection,
    *,
    actor: dict[str, Any],
    target: dict[str, Any],
    template_ids: list[int],
) -> list[dict[str, Any]]:
    """Auto-provision one server per template for a freshly-created user.

    Runs synchronously as part of user creation. Every step is best-effort:
    a missing provider, unknown template, or clone failure yields a per-template
    result but never raises, so user creation always succeeds. Servers are named
    ``<TEMPLATE_NAME>-<USER_ID>`` following the default naming convention.

    Returns a list of ``{template_id, template_name, status, detail}`` dicts.
    """
    results: list[dict[str, Any]] = []
    if not template_ids:
        return results
    user_id = int(target["id"])
    derived_user_id = target.get("user_id") or repository.derive_user_id(
        target.get("username", "") or ""
    )
    # Commit the already-created user (and jump onboarding) before cloning any
    # real guests. Each guest is then committed as it is created, so an
    # unexpected later error can never roll back the DB record of a Proxmox
    # guest that actually exists (which would orphan it and drift quotas).
    conn.commit()
    row = repository.get_settings_row(conn)
    provider_ok = _provider_configured(row)
    provider_config = _provider_config(row) if provider_ok else {}
    for template_id in template_ids:
        template = repository.get_server_template(conn, template_id)
        if template is None:
            results.append({
                "template_id": template_id,
                "template_name": "",
                "status": "skipped",
                "detail": "Server template not found.",
            })
            continue
        template_name = template["name"]
        if not provider_ok:
            results.append({
                "template_id": template_id,
                "template_name": template_name,
                "status": "skipped",
                "detail": "The LXC/VM provider is not configured yet.",
            })
            continue
        # Default naming convention: <template-slug>-USERID (validated + capped),
        # matching the static prefix used for user-created servers.
        raw_name = servers.server_name_prefix(
            template_name, derived_user_id
        ).rstrip("-")
        try:
            name = servers.validate_server_name(
                raw_name[: servers.MAX_SERVER_NAME_LEN]
            )
        except servers.ServerError as exc:
            results.append({
                "template_id": template_id,
                "template_name": template_name,
                "status": "skipped",
                "detail": f"Invalid server name {raw_name!r}: {exc}",
            })
            continue
        template_main_user = (template.get("main_os_user") or "").strip()
        os_users: list[str] = []
        if template_main_user:
            os_users = [template_main_user]
        else:
            try:
                os_users = servers.parse_os_users(derived_user_id)
            except servers.ServerError:
                os_users = []
        try:
            server = _clone_and_persist_server(
                conn,
                actor=actor,
                target=target,
                template=template,
                provider_config=provider_config,
                name=name,
                os_users=os_users,
                install_pubkey=True,
                template_main_user=template_main_user,
                admin_modified=True,
            )
            results.append({
                "template_id": template_id,
                "template_name": template_name,
                "status": server["status"],
                "detail": (
                    f"vmid={server['vmid']} ip={server['ip_address'] or '-'}"
                    if server["status"] == "created"
                    else "Provisioning failed; see the server log."
                ),
            })
            # Persist this guest's record immediately so a later failure can
            # never roll back the DB record of an existing Proxmox guest.
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - never block user creation
            # Discard any partial write from this failed attempt without
            # touching the already-committed user and prior servers.
            conn.rollback()
            logger.warning(
                "Auto-provision failed user=%s template=%s: %s",
                user_id, template_name, exc,
            )
            results.append({
                "template_id": template_id,
                "template_name": template_name,
                "status": "failed",
                "detail": f"Unexpected error: {exc.__class__.__name__}",
            })
    return results


def _reconcile_and_record_mesh(
    conn: sqlite3.Connection,
    user_id: int,
    server: dict[str, Any],
    main_user: str,
    admin_key_path: str,
) -> dict[str, Any]:
    """Reconcile the trusted mesh and append the transcript to the server log.

    Best-effort: any error is captured, never raised (so the committed server
    record and its guest are preserved). Returns the (possibly updated) server.
    """
    mesh_result = servers.ProxmoxResult()
    try:
        if not main_user:
            mesh_result.log(
                "Trusted access is enabled but the template has no main user; "
                "skipping mesh (a shared OS account is required)."
            )
        else:
            trusted = _trusted_servers_for(conn, user_id, main_user)
            servers.reconcile_trusted_mesh(
                servers=trusted,
                admin_key_path=admin_key_path,
                os_user=main_user,
                result=mesh_result,
            )
    except Exception as exc:  # noqa: BLE001 - best-effort, never fatal
        mesh_result.fail(f"trusted mesh error: {exc.__class__.__name__}")
    merged = (server["last_log"] + "\n\n--- trusted access ---\n"
              + mesh_result.transcript).strip()
    repository.update_user_server(conn, user_id, server["id"], last_log=merged)
    updated = repository.get_user_server(conn, user_id, server["id"])
    return updated or server


def _trusted_servers_for(
    conn: sqlite3.Connection, user_id: int, main_user: str
) -> list[dict[str, Any]]:
    """The user's reachable servers whose template grants trusted access and
    shares the same main OS user (so the mesh applies to one account)."""
    trusted = []
    for s in repository.list_user_servers(conn, user_id):
        if s["status"] == "failed" or not s["ip_address"]:
            continue
        tpl = (
            repository.get_server_template(conn, s["template_id"])
            if s["template_id"] is not None
            else None
        )
        if (
            tpl is not None
            and tpl.get("enable_trusted_access")
            and (tpl.get("main_os_user") or "").strip() == main_user
        ):
            trusted.append(s)
    return trusted


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

    # If a manual IP was just supplied (e.g. a VM), join it to the trusted
    # mesh now that it is reachable. Best-effort; guarded.
    if updates.get("ip_address") and updated["template_id"] is not None:
        tpl = repository.get_server_template(conn, updated["template_id"])
        if tpl is not None and tpl.get("enable_trusted_access"):
            main_user = (tpl.get("main_os_user") or "").strip()
            admin_key_path = servers.resolve_ssh_key(
                conn,
                updated.get("admin_ssh_key_id"),
                fallback_path=(updated.get("admin_ssh_key_path") or "").strip(),
            )
            updated = _reconcile_and_record_mesh(
                conn, user_id, updated, main_user, admin_key_path
            )

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
    "/users/{user_id}/servers/{server_id}", response_model=UserServerOut
)
def request_server_deletion(
    user_id: int,
    server_id: int,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> UserServerOut:
    """Request deletion of a server (deferred; 24h grace, then auto-destroy).

    The caller has already been warned this is permanent and irreversible. The
    server enters a "deletion pending" state for 24 hours, during which it can
    be cancelled; after the window elapses a sweep stops and destroys the guest
    and removes the record. Owners (self-service) and admins may request their
    own; force-removal (below) is a separate admin-only action.
    """
    is_admin = _require_self_or_admin(actor, user_id)
    server = repository.get_user_server(conn, user_id, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if not is_admin and not actor.get("self_service"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only self-service users may delete their servers",
        )
    if server.get("deletion_error"):
        # A failed-destroy record is the admin's to resolve via force-remove.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This server's automatic destruction failed; an administrator "
                "must resolve it."
            ),
        )
    if server.get("deletion_requested_at"):
        # Idempotent: already scheduled.
        return _server_out(server, include_error=is_admin)
    updated = repository.update_user_server(
        conn, user_id, server_id, deletion_requested_at=_utcnow_str()
    )
    assert updated is not None
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="server_delete_request",
        actor=actor,
        target_type="user_server",
        target_id=server_id,
        target_name=server["name"],
        detail="deletion scheduled (24h grace before the guest is destroyed)",
    )
    logger.info(
        "Server deletion requested by user=%r for server id=%s (user=%s)",
        actor.get("username"), server_id, user_id,
    )
    return _server_out(updated, include_error=is_admin)


@router.post(
    "/users/{user_id}/servers/{server_id}/cancel-deletion",
    response_model=UserServerOut,
)
def cancel_server_deletion(
    user_id: int,
    server_id: int,
    actor: dict[str, Any] = Depends(get_current_user),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> UserServerOut:
    """Cancel a pending deletion before the grace window elapses."""
    is_admin = _require_self_or_admin(actor, user_id)
    server = repository.get_user_server(conn, user_id, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    if not is_admin and not actor.get("self_service"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only self-service users may manage their servers",
        )
    if server.get("deletion_error"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This server already failed to destroy and cannot be "
                "un-scheduled; an administrator must resolve it."
            ),
        )
    if not server.get("deletion_requested_at"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This server is not scheduled for deletion.",
        )
    updated = repository.update_user_server(
        conn, user_id, server_id, deletion_requested_at=""
    )
    assert updated is not None
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="server_delete_cancel",
        actor=actor,
        target_type="user_server",
        target_id=server_id,
        target_name=server["name"],
        detail="pending deletion cancelled",
    )
    return _server_out(updated, include_error=is_admin)


@router.post(
    "/users/{user_id}/servers/{server_id}/force-remove",
    response_model=MessageOut,
)
def force_remove_server(
    user_id: int,
    server_id: int,
    actor: dict[str, Any] = Depends(require_admin),
    __: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> MessageOut:
    """Administrator-only: remove a server record, even if its destroy failed.

    A best-effort destroy is attempted first, but the record is removed
    regardless of the outcome - the administrator is expected to have verified
    the guest's state directly in Proxmox. Intended to clear records left in
    the admin list after an automatic destruction failed.
    """
    server = repository.get_user_server(conn, user_id, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    settings_row = repository.get_settings_row(conn)
    destroy_status = "skipped"
    if _provider_configured(settings_row) and server.get("vmid") is not None:
        outcome = servers.destroy_server(
            provider_config=_provider_config(settings_row),
            node=server.get("node", ""),
            vmid=server.get("vmid"),
            kind=server.get("kind", "lxc"),
        )
        destroy_status = outcome.get("status", "failed")
    repository.delete_user_server(conn, user_id, server_id)
    # Record enough to reconcile an orphaned guest: when destroy did not
    # confirm, the guest may still be running with the owner's key installed.
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="server_force_remove",
        actor=actor,
        target_type="user_server",
        target_id=server_id,
        target_name=server["name"],
        detail=(
            f"record force-removed by admin (destroy={destroy_status}; "
            f"vmid={server.get('vmid')} node={server.get('node') or '?'} "
            f"ip={server.get('ip_address') or '?'})"
            + ("" if destroy_status == "ok"
               else " -- guest NOT confirmed destroyed; verify/clean up in "
               "Proxmox (owner key may still be installed)")
        ),
    )
    logger.warning(
        "Server id=%s (user=%s) force-removed by admin=%r (destroy=%s)",
        server_id, user_id, actor.get("username"), destroy_status,
    )
    note = (
        " The guest was destroyed."
        if destroy_status == "ok"
        else " The guest was NOT confirmed destroyed; verify it in Proxmox."
    )
    return MessageOut(detail="Server record removed." + note)
