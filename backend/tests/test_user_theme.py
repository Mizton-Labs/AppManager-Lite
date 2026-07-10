"""Per-user UI theme selection (issue_020)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_member(client, csrf, username="themer@example.com"):
    resp = client.post(
        "/api/users",
        json={"username": username, "role": "user", "teams": [],
              "apps_server": "apps.example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(app, username, password):
    member = TestClient(app)
    login = member.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200, login.text
    return member, login.json()["csrf_token"]


def test_user_theme_defaults_empty_and_persists(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    member, mcsrf = _login(client.app, "themer@example.com", created["password"])

    # New users start with no explicit theme (they follow the default).
    assert member.get("/api/session").json()["user"]["theme"] == ""

    resp = member.patch(
        "/api/account/theme",
        json={"theme": "energy"},
        headers={"X-CSRF-Token": mcsrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["theme"] == "energy"

    # Persisted and reflected in the session's user payload.
    assert member.get("/api/session").json()["user"]["theme"] == "energy"


def test_user_theme_rejects_unknown_value(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf, username="themer2@example.com")
    member, mcsrf = _login(client.app, "themer2@example.com", created["password"])
    resp = member.patch(
        "/api/account/theme",
        json={"theme": "neon"},
        headers={"X-CSRF-Token": mcsrf},
    )
    assert resp.status_code == 422


def test_user_theme_is_per_user_not_global(admin) -> None:
    """One user's theme choice must not affect another user."""
    client, csrf, _ = admin
    a = _create_member(client, csrf, username="alice@example.com")
    b = _create_member(client, csrf, username="bob@example.com")
    ma, ca = _login(client.app, "alice@example.com", a["password"])
    mb, _ = _login(client.app, "bob@example.com", b["password"])

    ma.patch(
        "/api/account/theme",
        json={"theme": "classic"},
        headers={"X-CSRF-Token": ca},
    )
    assert ma.get("/api/session").json()["user"]["theme"] == "classic"
    # Bob is unaffected.
    assert mb.get("/api/session").json()["user"]["theme"] == ""


def test_user_theme_requires_csrf(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf, username="themer3@example.com")
    member, _ = _login(client.app, "themer3@example.com", created["password"])
    # Missing CSRF token is rejected.
    resp = member.patch("/api/account/theme", json={"theme": "light"})
    assert resp.status_code == 403
