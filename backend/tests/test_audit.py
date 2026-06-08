"""Audit log: events are recorded per category and exposed to admins only."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_member(client, csrf, username, teams):
    resp = client.post(
        "/api/users",
        json={"username": username, "role": "user", "teams": teams},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["password"]


def test_audit_requires_admin(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "auditviewer", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login",
            json={"username": "auditviewer", "password": password},
        )
        resp = member.get("/api/audit")
    assert resp.status_code == 403


def test_audit_invalid_category_rejected(admin) -> None:
    client, _csrf, _ = admin
    resp = client.get("/api/audit", params={"category": "bogus"})
    assert resp.status_code == 400


def test_application_actions_are_audited(admin) -> None:
    client, csrf, _ = admin
    created = client.post(
        "/api/applications",
        json={
            "name": "Audited App",
            "url": "https://example.com/a",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    client.request(
        "DELETE",
        f"/api/applications/{app_id}",
        headers={"X-CSRF-Token": csrf},
    )

    events = client.get("/api/audit", params={"category": "application"}).json()
    actions = {e["action"] for e in events}
    assert "create" in actions
    assert "delete" in actions
    # Newest first.
    assert events[0]["action"] == "delete"
    create_event = next(e for e in events if e["action"] == "create")
    assert create_event["category"] == "application"
    assert create_event["target_name"] == "Audited App"
    assert create_event["actor_username"] == "admin"


def test_user_and_login_events_are_audited(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "newperson", ["Red Team"])
    # A successful login and a failed login.
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login",
            json={"username": "newperson", "password": password},
        )
    with TestClient(client.app) as bad:
        bad.post(
            "/api/auth/login",
            json={"username": "newperson", "password": "wrong-password"},
        )

    events = client.get("/api/audit", params={"category": "user"}).json()
    actions = {e["action"] for e in events}
    assert "create" in actions
    assert "login" in actions
    assert "login_failed" in actions
    # The failed login is recorded even though the request returned 401.
    failed = next(e for e in events if e["action"] == "login_failed")
    assert failed["target_name"] == "newperson"


def test_audit_filter_separates_categories(admin) -> None:
    client, csrf, _ = admin
    client.post(
        "/api/applications",
        json={
            "name": "Cat App",
            "url": "https://example.com/c",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    app_events = client.get("/api/audit", params={"category": "application"}).json()
    user_events = client.get("/api/audit", params={"category": "user"}).json()
    assert all(e["category"] == "application" for e in app_events)
    assert all(e["category"] == "user" for e in user_events)


def test_system_startup_event_recorded(admin) -> None:
    client, _csrf, _ = admin
    events = client.get("/api/audit", params={"category": "system"}).json()
    actions = {e["action"] for e in events}
    # The app started during the test client's lifespan.
    assert "startup" in actions


def test_password_events_do_not_leak_secrets(admin) -> None:
    client, csrf, password = admin
    # Create + reset a user's password, then ensure no secret appears in audit.
    created = client.post(
        "/api/users",
        json={"username": "secretuser", "role": "user", "teams": ["Red Team"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    member_pw = created.json()["password"]
    user_id = created.json()["user"]["id"]
    reset = client.post(
        f"/api/users/{user_id}/reset-password",
        headers={"X-CSRF-Token": csrf},
    )
    reset_pw = reset.json()["password"]
    events = client.get("/api/audit").json()
    blob = " ".join(
        f"{e.get('detail', '')} {e.get('action', '')} {e.get('target_name', '')}"
        for e in events
    )
    assert member_pw not in blob
    assert reset_pw not in blob
    assert password not in blob
