"""Configurable branding and the first-run configured flag."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _seed_teams(admin, make_team):
    for _name in ("Red Team",):
        make_team(_name)


def test_session_carries_branding_defaults(client) -> None:
    # Pre-authentication, the session exposes empty branding and configured=False
    # so the login page can render the deployment's own name/logo once set.
    body = client.get("/api/session").json()
    assert body["app_name"] == ""
    assert body["app_logo"] == ""
    assert body["configured"] is False


def test_session_carries_branding_when_auth_disabled(client_no_auth) -> None:
    body = client_no_auth.get("/api/session").json()
    assert "app_name" in body
    assert "app_logo" in body
    assert body["configured"] is False


def test_branding_get_requires_admin(admin) -> None:
    client, csrf, _ = admin
    # Create a normal member and confirm they cannot read or write branding.
    member_pw = client.post(
        "/api/users",
        json={"username": "brandmember", "role": "user", "teams": ["Red Team"]},
        headers={"X-CSRF-Token": csrf},
    ).json()["password"]
    from fastapi.testclient import TestClient

    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login",
            json={"username": "brandmember", "password": member_pw},
        )
        mcsrf = member.get("/api/session").json().get("csrf_token")
        get_resp = member.get("/api/settings/branding")
        patch_resp = member.patch(
            "/api/settings/branding",
            json={"app_name": "Nope"},
            headers={"X-CSRF-Token": mcsrf or ""},
        )
    assert get_resp.status_code == 403
    assert patch_resp.status_code == 403


def test_update_branding_round_trips_and_sets_configured(admin) -> None:
    client, csrf, _ = admin
    resp = client.patch(
        "/api/settings/branding",
        json={"app_name": "Acme Portal", "configured": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["app_name"] == "Acme Portal"
    assert body["configured"] is True
    # Persisted and reflected in the session.
    again = client.get("/api/settings/branding").json()
    assert again["app_name"] == "Acme Portal"
    session = client.get("/api/session").json()
    assert session["app_name"] == "Acme Portal"
    assert session["configured"] is True


def test_update_branding_accepts_raster_logo(admin) -> None:
    client, csrf, _ = admin
    # A 1x1 PNG data URI (tiny, well under the cap) is accepted.
    png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    resp = client.patch(
        "/api/settings/branding",
        json={"app_logo": png},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["app_logo"] == png


def test_update_branding_rejects_script_logo(admin) -> None:
    client, csrf, _ = admin
    # SVG (script-in-SVG vector) is rejected by the icon validator.
    resp = client.patch(
        "/api/settings/branding",
        json={"app_logo": "data:image/svg+xml;base64,AAAA"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


def test_branding_update_is_audited(admin) -> None:
    client, csrf, _ = admin
    client.patch(
        "/api/settings/branding",
        json={"app_name": "Audited Brand"},
        headers={"X-CSRF-Token": csrf},
    )
    events = client.get("/api/audit", params={"category": "system"}).json()
    settings_events = [e for e in events if e["action"] == "settings_update"]
    assert any("branding" in e["detail"] for e in settings_events)
