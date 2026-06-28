"""Authentication and self-service account routes."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse, RedirectResponse
from starlette.responses import Response as StarletteResponse

from .. import audit, repository, security, sessions, sso
from ..config import get_settings
from ..deps import get_current_user, get_db, verify_csrf
from ..schemas import (
    ChangePasswordRequest,
    BundleOptionOut,
    LoginRequest,
    MessageOut,
    SessionOut,
    SsoConfigOut,
    SsoProviderOut,
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
        role=user["role"],
        is_active=user["is_active"],
        must_change_password=user["must_change_password"],
        self_service=user["self_service"],
        apps_server=user.get("apps_server", ""),
        apps_server_ip=user.get("apps_server_ip", ""),
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


@router.get("/account/bundles", response_model=list[BundleOptionOut])
def list_account_bundles(
    _user: dict[str, Any] = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[BundleOptionOut]:
    return [
        BundleOptionOut(id=template["id"], name=template["name"])
        for template in repository.list_bundle_templates(conn)
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
    content = repository.render_bundle_template(template, user)
    safe_name = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in template["name"].lower()
    ).strip("-") or "bundle"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.txt"'},
    )
