"""SSO configuration and login-flow behavior."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> TestClient:
    data = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(data))
    monkeypatch.setenv("APP_DB_PATH", str(data / "app.db"))
    monkeypatch.setenv("APP_FRONTEND_DIST", str(tmp_path / "no-dist"))
    monkeypatch.setenv("APP_ENABLE_AUTH", "1")
    monkeypatch.setenv("APP_DEV", "1")
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "logs"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    return TestClient(create_app())


def _admin_password(tmp_path: Path) -> str:
    text = (tmp_path / "data" / "first-run-admin-credentials.txt").read_text()
    for line in text.splitlines():
        if line.startswith("password:"):
            return line.split("password:", 1)[1].strip()
    raise AssertionError("first-run admin password not found")


def test_sso_config_lists_enabled_providers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="google",
        APP_OIDC_CLIENT_ID="client-id",
        APP_SAML_ENABLED="1",
        APP_SAML_IDP_ENTITY_ID="https://idp.example.com",
        APP_SAML_IDP_SSO_URL="https://idp.example.com/sso",
        APP_SAML_IDP_X509_CERT="cert",
    ) as client:
        body = client.get("/api/auth/sso/config").json()

    assert body == {
        "enabled": True,
        "local_login_enabled": True,
        "providers": [
            {
                "protocol": "oidc",
                "label": "Single Sign-On",
                "login_url": "auth/oidc/login",
            },
            {
                "protocol": "saml",
                "label": "SAML SSO",
                "login_url": "auth/saml/login",
            },
        ],
    }


def test_auth_mode_local_hides_sso_providers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_AUTH_MODE="local",
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="google",
        APP_OIDC_CLIENT_ID="client-id",
    ) as client:
        body = client.get("/api/auth/sso/config").json()
        oidc_login = client.get("/api/auth/oidc/login", follow_redirects=False)

    assert body == {"enabled": False, "local_login_enabled": True, "providers": []}
    assert oidc_login.status_code == 404


def test_auth_mode_sso_disables_local_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_AUTH_MODE="sso",
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="google",
        APP_OIDC_CLIENT_ID="client-id",
    ) as client:
        config = client.get("/api/auth/sso/config").json()
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "unused"},
        )

    assert config["enabled"] is True
    assert config["local_login_enabled"] is False
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Local password login is disabled"


def test_auth_mode_both_enables_sso_and_local_login(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_AUTH_MODE="both",
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="google",
        APP_OIDC_CLIENT_ID="client-id",
    ) as client:
        config = client.get("/api/auth/sso/config").json()
        resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": _admin_password(tmp_path)},
        )

    assert config["enabled"] is True
    assert config["local_login_enabled"] is True
    assert resp.status_code == 200


def test_microsoft_oidc_endpoints_do_not_duplicate_v2_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="microsoft",
        APP_OIDC_CLIENT_ID="client-id",
        APP_MICROSOFT_TENANT="tenant-id",
    ):
        from app.config import get_settings
        from app.sso import oidc_endpoints

        endpoints = oidc_endpoints(get_settings())

    assert endpoints.issuer == "https://login.microsoftonline.com/tenant-id/v2.0"
    assert (
        endpoints.authorization_endpoint
        == "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/authorize"
    )
    assert (
        endpoints.token_endpoint
        == "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token"
    )
    assert (
        endpoints.jwks_uri
        == "https://login.microsoftonline.com/tenant-id/discovery/v2.0/keys"
    )


def test_oidc_login_redirect_contains_callback_state_and_nonce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="google",
        APP_OIDC_CLIENT_ID="client-id",
    ) as client:
        resp = client.get("/api/auth/oidc/login", follow_redirects=False)

    assert resp.status_code == 302
    location = resp.headers["location"]
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert params["client_id"] == ["client-id"]
    assert params["redirect_uri"] == ["http://testserver/api/auth/oidc/callback"]
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid email profile"]
    assert params["state"][0]
    assert params["nonce"][0]


def test_oidc_callback_provisions_user_and_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="google",
        APP_OIDC_CLIENT_ID="client-id",
    ) as client:
        login = client.get("/api/auth/oidc/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

        from app import sso

        monkeypatch.setattr(
            sso,
            "oidc_claims_from_callback",
            lambda *args, **kwargs: {"email": "new.user@example.com"},
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=abc&state={state}",
            follow_redirects=False,
        )
        session = client.get("/api/session").json()

    assert resp.status_code == 302
    assert session["authenticated"] is True
    assert session["user"]["username"] == "new.user@example.com"
    assert session["user"]["role"] == "user"
    assert session["auth_method"] == "oidc"


def test_oidc_session_keeps_existing_password_reset_flag_but_marks_sso(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="google",
        APP_OIDC_CLIENT_ID="client-id",
        APP_SSO_AUTO_PROVISION="0",
    ) as client:
        from app import repository, sso
        from app.db import get_connection

        with get_connection() as conn:
            repository.create_user(
                conn,
                username="existing.user@example.com",
                password="TemporaryPass123",
                role="user",
                teams=[],
                must_change_password=True,
            )

        login = client.get("/api/auth/oidc/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        monkeypatch.setattr(
            sso,
            "oidc_claims_from_callback",
            lambda *args, **kwargs: {"email": "existing.user@example.com"},
        )
        resp = client.get(
            f"/api/auth/oidc/callback?code=abc&state={state}",
            follow_redirects=False,
        )
        session = client.get("/api/session").json()

    assert resp.status_code == 302
    assert session["authenticated"] is True
    assert session["auth_method"] == "oidc"
    assert session["user"]["username"] == "existing.user@example.com"
    assert session["user"]["must_change_password"] is True


def test_oidc_callback_rejects_invalid_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_OIDC_ENABLED="1",
        APP_OIDC_PROVIDER="google",
        APP_OIDC_CLIENT_ID="client-id",
    ) as client:
        resp = client.get("/api/auth/oidc/callback?code=abc&state=missing")

    assert resp.status_code == 400


def test_saml_acs_requires_relay_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _client(
        monkeypatch,
        tmp_path,
        APP_SAML_ENABLED="1",
        APP_SAML_IDP_ENTITY_ID="https://idp.example.com",
        APP_SAML_IDP_SSO_URL="https://idp.example.com/sso",
        APP_SAML_IDP_X509_CERT="cert",
    ) as client:
        resp = client.post("/api/auth/saml/acs", data={"SAMLResponse": "x"})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "SAML response is missing RelayState"
