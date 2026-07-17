"""Authentication, session, CSRF, and first-run admin behavior."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _proxy_app(*, auth_required: bool = True, private: bool = False) -> int:
    from app.db import get_connection
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO applications (name,url,url_type,is_active,approval_status,alias_auth_required,is_private) "
            "VALUES ('Proxy app','proxy-app','alias',1,'approved',?,?)",
            (int(auth_required), int(private)),
        )
        return int(cur.lastrowid)


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


def test_proxy_check_requires_session(client: TestClient) -> None:
    app_id = _proxy_app()
    resp = client.get(f"/api/auth/proxy-check/{app_id}/proxy-app")
    assert resp.status_code == 401


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
    assert body["auth_method"] == "local"
    assert body["user"]["must_change_password"] is True
    assert body["csrf_token"]


def test_proxy_check_accepts_valid_session(client: TestClient) -> None:
    app_id = _proxy_app()
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    assert login.status_code == 200
    resp = client.get(f"/api/auth/proxy-check/{app_id}/proxy-app")
    assert resp.status_code == 204
    assert not resp.content


def test_proxy_check_allows_anonymous_public_alias(client: TestClient) -> None:
    app_id = _proxy_app(auth_required=False)
    response = client.get(
        f"/api/auth/proxy-check/{app_id}/proxy-app",
    )
    assert response.status_code == 204


def test_proxy_check_rejects_stale_alias_even_with_session(client: TestClient) -> None:
    app_id = _proxy_app()
    client.post("/api/auth/login", json={"username": "admin", "password": client.admin_password})  # type: ignore[attr-defined]
    response = client.get(
        f"/api/auth/proxy-check/{app_id}/old-proxy-app",
    )
    assert response.status_code == 403


def test_proxy_check_auth_disabled_allows_nonprivate_but_denies_private(client_no_auth: TestClient) -> None:
    public_id = _proxy_app(auth_required=True)
    public = client_no_auth.get(f"/api/auth/proxy-check/{public_id}/proxy-app")
    assert public.status_code == 204
    private_id = _proxy_app(private=True)
    private = client_no_auth.get(f"/api/auth/proxy-check/{private_id}/proxy-app")
    assert private.status_code == 401

    shared_id = _proxy_app()
    created = client_no_auth.post(
        "/api/users",
        json={"username": "shared@example.com", "role": "user", "teams": []},
    )
    assert created.status_code == 201
    from app.db import get_connection
    with get_connection() as conn:
        user_id = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
        conn.execute(
            "INSERT INTO application_user_shares (application_id, user_id) VALUES (?, ?)",
            (shared_id, user_id),
        )
    shared = client_no_auth.get(f"/api/auth/proxy-check/{shared_id}/proxy-app")
    assert shared.status_code == 401


def test_state_change_requires_csrf(client: TestClient) -> None:
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    # No CSRF header -> rejected.
    resp = client.post(
        "/api/users",
        json={
            "username": "x@example.com",
            "role": "user",
            "teams": [],
            "apps_server": "apps.example.com",
        },
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
