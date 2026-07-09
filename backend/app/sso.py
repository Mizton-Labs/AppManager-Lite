"""SSO helpers for OIDC and SAML authentication flows."""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from authlib.jose import JsonWebKey, jwt
from fastapi import HTTPException, Request, status
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings

from . import repository
from .config import Settings


FLOW_TTL_MINUTES = 10


@dataclass(frozen=True)
class OidcEndpoints:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    jwks_uri: str


def sso_enabled(settings: Settings) -> bool:
    return bool(settings.enable_auth and (settings.oidc_enabled or settings.saml_enabled))


def safe_return_to(value: str | None) -> str:
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        return ""
    return value


def create_flow(
    conn: sqlite3.Connection,
    *,
    protocol: str,
    state: str,
    nonce: str = "",
    return_to: str = "",
) -> None:
    conn.execute("DELETE FROM sso_auth_flows WHERE expires_at <= datetime('now')")
    conn.execute(
        """
        INSERT INTO sso_auth_flows (state, protocol, nonce, return_to, expires_at)
        VALUES (?, ?, ?, ?, datetime('now', ?))
        """,
        (
            state,
            protocol,
            nonce,
            safe_return_to(return_to),
            f"+{FLOW_TTL_MINUTES} minutes",
        ),
    )


def consume_flow(conn: sqlite3.Connection, *, protocol: str, state: str) -> sqlite3.Row:
    conn.execute("DELETE FROM sso_auth_flows WHERE expires_at <= datetime('now')")
    row = conn.execute(
        """
        SELECT * FROM sso_auth_flows
        WHERE state = ? AND protocol = ? AND expires_at > datetime('now')
        """,
        (state, protocol),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired SSO state",
        )
    conn.execute("DELETE FROM sso_auth_flows WHERE state = ?", (state,))
    return row


def provider_label(provider: str) -> str:
    labels = {
        "google": "Google",
        "microsoft": "Microsoft",
        "azure": "Microsoft",
        "okta": "Okta",
        "auth0": "Auth0",
        "keycloak": "Keycloak",
        "oidc": "Single Sign-On",
        "generic": "Single Sign-On",
    }
    return labels.get(provider.lower(), provider.replace("-", " ").title())


def oidc_endpoints(settings: Settings) -> OidcEndpoints:
    provider = settings.oidc_provider.lower()
    if provider == "google":
        return OidcEndpoints(
            issuer="https://accounts.google.com",
            authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            userinfo_endpoint="https://openidconnect.googleapis.com/v1/userinfo",
            jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        )
    if provider in {"microsoft", "azure"}:
        tenant = settings.microsoft_tenant or "common"
        base = f"https://login.microsoftonline.com/{tenant}"
        issuer = f"{base}/v2.0"
        return OidcEndpoints(
            issuer=issuer,
            authorization_endpoint=f"{base}/oauth2/v2.0/authorize",
            token_endpoint=f"{base}/oauth2/v2.0/token",
            userinfo_endpoint="https://graph.microsoft.com/oidc/userinfo",
            jwks_uri=f"{base}/discovery/v2.0/keys",
        )

    if all(
        (
            settings.oidc_issuer,
            settings.oidc_authorization_endpoint,
            settings.oidc_token_endpoint,
            settings.oidc_userinfo_endpoint,
            settings.oidc_jwks_uri,
        )
    ):
        return OidcEndpoints(
            issuer=settings.oidc_issuer,
            authorization_endpoint=settings.oidc_authorization_endpoint,
            token_endpoint=settings.oidc_token_endpoint,
            userinfo_endpoint=settings.oidc_userinfo_endpoint,
            jwks_uri=settings.oidc_jwks_uri,
        )

    if not settings.oidc_issuer:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC issuer is not configured",
        )
    discovery = _fetch_json(
        f"{settings.oidc_issuer}/.well-known/openid-configuration"
    )
    return OidcEndpoints(
        issuer=str(discovery["issuer"]).rstrip("/"),
        authorization_endpoint=str(discovery["authorization_endpoint"]),
        token_endpoint=str(discovery["token_endpoint"]),
        userinfo_endpoint=str(discovery.get("userinfo_endpoint", "")),
        jwks_uri=str(discovery["jwks_uri"]),
    )


def oidc_login_url(
    settings: Settings, *, redirect_uri: str, state: str, nonce: str
) -> str:
    if not settings.oidc_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not settings.oidc_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC client ID is not configured",
        )
    endpoints = oidc_endpoints(settings)
    query = urllib.parse.urlencode(
        {
            "client_id": settings.oidc_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
        }
    )
    return f"{endpoints.authorization_endpoint}?{query}"


def oidc_claims_from_callback(
    settings: Settings, *, code: str, redirect_uri: str, expected_nonce: str
) -> dict[str, Any]:
    endpoints = oidc_endpoints(settings)
    token_response = _post_form_json(
        endpoints.token_endpoint,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
        },
    )
    id_token = token_response.get("id_token")
    if not isinstance(id_token, str):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC token response did not include an id_token",
        )
    jwks = JsonWebKey.import_key_set(_fetch_json(endpoints.jwks_uri))
    claims = jwt.decode(
        id_token,
        jwks,
        claims_options={
            "iss": {"essential": True, "value": endpoints.issuer},
            "aud": {"essential": True, "value": settings.oidc_client_id},
            "nonce": {"essential": True, "value": expected_nonce},
        },
    )
    claims.validate()
    result = dict(claims)
    if "email" not in result and endpoints.userinfo_endpoint:
        access_token = token_response.get("access_token")
        if isinstance(access_token, str):
            result.update(_fetch_json(endpoints.userinfo_endpoint, bearer=access_token))
    return result


def user_from_sso_claims(
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    email: str,
) -> dict[str, Any]:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSO response did not include a valid email address",
        )
    if settings.sso_email_domain_allowlist:
        domain = email.rsplit("@", 1)[1]
        if domain not in settings.sso_email_domain_allowlist:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This email domain is not allowed to sign in",
            )
    row = repository.get_user_by_username(conn, email)
    if row is not None:
        user = repository.get_user_by_id(conn, row["id"])
        if user is None or not user["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive",
            )
        return user
    if not settings.sso_auto_provision:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No local account is linked to this SSO identity",
        )
    try:
        user = repository.create_sso_user(
            conn, username=email, role=settings.sso_default_role
        )
    except ValueError as exc:
        # e.g. the derived user identifier collides with an existing account.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    # Best-effort jump-server onboarding for auto-provisioned SSO users.
    from . import jumpserver

    jumpserver.sync_user(conn, user)
    return user


def saml_settings(settings: Settings, request: Request) -> dict[str, Any]:
    acs_url = str(request.url_for("saml_acs"))
    metadata_url = str(request.url_for("saml_metadata"))
    entity_id = settings.saml_sp_entity_id or metadata_url
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": entity_id,
            "assertionConsumerService": {
                "url": acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": settings.saml_nameid_format,
        },
        "idp": {
            "entityId": settings.saml_idp_entity_id,
            "singleSignOnService": {
                "url": settings.saml_idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": settings.saml_idp_x509_cert,
        },
        "security": {
            "wantAssertionsSigned": True,
            "wantMessagesSigned": False,
            "wantNameId": True,
            "wantNameIdEncrypted": False,
            "requestedAuthnContext": False,
            "signatureAlgorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            "digestAlgorithm": "http://www.w3.org/2001/04/xmlenc#sha256",
        },
    }


async def saml_auth(request: Request, settings: Settings) -> OneLogin_Saml2_Auth:
    form = await request.form() if request.method == "POST" else {}
    data = {
        "https": "on" if request.url.scheme == "https" else "off",
        "http_host": request.url.netloc,
        "server_port": str(request.url.port or (443 if request.url.scheme == "https" else 80)),
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": dict(form),
    }
    return OneLogin_Saml2_Auth(data, old_settings=saml_settings(settings, request))


def saml_metadata_xml(settings: Settings, request: Request) -> str:
    saml_config = OneLogin_Saml2_Settings(
        settings=saml_settings(settings, request), sp_validation_only=True
    )
    errors = saml_config.validate_metadata(saml_config.get_sp_metadata())
    if errors:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SAML service provider metadata is invalid",
        )
    return saml_config.get_sp_metadata()


def email_from_saml_auth(auth: OneLogin_Saml2_Auth, settings: Settings) -> str:
    attrs = auth.get_attributes()
    values = attrs.get(settings.saml_email_attribute) or attrs.get(
        settings.saml_email_attribute.lower()
    )
    if values:
        return str(values[0])
    return str(auth.get_nameid() or "")


def _fetch_json(url: str, *, bearer: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _post_form_json(url: str, data: dict[str, str]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))
