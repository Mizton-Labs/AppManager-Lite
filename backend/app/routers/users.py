"""Administrator user-management routes.

All routes require an admin session (or run open when authentication is
disabled). Guardrails prevent removing the last active administrator.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, jumpserver, proxmox, repository, security, servers, sessions
from ..deps import get_current_user, get_db, require_admin, verify_csrf
from ..schemas import (
    CreateUserRequest,
    GeneratedPasswordOut,
    MessageOut,
    ProvisionResultOut,
    TeamOut,
    UpdateUserRequest,
    UserOut,
    ApplicationShareUserOut,
)
from .provisioning import (
    _provider_config,
    _provider_configured,
    provision_default_servers,
)

router = APIRouter(tags=["users"])

logger = logging.getLogger(__name__)


def _user_out(user: dict[str, Any]) -> UserOut:
    return UserOut(
        id=user["id"],
        username=user["username"],
        user_id=user["user_id"],
        role=user["role"],
        is_active=user["is_active"],
        must_change_password=user["must_change_password"],
        self_service=user["self_service"],
        apps_server=user["apps_server"],
        apps_server_ip=user["apps_server_ip"],
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


def _preflight_delete_owned_servers(
    conn: sqlite3.Connection, owned_servers: list[dict[str, Any]]
) -> None:
    """Verify every server is safe to destroy before deleting its owner.

    Never destroys anything itself, and never leaves the account partially
    deleted: this only inspects live Proxmox state and raises (aborting the
    whole deletion, before any change is made) if any server cannot be
    confirmed safe. A guest is blocked when its Proxmox "protection" flag is
    on, or when its live state cannot be verified at all (provider
    unconfigured/unreachable, or the guest's presence/protection could not be
    read) -- unknown is treated the same as protected, never as "safe to
    destroy". A guest already confirmed absent from Proxmox has nothing to
    protect and is allowed through (its record is simply removed).

    Includes every row with a ``vmid`` regardless of ``status`` -- a
    ``"failed"`` provisioning record can still carry a real, live guest (the
    clone succeeded but a later step errored), so it must be verified exactly
    like a ``"created"`` row rather than silently skipped, since the destroy
    step below will still act on it.
    """
    candidates = [s for s in owned_servers if s.get("vmid")]
    if not candidates:
        return
    settings_row = repository.get_settings_row(conn)
    if not _provider_configured(settings_row):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot verify this user's server(s) against Proxmox (no "
                "provider configured); deletion is blocked. Transfer the "
                "servers instead, or configure the provider first."
            ),
        )
    config = _provider_config(settings_row)
    inventory_result = proxmox.ProxmoxResult()
    inventory = proxmox.list_cluster_guest_inventory(config, result=inventory_result)
    if inventory_result.status != "ok":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Could not read live Proxmox inventory; deletion is blocked "
                "so a server is never assumed destroyable. Try again, or "
                "transfer the servers instead."
            ),
        )
    blocked: list[str] = []
    for server in candidates:
        vmid = int(server["vmid"])
        if vmid not in inventory:
            continue  # confirmed absent: nothing to protect
        protect_result = proxmox.ProxmoxResult()
        protected = proxmox.get_guest_protection(
            config,
            node=server.get("node", "") or inventory[vmid]["node"],
            kind=server.get("kind", "lxc"),
            vmid=vmid,
            result=protect_result,
        )
        if protected is None:
            blocked.append(f"{server['name']} (protection state unknown)")
        elif protected:
            blocked.append(f"{server['name']} (Proxmox protection is enabled)")
    if blocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot delete this user: the following server(s) block "
                "automatic destruction: " + "; ".join(blocked) + ". Remove "
                "Proxmox protection manually first, or transfer the servers "
                "to another user instead."
            ),
        )


def _destroy_owned_server_for_deletion(
    conn: sqlite3.Connection, row: dict[str, Any]
) -> None:
    """Destroy one server as part of deleting its owner (preflight already
    confirmed it is safe). Removes the DB record only after a successful
    destroy (or when the guest is already confirmed gone)."""
    settings_row = repository.get_settings_row(conn)
    if not _provider_configured(settings_row):
        # Preflight already required a configured provider for any server
        # with a vmid; a reference/never-provisioned row has nothing to
        # destroy either way.
        repository.delete_user_server(conn, row["user_id"], row["id"])
        return
    outcome = servers.destroy_server(
        provider_config=_provider_config(settings_row),
        node=row.get("node", ""),
        vmid=row.get("vmid"),
        kind=row.get("kind", "lxc"),
    )
    if outcome.get("status") == "ok":
        repository.delete_user_server(conn, row["user_id"], row["id"])
    else:
        # Preflight passed but the destroy itself failed (e.g. transient
        # error) -- surface this loudly rather than silently deleting the
        # user while a guest survives untracked.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Failed to destroy server '{row['name']}'; user deletion "
                "aborted so no guest is left untracked. Try again."
            ),
        )


@router.get("/teams", response_model=list[TeamOut])
def list_teams(
    _: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[TeamOut]:
    # Readable by any signed-in user so the sidebar and team pickers can be
    # data-driven. Team names, order, and icons are not sensitive; mutations
    # remain admin-only (see the settings router).
    return [TeamOut(**team) for team in repository.list_teams(conn)]


@router.get("/users", response_model=list[UserOut])
def list_users(
    _: dict[str, Any] = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[UserOut]:
    return [_user_out(u) for u in repository.list_users(conn)]


@router.get("/users/resolve", response_model=ApplicationShareUserOut)
def resolve_share_user(
    identity: str = Query(min_length=1, max_length=254),
    _: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ApplicationShareUserOut:
    needle = identity.strip().casefold()
    matches = [
        user for user in repository.list_users(conn)
        if user["is_active"] and (
            user["username"].casefold() == needle
            or user["user_id"].casefold() == needle
        )
    ]
    if not matches:
        raise HTTPException(status_code=404, detail="User not found")
    if len({user["id"] for user in matches}) != 1:
        raise HTTPException(status_code=409, detail="User identity is ambiguous")
    user = matches[0]
    return ApplicationShareUserOut(id=user["id"], username=user["username"], user_id=user["user_id"])


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
            apps_server_ip=payload.apps_server_ip,
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
    jump_status, jump_detail = jumpserver.sync_user(conn, user)
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="create",
        actor=actor,
        target_type="user",
        target_id=user["id"],
        target_name=user["username"],
        detail=f"role={user['role']} teams={payload.teams} "
        f"self_service={user['self_service']} apps_server={user['apps_server']!r} "
        f"apps_server_ip={user['apps_server_ip']!r} jump={jump_status}",
    )
    if jump_status == "failed":
        logger.warning(
            "Jump-server onboarding failed for %r: %s",
            user["username"],
            jump_detail,
        )
    # Auto-provision one server per selected template (best-effort; a failure
    # never blocks user creation). The admin UI pre-selects every template.
    provisioning_results = provision_default_servers(
        conn,
        actor=actor,
        target=user,
        template_ids=payload.provision_templates,
    )
    return GeneratedPasswordOut(
        user=_user_out(user),
        password=password,
        provisioning=[ProvisionResultOut(**r) for r in provisioning_results],
    )


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
    # issue_017: an apps-server location is optional. A user may clear it (they
    # keep view access to applications; a custom apps server is only needed to
    # create applications).

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

    try:
        updated = repository.update_user(
            conn,
            user_id,
            username=payload.username,
            role=payload.role,
            teams=payload.teams,
            is_active=payload.is_active,
            self_service=payload.self_service,
            apps_server=payload.apps_server,
            apps_server_ip=payload.apps_server_ip,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    detail = (
        f"role={payload.role} is_active={payload.is_active} "
        f"teams={payload.teams} self_service={payload.self_service}"
    )
    if payload.username is not None and payload.username != target["username"]:
        detail += (
            f" username_old={target['username']} username_new={payload.username} "
            f"derived_user_id={updated['user_id']}"
        )
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="update",
        actor=admin,
        target_type="user",
        target_id=user_id,
        target_name=updated["username"],
        detail=detail,
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
    delete_apps: bool = Query(default=False),
    server_disposition: str | None = Query(default=None),
    transfer_servers_to_user_id: int | None = Query(default=None),
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

    owned_servers = repository.list_user_servers(conn, user_id)
    if owned_servers:
        if server_disposition not in ("transfer", "delete"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"This user owns {len(owned_servers)} server(s). Specify "
                    "server_disposition=transfer (with "
                    "transfer_servers_to_user_id) or server_disposition=delete."
                ),
            )
        if server_disposition == "transfer":
            if transfer_servers_to_user_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="transfer_servers_to_user_id is required to transfer servers",
                )
            if transfer_servers_to_user_id == user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot transfer servers to the account being deleted",
                )
            recipient = repository.get_user_by_id(conn, transfer_servers_to_user_id)
            if recipient is None or not recipient["is_active"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Server transfer recipient must be an existing, active user",
                )
        else:  # "delete": every server must be confirmed safe to destroy first
            _preflight_delete_owned_servers(conn, owned_servers)

    # Capture the target jump account + public key before the row is deleted so
    # the jump-server account's key can be revoked afterwards. In shared mode
    # the key lives in the shared jumper account; in per-user mode it lives in
    # the user's own account.
    _jump_cfg = jumpserver.load_config(conn)
    _jump_os_user = jumpserver.target_account(_jump_cfg, target)
    _jump_key = repository.get_user_ssh_key(conn, user_id) or {}
    _jump_pubkey = _jump_key.get("public_key", "")

    if owned_servers:
        if server_disposition == "transfer":
            repository.transfer_user_servers(
                conn, user_id, transfer_servers_to_user_id
            )
            logger.info(
                "Transferred %s server(s) from deleted user id=%s to id=%s",
                len(owned_servers), user_id, transfer_servers_to_user_id,
            )
        else:
            for row in owned_servers:
                _destroy_owned_server_for_deletion(conn, row)

    repository.delete_user(
        conn,
        user_id,
        delete_apps=delete_apps,
        transfer_to_user_id=None if delete_apps else admin.get("id"),
    )
    jump_status, jump_detail = jumpserver.remove_user(
        conn, os_user=_jump_os_user, public_key=_jump_pubkey
    )
    if jump_status == "failed":
        logger.warning(
            "Jump-server offboarding failed for %r: %s",
            target["username"],
            jump_detail,
        )
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
        detail=f"jump={jump_status}",
    )
    detail = (
        "User deleted; applications deleted"
        if delete_apps
        else f"User deleted; applications transferred to {admin.get('username')}"
    )
    return MessageOut(detail=detail)
