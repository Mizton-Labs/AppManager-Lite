"""Behavior when authentication is disabled (APP_ENABLE_AUTH=0)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_session_reports_auth_disabled(client_no_auth: TestClient) -> None:
    body = client_no_auth.get("/api/session").json()
    assert body["authenticated"] is True
    assert body["enable_auth"] is False


def test_user_management_open_without_login_or_csrf(client_no_auth: TestClient) -> None:
    # Listing works without a session.
    assert client_no_auth.get("/api/users").status_code == 200
    # State-changing requests succeed without a CSRF token when auth is off.
    resp = client_no_auth.post(
        "/api/users",
        json={"username": "service-account", "role": "user", "teams": []},
    )
    assert resp.status_code == 201, resp.text


def test_account_password_change_disabled(client_no_auth: TestClient) -> None:
    resp = client_no_auth.post(
        "/api/account/password",
        json={
            "current_password": "x",
            "new_password": "y",
            "confirm_password": "y",
        },
    )
    assert resp.status_code == 400
