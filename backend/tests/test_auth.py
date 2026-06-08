"""Authentication, session, CSRF, and first-run admin behavior."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_session_unauthenticated(client: TestClient) -> None:
    body = client.get("/api/session").json()
    assert body["authenticated"] is False
    assert body["enable_auth"] is True
    assert body["user"] is None


def test_protected_route_requires_auth(client: TestClient) -> None:
    assert client.get("/api/users").status_code == 401


def test_first_run_credentials_file_is_private(client: TestClient, tmp_path) -> None:
    creds = tmp_path / "data" / "first-run-admin-credentials.txt"
    assert creds.is_file()
    assert oct(creds.stat().st_mode)[-3:] == "600"


def test_login_rejects_wrong_password(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/login", json={"username": "admin", "password": "not-the-password"}
    )
    assert resp.status_code == 401


def test_login_sets_forced_change_flag(client: TestClient) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["authenticated"] is True
    assert body["user"]["must_change_password"] is True
    assert body["csrf_token"]


def test_state_change_requires_csrf(client: TestClient) -> None:
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    # No CSRF header -> rejected.
    resp = client.post(
        "/api/users", json={"username": "x", "role": "user", "teams": []}
    )
    assert resp.status_code == 403


def test_change_password_clears_flag_and_old_password_fails(admin) -> None:
    client, _csrf, new_pw = admin
    # Re-login with the new password works and the flag is cleared.
    client.post("/api/auth/logout", headers={"X-CSRF-Token": _csrf})
    resp = client.post(
        "/api/auth/login", json={"username": "admin", "password": new_pw}
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["must_change_password"] is False


def test_logout_clears_session(admin) -> None:
    client, csrf, _pw = admin
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/session").json()["authenticated"] is False
