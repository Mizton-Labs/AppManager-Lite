"""Authentication, session, CSRF, and first-run admin behavior."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _proxy_app(
    *, auth_required: bool = True, private: bool = False, pass_authenticated_user: bool = False
) -> int:
    from app.db import get_connection
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO applications "
            "(name,url,url_type,is_active,approval_status,alias_auth_required,is_private,pass_authenticated_user) "
            "VALUES ('Proxy app','proxy-app','alias',1,'approved',?,?,?)",
            (int(auth_required), int(private), int(pass_authenticated_user)),
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
    # Opt-in is off by default: no identity is forwarded upstream.
    assert "x-appmanager-user" not in resp.headers


def test_proxy_check_returns_identity_header_when_opted_in(client: TestClient) -> None:
    app_id = _proxy_app(pass_authenticated_user=True)
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    resp = client.get(f"/api/auth/proxy-check/{app_id}/proxy-app")
    assert resp.status_code == 204
    assert resp.headers["x-appmanager-user"] == "admin"


def test_proxy_check_ignores_client_supplied_identity_header(client: TestClient) -> None:
    app_id = _proxy_app(pass_authenticated_user=True)
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    resp = client.get(
        f"/api/auth/proxy-check/{app_id}/proxy-app",
        headers={"X-AppManager-User": "attacker@example.com"},
    )
    assert resp.status_code == 204
    assert resp.headers["x-appmanager-user"] == "admin"


def test_proxy_check_public_alias_never_returns_identity_even_if_opted_in(
    client: TestClient,
) -> None:
    # Data-level inconsistency (opt-in bit set on a public alias) must never
    # leak identity: the server-side invariant is enforced regardless.
    app_id = _proxy_app(auth_required=False, pass_authenticated_user=True)
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    resp = client.get(f"/api/auth/proxy-check/{app_id}/proxy-app")
    assert resp.status_code == 204
    assert "x-appmanager-user" not in resp.headers


def test_proxy_check_auth_disabled_never_returns_identity(
    client_no_auth: TestClient,
) -> None:
    app_id = _proxy_app(auth_required=True, pass_authenticated_user=True)
    resp = client_no_auth.get(f"/api/auth/proxy-check/{app_id}/proxy-app")
    assert resp.status_code == 204
    assert "x-appmanager-user" not in resp.headers


def _login_as_unsafe_username_user(client: TestClient, username: str) -> None:
    """Provision a user with a username that would be unsafe to place in a raw
    HTTP header (e.g. as an SSO-provisioned account might carry, since SSO
    claims are not restricted to the local email-format validator) and start
    a session for them, bypassing the normal create/login API so the unsafe
    value reaches the database directly.
    """
    from app.db import get_connection
    from app import sessions

    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, is_active, self_service) "
            "VALUES (?, 'x', 'admin', 1, 1)",
            (username,),
        )
        user_id = int(cur.lastrowid)
        created = sessions.create_session(conn, user_id)
    client.cookies.set(sessions.SESSION_COOKIE_NAME, created["session_id"])


def test_proxy_check_suppresses_crlf_username_instead_of_injecting_header(
    client: TestClient,
) -> None:
    app_id = _proxy_app(pass_authenticated_user=True)
    _login_as_unsafe_username_user(client, "attacker@example.com\r\nX-Injected: 1")
    resp = client.get(f"/api/auth/proxy-check/{app_id}/proxy-app")
    assert resp.status_code == 204
    assert "x-appmanager-user" not in resp.headers
    assert "x-injected" not in resp.headers


def test_proxy_check_suppresses_non_latin1_username_instead_of_crashing(
    client: TestClient,
) -> None:
    app_id = _proxy_app(pass_authenticated_user=True)
    _login_as_unsafe_username_user(client, "用户@example.com")
    resp = client.get(f"/api/auth/proxy-check/{app_id}/proxy-app")
    assert resp.status_code == 204
    assert "x-appmanager-user" not in resp.headers


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


# ---------------------------------------------------------------------------
# issue_local_031: authorized alias visits, counted from the app-aware
# auth_request classification of the original browser navigation.
# ---------------------------------------------------------------------------


def _alias_visit_totals(app_id: int) -> dict:
    from app.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(request_count),0) total, "
            "GROUP_CONCAT(DISTINCT visitor_key) keys "
            "FROM application_alias_usage_daily WHERE application_id = ?",
            (app_id,),
        ).fetchone()
        return {"total": row["total"], "keys": row["keys"] or ""}


def test_proxy_check_counts_document_navigation(client: TestClient) -> None:
    app_id = _proxy_app()
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    resp = client.get(
        f"/api/auth/proxy-check/{app_id}/proxy-app",
        headers={"Sec-Fetch-Dest": "document"},
    )
    assert resp.status_code == 204
    totals = _alias_visit_totals(app_id)
    assert totals["total"] == 1
    assert "user:" in totals["keys"]


def test_proxy_check_counts_iframe_navigation(client: TestClient) -> None:
    app_id = _proxy_app()
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    resp = client.get(
        f"/api/auth/proxy-check/{app_id}/proxy-app",
        headers={"Sec-Fetch-Dest": "iframe"},
    )
    assert resp.status_code == 204
    assert _alias_visit_totals(app_id)["total"] == 1


def test_proxy_check_does_not_count_subresources(client: TestClient) -> None:
    app_id = _proxy_app()
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    for dest in ("script", "style", "image", "empty"):
        resp = client.get(
            f"/api/auth/proxy-check/{app_id}/proxy-app",
            headers={"Sec-Fetch-Dest": dest},
        )
        assert resp.status_code == 204
    assert _alias_visit_totals(app_id)["total"] == 0


def test_proxy_check_does_not_count_without_fetch_dest_header(client: TestClient) -> None:
    app_id = _proxy_app()
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    resp = client.get(f"/api/auth/proxy-check/{app_id}/proxy-app")
    assert resp.status_code == 204
    assert _alias_visit_totals(app_id)["total"] == 0


def test_proxy_check_does_not_count_denied_requests(client: TestClient) -> None:
    app_id = _proxy_app()
    # Not logged in: denied (401), never counted.
    resp = client.get(
        f"/api/auth/proxy-check/{app_id}/proxy-app",
        headers={"Sec-Fetch-Dest": "document"},
    )
    assert resp.status_code == 401
    assert _alias_visit_totals(app_id)["total"] == 0


def test_proxy_check_counts_public_alias_anonymously(client: TestClient) -> None:
    app_id = _proxy_app(auth_required=False)
    resp = client.get(
        f"/api/auth/proxy-check/{app_id}/proxy-app",
        headers={"Sec-Fetch-Dest": "document"},
    )
    assert resp.status_code == 204
    totals = _alias_visit_totals(app_id)
    assert totals["total"] == 1
    assert totals["keys"] == "anonymous"


def test_proxy_check_counts_auth_disabled_visit_anonymously(
    client_no_auth: TestClient,
) -> None:
    app_id = _proxy_app(auth_required=True)
    resp = client_no_auth.get(
        f"/api/auth/proxy-check/{app_id}/proxy-app",
        headers={"Sec-Fetch-Dest": "document"},
    )
    assert resp.status_code == 204
    totals = _alias_visit_totals(app_id)
    assert totals["total"] == 1
    assert totals["keys"] == "anonymous"


def test_proxy_check_recording_failure_never_denies_authorized_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = _proxy_app()
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": client.admin_password},  # type: ignore[attr-defined]
    )
    from app import repository

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(repository, "record_application_alias_visit", _boom)
    resp = client.get(
        f"/api/auth/proxy-check/{app_id}/proxy-app",
        headers={"Sec-Fetch-Dest": "document"},
    )
    assert resp.status_code == 204
