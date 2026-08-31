"""Audit log: events are recorded per category and exposed to admins only."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _seed_teams(admin, make_team):
    for _name in ("Red Team",):
        make_team(_name)


def _create_member(client, csrf, username, teams):
    username = username if "@" in username else f"{username}@example.com"
    resp = client.post(
        "/api/users",
        json={
            "username": username,
            "role": "user",
            "teams": teams,
            "apps_server": "apps.example.com",
        },
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
        json={
            "username": "secretuser@example.com",
            "role": "user",
            "teams": ["Red Team"],
            "apps_server": "apps.example.com",
        },
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


# ---------------------------------------------------------------------------
# issue_local_032: navigation activity, kept separate from the security/admin
# audit log above.
# ---------------------------------------------------------------------------


def test_record_navigation_accepts_allowlisted_destination(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/audit/navigation",
        json={"destination": "servers"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 204

    events = client.get("/api/audit/navigation").json()["items"]
    assert any(e["destination"] == "servers" for e in events)


def test_record_navigation_rejects_unknown_destination(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/audit/navigation",
        json={"destination": "not-a-real-section"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


def test_record_navigation_rejects_raw_url_as_destination(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/audit/navigation",
        json={"destination": "/app-manager?editApp=42&secret=1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


def test_record_navigation_requires_csrf(admin) -> None:
    client, _csrf, _ = admin
    resp = client.post("/api/audit/navigation", json={"destination": "servers"})
    assert resp.status_code == 403


def test_navigation_listing_is_admin_only(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "navviewer", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login",
            json={"username": "navviewer", "password": password},
        )
        mcsrf = login.json()["csrf_token"]
        member.post(
            "/api/audit/navigation",
            json={"destination": "home"},
            headers={"X-CSRF-Token": mcsrf},
        )
        resp = member.get("/api/audit/navigation")
    assert resp.status_code == 403


def test_navigation_events_within_five_minutes_are_deduplicated(admin) -> None:
    client, csrf, _ = admin
    for _ in range(3):
        resp = client.post(
            "/api/audit/navigation",
            json={"destination": "app_manager"},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 204

    events = client.get("/api/audit/navigation").json()["items"]
    matching = [e for e in events if e["destination"] == "app_manager"]
    # All three calls land in the same 5-minute bucket for the same actor and
    # destination, so they collapse into a single row with visit_count=3.
    assert len(matching) == 1
    assert matching[0]["visit_count"] == 3


def test_navigation_activity_never_contains_raw_url_or_query_string(admin) -> None:
    client, csrf, _ = admin
    client.post(
        "/api/audit/navigation",
        json={"destination": "settings.general"},
        headers={"X-CSRF-Token": csrf},
    )
    events = client.get("/api/audit/navigation").json()["items"]
    for event in events:
        assert set(event.keys()) == {
            "id", "actor_username", "destination",
            "first_seen_at", "last_seen_at", "visit_count",
        }
        assert "?" not in event["destination"]
        assert "/" not in event["destination"]


def test_navigation_activity_does_not_appear_in_security_audit_log(admin) -> None:
    client, csrf, _ = admin
    client.post(
        "/api/audit/navigation",
        json={"destination": "about"},
        headers={"X-CSRF-Token": csrf},
    )
    events = client.get("/api/audit").json()
    assert not any(e.get("action") == "about" for e in events)
    assert not any("navigation" in e.get("action", "") for e in events)


# ---------------------------------------------------------------------------
# issue_local_032 (follow-up): bounded navigation-activity pagination.
# ---------------------------------------------------------------------------


def _seed_navigation_rows(count: int, *, actor_id: int = 1, actor_username: str = "admin") -> None:
    """Directly seed distinct navigation_activity rows (distinct 5-minute
    buckets), bypassing the dedup-by-bucket API to make row counts exact."""
    from app.db import get_connection

    with get_connection() as conn:
        for i in range(count):
            conn.execute(
                "INSERT INTO navigation_activity "
                "(actor_id, actor_username, destination, bucket_started_at, "
                "first_seen_at, last_seen_at, visit_count) "
                "VALUES (?, ?, 'home', datetime('now', ?), datetime('now'), "
                "datetime('now', ?), 1)",
                (actor_id, actor_username, f"-{i} minutes", f"-{i} minutes"),
            )


def test_navigation_pagination_defaults_to_50_per_page(admin) -> None:
    client, _csrf, _ = admin
    _seed_navigation_rows(60)
    body = client.get("/api/audit/navigation").json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["items"]) == 50
    assert body["total"] == 60


def test_navigation_pagination_next_page_has_no_overlap(admin) -> None:
    client, _csrf, _ = admin
    _seed_navigation_rows(60)
    page1 = client.get("/api/audit/navigation", params={"offset": 0, "limit": 50}).json()
    page2 = client.get("/api/audit/navigation", params={"offset": 50, "limit": 50}).json()
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)
    assert len(page2["items"]) == 10


def test_navigation_total_is_capped_at_500_even_with_more_stored(admin) -> None:
    client, _csrf, _ = admin
    _seed_navigation_rows(510)
    body = client.get("/api/audit/navigation").json()
    assert body["total"] == 500


def test_navigation_offset_450_returns_at_most_50(admin) -> None:
    client, _csrf, _ = admin
    _seed_navigation_rows(510)
    body = client.get("/api/audit/navigation", params={"offset": 450, "limit": 50}).json()
    assert len(body["items"]) <= 50


def test_navigation_rejects_limit_over_50(admin) -> None:
    client, _csrf, _ = admin
    resp = client.get("/api/audit/navigation", params={"limit": 51})
    assert resp.status_code == 422


def test_navigation_rejects_offset_over_450(admin) -> None:
    client, _csrf, _ = admin
    resp = client.get("/api/audit/navigation", params={"offset": 451})
    assert resp.status_code == 422


def test_navigation_rejects_negative_offset(admin) -> None:
    client, _csrf, _ = admin
    resp = client.get("/api/audit/navigation", params={"offset": -1})
    assert resp.status_code == 422


def test_navigation_pagination_requires_admin(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "navpager", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login",
            json={"username": "navpager", "password": password},
        )
        resp = member.get("/api/audit/navigation", params={"offset": 0, "limit": 50})
    assert resp.status_code == 403
