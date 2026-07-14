"""Authentication and self-service account routes."""

from __future__ import annotations

import io
import logging
import secrets
import sqlite3
import stat
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import PlainTextResponse, RedirectResponse
from starlette.responses import Response as StarletteResponse

from .. import (
    audit,
    jumpserver,
    keystore,
    repository,
    schemas,
    security,
    servers,
    sessions,
    sshkeys,
    sso,
)
from ..proxmox import ProxmoxResult
from ..config import get_settings
from ..deps import get_current_user, get_db, verify_csrf
from ..schemas import (
    ChangePasswordRequest,
    BundleOptionOut,
    LoginRequest,
    MessageOut,
    ServerKeyRotationOut,
    SessionOut,
    SshKeyInfoOut,
    SshKeyRegenerateOut,
    SsoConfigOut,
    SsoProviderOut,
    UpdateThemeRequest,
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


def _post_login_redirect() -> str:
    settings = get_settings()
    return f"{settings.base_prefix}/" if settings.base_prefix else "/"


def _redirect_after_login(return_to: str | None = None) -> str:
    return sso.safe_return_to(return_to) or _post_login_redirect()


def _create_session_response(
    response: Response,
    conn: sqlite3.Connection,
    user: dict[str, Any],
    *,
    auth_method: str = "local",
) -> dict[str, str]:
    created = sessions.create_session(conn, user["id"], auth_method=auth_method)
    _set_session_cookie(response, created["session_id"])
    return created


def _user_out(user: dict[str, Any]) -> UserOut:
    return UserOut(
        id=user["id"],
        username=user["username"],
        user_id=user.get("user_id", repository.derive_user_id(user["username"])),
        role=user["role"],
        is_active=user["is_active"],
        must_change_password=user["must_change_password"],
        self_service=user["self_service"],
        apps_server=user.get("apps_server", ""),
        apps_server_ip=user.get("apps_server_ip", ""),
        theme=user.get("theme", ""),
        teams=user["teams"],
    )


@router.get("/auth/proxy-check", status_code=status.HTTP_204_NO_CONTENT)
def proxy_check(
    request: Request, conn: sqlite3.Connection = Depends(get_db)
) -> StarletteResponse:
    settings = get_settings()
    if not settings.enable_auth:
        return StarletteResponse(status_code=status.HTTP_204_NO_CONTENT)
    session_id = request.cookies.get(sessions.SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    session = sessions.get_session(conn, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    user = repository.get_user_by_id(conn, session["user_id"])
    if user is None or not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return StarletteResponse(status_code=status.HTTP_204_NO_CONTENT)


def _branding(conn: sqlite3.Connection) -> dict[str, Any]:
    """Branding fields included in every session response (readable pre-auth)."""
    row = repository.get_settings_row(conn)
    return {
        "app_name": row.get("app_name", "") or "",
        "app_logo": row.get("app_logo", "") or "",
        "collaborators": repository.parse_collaborators(
            row.get("collaborators", "[]")
        ),
        "default_theme": schemas.normalize_theme(row.get("default_theme")),
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
        auth_method=session["auth_method"],
        **branding,
    )


@router.get("/auth/sso/config", response_model=SsoConfigOut)
def sso_config() -> SsoConfigOut:
    settings = get_settings()
    providers: list[SsoProviderOut] = []
    if settings.enable_auth and settings.oidc_enabled:
        label = settings.oidc_label or sso.provider_label(settings.oidc_provider)
        providers.append(
            SsoProviderOut(
                protocol="oidc", label=label, login_url="auth/oidc/login"
            )
        )
    if settings.enable_auth and settings.saml_enabled:
        providers.append(
            SsoProviderOut(
                protocol="saml",
                label=settings.saml_label or "SAML SSO",
                login_url="auth/saml/login",
            )
        )
    return SsoConfigOut(
        enabled=bool(providers),
        local_login_enabled=settings.sso_local_login_enabled,
        providers=providers,
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
    if not settings.sso_local_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local password login is disabled",
        )

    row = repository.get_user_by_username(conn, payload.username)
    if row is None:
        row = repository.get_user_by_unique_email_local_part(conn, payload.username)
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

    user = repository.get_user_by_id(conn, row["id"])
    assert user is not None
    created = _create_session_response(response, conn, user)
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


@router.get("/auth/oidc/login")
def oidc_login(
    request: Request,
    next: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    if not settings.enable_auth or not settings.oidc_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    sso.create_flow(conn, protocol="oidc", state=state, nonce=nonce, return_to=next or "")
    redirect_uri = str(request.url_for("oidc_callback"))
    url = sso.oidc_login_url(
        settings, redirect_uri=redirect_uri, state=state, nonce=nonce
    )
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/oidc/callback", name="oidc_callback")
def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    if not settings.enable_auth or not settings.oidc_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"OIDC error: {error}"
        )
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC callback is missing code or state",
        )
    flow = sso.consume_flow(conn, protocol="oidc", state=state)
    claims = sso.oidc_claims_from_callback(
        settings,
        code=code,
        redirect_uri=str(request.url_for("oidc_callback")),
        expected_nonce=flow["nonce"],
    )
    email = str(claims.get("email") or claims.get("preferred_username") or "")
    user = sso.user_from_sso_claims(conn, settings, email=email)
    redirect = RedirectResponse(
        _redirect_after_login(flow["return_to"]), status_code=status.HTTP_302_FOUND
    )
    _create_session_response(redirect, conn, user, auth_method="oidc")
    logger.info("OIDC login succeeded for username=%r (id=%s)", user["username"], user["id"])
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="oidc_login",
        actor=user,
        target_type="user",
        target_id=user["id"],
        target_name=user["username"],
    )
    return redirect


@router.get("/auth/saml/login")
async def saml_login(
    request: Request,
    next: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    if not settings.enable_auth or not settings.saml_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    state = secrets.token_urlsafe(32)
    sso.create_flow(conn, protocol="saml", state=state, return_to=next or "")
    auth = await sso.saml_auth(request, settings)
    return RedirectResponse(auth.login(return_to=state), status_code=status.HTTP_302_FOUND)


@router.post("/auth/saml/acs", name="saml_acs")
async def saml_acs(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> RedirectResponse:
    settings = get_settings()
    if not settings.enable_auth or not settings.saml_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    form = await request.form()
    relay_state = str(form.get("RelayState") or "")
    if not relay_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML response is missing RelayState",
        )
    flow = sso.consume_flow(conn, protocol="saml", state=relay_state)
    auth = await sso.saml_auth(request, settings)
    auth.process_response(request_id=None)
    if auth.get_errors() or not auth.is_authenticated():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML response validation failed",
        )
    email = sso.email_from_saml_auth(auth, settings)
    user = sso.user_from_sso_claims(conn, settings, email=email)
    redirect = RedirectResponse(
        _redirect_after_login(flow["return_to"]), status_code=status.HTTP_302_FOUND
    )
    _create_session_response(redirect, conn, user, auth_method="saml")
    logger.info("SAML login succeeded for username=%r (id=%s)", user["username"], user["id"])
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="saml_login",
        actor=user,
        target_type="user",
        target_id=user["id"],
        target_name=user["username"],
    )
    return redirect


@router.get("/auth/saml/metadata", name="saml_metadata")
def saml_metadata(request: Request) -> PlainTextResponse:
    settings = get_settings()
    if not settings.enable_auth or not settings.saml_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return PlainTextResponse(
        sso.saml_metadata_xml(settings, request), media_type="application/samlmetadata+xml"
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


@router.patch("/account/theme", response_model=UserOut)
def update_own_theme(
    payload: UpdateThemeRequest,
    user: dict[str, Any] = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> UserOut:
    """Persist the signed-in user's own UI theme (self-only, issue_020)."""
    updated = repository.set_user_theme(conn, user["id"], payload.theme)
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_out(updated)


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


def _user_servers_with_main_user(
    conn: sqlite3.Connection, user_id: int
) -> list[dict[str, Any]]:
    """The user's servers, each annotated with its template's main OS user."""
    servers = repository.list_user_servers(conn, user_id)
    for s in servers:
        if s.get("template_id") is not None:
            tpl = repository.get_server_template(conn, s["template_id"])
            s["main_os_user"] = (tpl or {}).get("main_os_user", "")
    return servers


@router.get("/account/bundles", response_model=list[BundleOptionOut])
def list_account_bundles(
    _user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[BundleOptionOut]:
    # Disabled templates are hidden from the account download list.
    return [
        BundleOptionOut(
            id=template["id"],
            name=template["name"],
            description=template.get("description", ""),
        )
        for template in repository.list_bundle_templates(conn)
        if template.get("enabled", True)
    ]


@router.get("/account/bundles/{template_id}/download")
def download_account_bundle(
    template_id: int,
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    template = repository.get_bundle_template(conn, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Bundle template not found")
    if not template.get("enabled", True):
        raise HTTPException(status_code=404, detail="Bundle template not found")
    # The downloaded key filename (and thus the config's IdentityFile) is tagged
    # with the application name so users can identify it in ~/.ssh (issue_018).
    key_name = repository.appmanager_key_name(
        repository.get_settings_row(conn).get("app_name", "")
    )
    jump = jumpserver.load_config(conn)
    settings_row = repository.get_settings_row(conn)
    jump_dict = {
        "enabled": jump.enabled,
        "host": jump.host,
        "jump_user": jumpserver.target_account(jump, user),
        "port": jump.port,
        "bundle_override": bool(settings_row.get("jump_bundle_override", 0)),
        "bundle_host": settings_row.get("jump_bundle_host", "") or "",
        "bundle_port": int(settings_row.get("jump_bundle_port", 22) or 22),
    }
    servers = _user_servers_with_main_user(conn, user["id"])
    # Whether the connect scripts can rely on the bundled ``config`` (standard
    # ssh config) or must be self-contained (mapping template text may not be a
    # valid ssh config).
    self_contained_scripts = False
    if template["is_builtin"]:
        # Built-in SSH config: rendered dynamically from the user's servers
        # and the jump server (with ProxyJump when the jump server is enabled).
        content = repository.render_builtin_ssh_config(
            user, servers, jump=jump_dict, key_name=key_name
        )
    elif template["mappings"]:
        content = repository.render_bundle_template(
            template,
            user,
            servers,
            repository.list_server_templates(conn),
        )
        self_contained_scripts = True
    else:
        # No mappings and not builtin: generic per-server config fallback.
        content = repository.render_generic_ssh_config(
            user, servers, key_name=key_name
        )
    safe_name = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in template["name"].lower()
    ).strip("-") or "bundle"

    # Assemble a zip so the user gets a ready-to-use ~/.ssh directory: the SSH
    # config, their private/public key (private key has NO extension so it can
    # be dropped straight into ~/.ssh), and one connect_server_<name>.sh helper
    # per server. The private key makes this effectively a key download, so it
    # is audited and never cached.
    try:
        key = repository.get_user_ssh_key(conn, user["id"])
    except keystore.MasterKeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Server key store is misconfigured; contact an administrator.",
        ) from exc
    if key is None:
        raise HTTPException(status_code=404, detail="SSH key not available")

    scripts = repository.render_connect_scripts(
        user,
        servers,
        jump=jump_dict,
        key_name=key_name,
        self_contained=self_contained_scripts,
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        def _write(name: str, data: str, mode: int) -> None:
            info = zipfile.ZipInfo(name)
            # Explicit Unix metadata keeps permission bits meaningful for
            # extractors that honor the ZIP's create-system field.
            info.create_system = 3
            # Include the regular-file type bit so every extractor treats these
            # as files (not directories) and preserves the permission bits.
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, data)

        _write("config", content, 0o644)
        _write(key_name, key["private_key"], 0o600)
        _write(key_name + ".pub", key["public_key"] + "\n", 0o644)
        for filename, script in scripts:
            _write(filename, script, 0o755)
        _write(
            "README.txt",
            "SSH bundle usage\n"
            "================\n\n"
            "Run a connect script from this extracted directory:\n"
            "  ./connect_server_<name>.sh\n\n"
            "If Windows, WSL, or an archive tool removed its executable bit, use:\n"
            "  sh connect_server_<name>.sh\n\n"
            "The scripts use the private key shipped beside them. Keep the private "
            "key confidential and do not copy it to an untrusted location. The "
            "scripts attempt to set it to mode 600; if WSL files under /mnt do not "
            "preserve Unix permissions, copy the bundle to your Linux home directory "
            "before running it.\n",
            0o644,
        )

    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="ssh_key_download",
        actor=user,
        target_type="user",
        target_id=user["id"],
        target_name=user["username"],
        detail="part=private (bundle)",
    )

    # issue_021: suffix the download with a UTC timestamp so repeated
    # downloads (e.g. after a key rotation) don't collide in a browser's
    # downloads folder. Only the download filename changes; the zip's
    # entries (config, key files, connect scripts) keep their stable names.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{safe_name}-{timestamp}.zip"'
            ),
            "Cache-Control": "no-store",
        },
    )


def _ssh_key_info(user: dict[str, Any], key: dict[str, Any]) -> SshKeyInfoOut:
    return SshKeyInfoOut(
        user_id=user.get("user_id", repository.derive_user_id(user["username"])),
        public_key=key["public_key"],
        generated_at=key["generated_at"],
    )


@router.get("/account/ssh-key", response_model=SshKeyInfoOut)
def get_account_ssh_key(
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> SshKeyInfoOut:
    key = repository.get_user_ssh_key(conn, user["id"])
    if key is None:
        raise HTTPException(status_code=404, detail="SSH key not available")
    return _ssh_key_info(user, key)


@router.get("/account/ssh-key/download")
def download_account_ssh_key(
    part: str = Query(pattern="^(private|public)$"),
    user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Download the caller's own SSH key material.

    The private key is served only to the authenticated owner over the
    session-gated API. Key material is never logged or audited; private-key
    downloads are audited as an event (metadata only) so owners have a
    forensic trail should a session ever be hijacked.
    """
    try:
        key = repository.get_user_ssh_key(conn, user["id"])
    except keystore.MasterKeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Server key store is misconfigured; contact an administrator.",
        ) from exc
    if key is None:
        raise HTTPException(status_code=404, detail="SSH key not available")
    key_name = repository.appmanager_key_name(
        repository.get_settings_row(conn).get("app_name", "")
    )
    if part == "public":
        content = key["public_key"] + "\n"
        filename = key_name + ".pub"
    else:
        content = key["private_key"]
        filename = key_name
        audit.record(
            conn,
            category=audit.CATEGORY_USER,
            action="ssh_key_download",
            actor=user,
            target_type="user",
            target_id=user["id"],
            target_name=user["username"],
            detail="part=private",
        )
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _rotate_key_on_servers(
    conn: sqlite3.Connection,
    user: dict[str, Any],
    old_public_key: str,
    new_public_key: str,
) -> list[ServerKeyRotationOut]:
    """Propagate a regenerated key to the user's servers.

    Best-effort per server: each outcome is reported so the user sees a
    verification summary. Nothing here raises.
    """
    summary: list[ServerKeyRotationOut] = []
    owner_uid = user.get("user_id") or repository.derive_user_id(
        user.get("username", "") or ""
    )
    owner_marker = f"AppManager-managed:{owner_uid}"
    for server in repository.list_user_servers(conn, user["id"]):
        entry = ServerKeyRotationOut(
            server=server["name"],
            ip_address=server["ip_address"],
            status="skipped",
        )
        if server["status"] == "failed":
            entry.detail = "creation failed; nothing to rotate"
            summary.append(entry)
            continue
        if not server["ip_address"]:
            entry.detail = "no IP address on record"
            summary.append(entry)
            continue
        # Resolve the admin key from the registry: the server's own key first,
        # then its template's key (registry FK preferred, legacy path fallback).
        key_id = server.get("admin_ssh_key_id")
        fallback = server.get("admin_ssh_key_path", "")
        if key_id is None and not fallback and server["template_id"] is not None:
            template = repository.get_server_template(
                conn, server["template_id"]
            )
            if template is not None:
                key_id = template.get("admin_ssh_key_id")
                fallback = template.get("admin_ssh_key_path", "")
        admin_key_path = servers.resolve_ssh_key(
            conn, key_id, fallback_path=fallback
        )
        if not admin_key_path:
            entry.detail = "no admin SSH key configured for this server"
            summary.append(entry)
            continue
        if not old_public_key:
            # Without the previous key there is nothing to locate/replace on
            # the server; install the new key manually or via re-provision.
            entry.detail = "no previous key on record; nothing to replace"
            summary.append(entry)
            continue
        result = ProxmoxResult()
        rotation_status = servers.rotate_public_key(
            ip=server["ip_address"],
            admin_key_path=admin_key_path,
            old_public_key=old_public_key,
            new_public_key=new_public_key,
            result=result,
            marker=owner_marker,
        )
        if rotation_status == "updated":
            entry.status = "updated"
        elif rotation_status == "noop":
            entry.status = "skipped"
        else:
            entry.status = "failed"
        entry.detail = result.transcript.splitlines()[-1] if result.steps else ""
        repository.update_user_server(
            conn,
            user["id"],
            server["id"],
            last_log=(server["last_log"] + "\n\n--- key rotation ---\n"
                      + result.transcript).strip(),
        )
        summary.append(entry)

    # Rotate the key on the jump server too, when configured. Guard the whole
    # branch: a stored-key decrypt/materialize failure must not 500 the
    # regenerate (which has already persisted the new key).
    try:
        jump_config = jumpserver.load_config(conn)
    except Exception:  # noqa: BLE001
        jump_config = jumpserver.JumpConfig(
            enabled=False, host="", key_path="", management_user=""
        )
    if jump_config.enabled:
        # The bastion account depends on the configured account model: the
        # user's own account in 'per_user' mode, or the shared jumper account
        # in 'shared' mode (matching jumpserver.sync_user). The owner's derived
        # id is still used as the key's provenance stamp even in shared mode.
        account = jumpserver.target_account(jump_config, user)
        owner_id = jumpserver.os_user_for(user)
        jentry = ServerKeyRotationOut(
            server="jump server", ip_address=jump_config.host, status="skipped"
        )
        if not jump_config.ready:
            jentry.detail = "jump server enabled but not fully configured"
        elif not servers._OS_USER_RE.match(account or ""):
            jentry.detail = f"jump account {account!r} is not a valid username"
        elif not servers._OS_USER_RE.match(owner_id or ""):
            jentry.detail = f"owner id {owner_id!r} is not a valid stamp"
        elif not old_public_key:
            jentry.detail = "no previous key on record; nothing to replace"
        else:
            jresult = ProxmoxResult()
            # Install the new key FIRST so the account never loses access; then
            # remove the old one. If removal fails, the old key lingers (stale)
            # but the user is not locked out - reported as failed for follow-up.
            installed = jumpserver.onboard_user(
                jump_config, os_user=account,
                public_key=new_public_key, result=jresult,
                stamp_id=owner_id,
            )
            removed = jumpserver.offboard_user(
                jump_config, os_user=account,
                public_key=old_public_key, result=jresult,
            )
            if installed and removed:
                jentry.status = "updated"
            elif installed and not removed:
                jentry.status = "failed"
                jentry.detail = (
                    "new key installed but the old key could not be removed; "
                    "remove it manually on the jump server"
                )
            else:
                jentry.status = "failed"
            if not jentry.detail:
                jentry.detail = (
                    jresult.transcript.splitlines()[-1] if jresult.steps else ""
                )
        summary.append(jentry)
    return summary


@router.post("/account/ssh-key/regenerate", response_model=SshKeyRegenerateOut)
def regenerate_account_ssh_key(
    user: dict[str, Any] = Depends(get_current_user),
    _: None = Depends(verify_csrf),
    conn: sqlite3.Connection = Depends(get_db),
) -> SshKeyRegenerateOut:
    """Replace the caller's SSH keypair with a freshly generated one.

    The previous key stops being served immediately, and the old public key
    is removed from (and the new one installed on) each of the user's
    reachable servers; the per-server outcome is returned as a summary.
    """
    if repository.get_user_by_id(conn, user["id"]) is None:
        raise HTTPException(status_code=404, detail="SSH key not available")
    old_key = repository.get_user_ssh_key(conn, user["id"])
    private_key, public_key = sshkeys.generate_keypair(user["username"])
    repository.set_user_ssh_key(
        conn, user["id"], private_key=private_key, public_key=public_key
    )
    # Persist the new key before touching any server: if the process dies
    # mid-rotation, the DB must already serve the key the servers trust.
    conn.commit()
    rotation = _rotate_key_on_servers(
        conn, user, (old_key or {}).get("public_key", ""), public_key
    )
    logger.info(
        "SSH key regenerated by user=%r (id=%s); rotation: %s",
        user["username"],
        user["id"],
        ", ".join(f"{r.server}={r.status}" for r in rotation) or "no servers",
    )
    audit.record(
        conn,
        category=audit.CATEGORY_USER,
        action="ssh_key_regenerate",
        actor=user,
        target_type="user",
        target_id=user["id"],
        target_name=user["username"],
        detail=(
            "rotation: "
            + (", ".join(f"{r.server}={r.status}" for r in rotation)
               or "no servers")
        ),
    )
    key = repository.get_user_ssh_key(conn, user["id"])
    if key is None:  # pragma: no cover - written one statement above
        raise HTTPException(status_code=500, detail="SSH key rotation failed")
    info = _ssh_key_info(user, key)
    return SshKeyRegenerateOut(**info.model_dump(), rotation=rotation)
