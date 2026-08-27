"""Application catalogue listing and server-side team gating."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _seed_teams(request, admin, make_team):
    client, csrf, _ = admin
    for _name in ("Red Team", "Threat Hunting", "Detect and Response"):
        make_team(_name)
    # ``make_team`` seeds via the admin-authenticated shared ``client``. A few
    # tests intentionally exercise the unauthenticated/auth-disabled path using
    # the bare ``client``/``client_no_auth`` fixtures and never request
    # ``admin`` themselves; for those, drop the admin session this fixture
    # established so the caller is observed as anonymous.
    if "admin" not in inspect.signature(request.function).parameters:
        client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})


def _create_member(client, csrf, username, teams):
    """Create a standard user via the admin client; return their password."""
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


def _create_share_user(client, csrf, username, teams, *, self_service=False):
    username = username if "@" in username else f"{username}@example.com"
    response = client.post(
        "/api/users",
        json={"username": username, "role": "user", "teams": teams,
              "apps_server": "apps.example.com", "self_service": self_service},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_app(client, csrf, name, url, teams):
    """Admin-create an application (auto-approved and visible)."""
    resp = client.post(
        "/api/applications",
        json={"name": name, "url": url, "teams": teams},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_statistics_zero_fill_complete_date_range(admin) -> None:
    client, csrf, _ = admin
    app = _seed_app(client, csrf, "One Day App", "https://example.com/one", ["Red Team"])
    _seed_app(client, csrf, "No Activity App", "https://example.com/zero", ["Red Team"])
    from app.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO application_usage_daily "
            "(application_id, usage_date, visitor_key, launch_count) "
            "VALUES (?, date('now'), 'user:1', 3)",
            (app["id"],),
        )
    response = client.get("/api/application-statistics", params={"days": 7})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["trend"]) == 7
    assert len(body["app_trends"]) == 1
    points = body["app_trends"][0]["points"]
    assert len(points) == 7
    assert sum(point["launches"] for point in points) == 3
    assert points[-1]["date"] == datetime.now(timezone.utc).date().isoformat()
    assert [series["name"] for series in body["app_trends"]] == ["One Day App"]
    for days in (30, 90):
        ranged = client.get("/api/application-statistics", params={"days": days})
        assert ranged.status_code == 200
        assert len(ranged.json()["trend"]) == days
        assert len(ranged.json()["app_trends"][0]["points"]) == days


def test_statistics_includes_authorized_alias_visits(admin) -> None:
    client, csrf, _ = admin
    app = _seed_app(client, csrf, "Alias Visited", "https://example.com/av", ["Red Team"])
    other = _seed_app(client, csrf, "Alias Unvisited", "https://example.com/au", ["Red Team"])
    from app.db import get_connection

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO application_alias_usage_daily "
            "(application_id, usage_date, visitor_key, request_count) "
            "VALUES (?, date('now'), 'user:1', 4)",
            (app["id"],),
        )
        conn.execute(
            "INSERT INTO application_alias_usage_daily "
            "(application_id, usage_date, visitor_key, request_count) "
            "VALUES (?, date('now'), 'anonymous', 2)",
            (app["id"],),
        )

    body = client.get("/api/application-statistics", params={"days": 7}).json()
    assert body["alias_visits"] == 6
    assert body["unique_alias_users"] == 1
    assert body["anonymous_alias_visits"] == 2

    rows = {row["name"]: row for row in body["applications"]}
    assert rows["Alias Visited"]["alias_visits"] == 6
    assert rows["Alias Visited"]["unique_alias_users"] == 1
    assert rows["Alias Visited"]["anonymous_alias_visits"] == 2
    assert rows["Alias Unvisited"]["alias_visits"] == 0
    assert rows["Alias Unvisited"]["unique_alias_users"] == 0


def test_clean_install_has_no_applications(admin) -> None:
    # A fresh database starts with no applications (no placeholder seed).
    client, _csrf, _ = admin
    resp = client.get("/api/applications")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_private_alias_is_owner_and_admin_only(admin) -> None:
    client, csrf, _ = admin
    owner = _create_share_user(client, csrf, "private.owner", ["Red Team"], self_service=True)
    other = _create_share_user(client, csrf, "other.user", ["Red Team"])
    with TestClient(client.app) as owner_client:
        login = owner_client.post("/api/auth/login", json={"username": "private.owner@example.com", "password": owner["password"]}).json()
        created = owner_client.post(
            "/api/applications",
            json={"name":"Private Lab","url":"private-lab","url_type":"alias","teams":[],"is_private":True,"apps_server":"apps.example.com","apps_port":"8000"},
            headers={"X-CSRF-Token": login["csrf_token"]},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]
        assert {app["id"] for app in owner_client.get("/api/applications").json()} == {app_id}
    with TestClient(client.app) as other_client:
        other_client.post("/api/auth/login", json={"username":"other.user@example.com","password":other["password"]})
        assert other_client.get("/api/applications").json() == []
        denied = other_client.get(f"/api/auth/proxy-check/{app_id}/private-lab")
        assert denied.status_code == 403
    assert client.get(f"/api/auth/proxy-check/{app_id}/private-lab").status_code == 204


def test_explicit_user_share_and_case_insensitive_resolution(admin) -> None:
    client, csrf, _ = admin
    owner = _create_share_user(client, csrf, "share.owner", ["Red Team"], self_service=True)
    recipient = _create_share_user(client, csrf, "Shared.Person", [])
    with TestClient(client.app) as recipient_client:
        recipient_client.post("/api/auth/login", json={"username":"Shared.Person@example.com","password":recipient["password"]})
        resolved = recipient_client.get("/api/users/resolve", params={"identity":"SHARED-PERSON"})
        assert resolved.status_code == 200
        recipient_id = resolved.json()["id"]
    with TestClient(client.app) as owner_client:
        login = owner_client.post("/api/auth/login", json={"username":"share.owner@example.com","password":owner["password"]}).json()
        created = owner_client.post(
            "/api/applications",
            json={"name":"Direct Share","url":"direct-share","url_type":"alias","teams":[],"shared_user_ids":[recipient_id],"apps_server":"apps.example.com","apps_port":"8000"},
            headers={"X-CSRF-Token":login["csrf_token"]},
        )
        assert created.status_code == 201, created.text
    with TestClient(client.app) as recipient_client:
        recipient_client.post("/api/auth/login", json={"username":"Shared.Person@example.com","password":recipient["password"]})
        assert [app["name"] for app in recipient_client.get("/api/applications").json()] == ["Direct Share"]


def test_member_sees_only_their_team_apps(admin) -> None:
    client, csrf, _ = admin
    _seed_app(client, csrf, "Red Tool", "https://example.com/red", ["Red Team"])
    _seed_app(client, csrf, "Hunt Tool", "https://example.com/hunt", ["Threat Hunting"])
    password = _create_member(client, csrf, "redder", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "redder", "password": password}
        )
        names = {a["name"] for a in member.get("/api/applications").json()}
    assert names == {"Red Tool"}


def test_member_with_no_teams_sees_nothing(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "loner", [])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "loner", "password": password}
        )
        resp = member.get("/api/applications")
    assert resp.status_code == 200
    assert resp.json() == []


def test_member_team_query_returns_team_apps(admin) -> None:
    client, csrf, _ = admin
    _seed_app(client, csrf, "Hunt One", "https://example.com/h1", ["Threat Hunting"])
    _seed_app(
        client,
        csrf,
        "Shared Case",
        "https://example.com/case",
        ["Detect and Response", "Threat Hunting"],
    )
    _seed_app(client, csrf, "Red Only", "https://example.com/red", ["Red Team"])
    password = _create_member(client, csrf, "hunter", ["Threat Hunting"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "hunter", "password": password}
        )
        resp = member.get("/api/applications", params={"team": "Threat Hunting"})
    assert resp.status_code == 200, resp.text
    names = {a["name"] for a in resp.json()}
    assert names == {"Hunt One", "Shared Case"}


def test_member_cannot_view_other_team_section(admin) -> None:
    client, csrf, _ = admin
    _seed_app(client, csrf, "Hunt Tool", "https://example.com/hunt", ["Threat Hunting"])
    password = _create_member(client, csrf, "redder2", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "redder2", "password": password}
        )
        resp = member.get("/api/applications", params={"team": "Threat Hunting"})
    assert resp.status_code == 403, resp.text


def test_team_query_keeps_visibility_but_reports_publisher_team(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "publisher", ["Red Team"])
    with TestClient(client.app) as publisher:
        login = publisher.post(
            "/api/auth/login", json={"username": "publisher", "password": password}
        )
        pcsrf = login.json()["csrf_token"]
        created = publisher.post(
            "/api/applications",
            json={
                "name": "Cross Shared",
                "url": "https://example.com/cross",
                "teams": ["Threat Hunting"],
            },
            headers={"X-CSRF-Token": pcsrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

    approved = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200, approved.text

    resp = client.get("/api/applications", params={"team": "Threat Hunting"})
    assert resp.status_code == 200, resp.text
    app = next(a for a in resp.json() if a["id"] == app_id)
    assert app["teams"] == ["Threat Hunting"]
    assert app["publisher_team"] == "Red Team"
    assert app["created_by"] == "publisher@example.com"


def test_publisher_team_section_respects_app_visibility(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "redpublisher", ["Red Team"])
    with TestClient(client.app) as publisher:
        login = publisher.post(
            "/api/auth/login", json={"username": "redpublisher", "password": password}
        )
        pcsrf = login.json()["csrf_token"]
        created = publisher.post(
            "/api/applications",
            json={
                "name": "Red Published Threat Share",
                "url": "https://example.com/red-threat",
                "teams": ["Threat Hunting"],
            },
            headers={"X-CSRF-Token": pcsrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

    approved = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200, approved.text

    viewer_password = _create_member(client, csrf, "threatviewer", ["Threat Hunting"])
    with TestClient(client.app) as viewer:
        viewer.post(
            "/api/auth/login",
            json={"username": "threatviewer", "password": viewer_password},
        )
        home = viewer.get("/api/applications")
        red_section = viewer.get(
            "/api/applications", params={"publisher_team": "Red Team"}
        )
        threat_section = viewer.get(
            "/api/applications", params={"publisher_team": "Threat Hunting"}
        )

    assert home.status_code == 200, home.text
    assert any(a["id"] == app_id for a in home.json())
    assert red_section.status_code == 200, red_section.text
    assert [a["id"] for a in red_section.json()] == [app_id]
    assert threat_section.status_code == 200, threat_section.text
    assert all(a["id"] != app_id for a in threat_section.json())


def test_home_listing_uses_application_sort_order(admin) -> None:
    client, csrf, _ = admin
    _seed_app(client, csrf, "Last", "https://example.com/last", ["Red Team"])
    _seed_app(client, csrf, "First", "https://example.com/first", ["Red Team"])
    apps = client.get("/api/applications").json()
    ids = {app["name"]: app["id"] for app in apps}

    client.patch(
        f"/api/applications/{ids['Last']}",
        json={"sort_order": 20},
        headers={"X-CSRF-Token": csrf},
    )
    client.patch(
        f"/api/applications/{ids['First']}",
        json={"sort_order": 10},
        headers={"X-CSRF-Token": csrf},
    )

    ordered = [app["name"] for app in client.get("/api/applications").json()]
    assert ordered.index("First") < ordered.index("Last")


def test_reorder_applications_bulk_saves_atomically(admin) -> None:
    client, csrf, _ = admin
    a = _seed_app(client, csrf, "Alpha", "https://example.com/a", ["Red Team"])
    b = _seed_app(client, csrf, "Bravo", "https://example.com/b", ["Red Team"])
    c = _seed_app(client, csrf, "Charlie", "https://example.com/c", ["Red Team"])
    ids_before = [a["id"], b["id"], c["id"]]

    resp = client.post(
        "/api/applications/reorder",
        json={
            "groups": [
                {
                    "application_ids": [c["id"], a["id"], b["id"]],
                    "expected_application_ids": ids_before,
                }
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text

    ordered = [
        app["name"]
        for app in client.get("/api/applications").json()
        if app["name"] in ("Alpha", "Bravo", "Charlie")
    ]
    assert ordered == ["Charlie", "Alpha", "Bravo"]


def test_reorder_applications_rejects_stale_expected_order(admin) -> None:
    client, csrf, _ = admin
    a = _seed_app(client, csrf, "StaleA", "https://example.com/sa", ["Red Team"])
    b = _seed_app(client, csrf, "StaleB", "https://example.com/sb", ["Red Team"])

    resp = client.post(
        "/api/applications/reorder",
        json={
            "groups": [
                {
                    "application_ids": [b["id"], a["id"]],
                    "expected_application_ids": [b["id"], a["id"]],  # wrong: actual is [a,b]
                }
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409, resp.text
    # Nothing changed.
    ordered = [
        app["name"]
        for app in client.get("/api/applications").json()
        if app["name"] in ("StaleA", "StaleB")
    ]
    assert ordered == ["StaleA", "StaleB"]


def test_reorder_applications_rejects_duplicate_ids(admin) -> None:
    client, csrf, _ = admin
    a = _seed_app(client, csrf, "DupA", "https://example.com/da", ["Red Team"])

    resp = client.post(
        "/api/applications/reorder",
        json={
            "groups": [
                {
                    "application_ids": [a["id"], a["id"]],
                    "expected_application_ids": [a["id"], a["id"]],
                }
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422, resp.text


def test_reorder_applications_rejects_mismatched_id_sets(admin) -> None:
    client, csrf, _ = admin
    a = _seed_app(client, csrf, "SetA", "https://example.com/seta", ["Red Team"])
    b = _seed_app(client, csrf, "SetB", "https://example.com/setb", ["Red Team"])

    resp = client.post(
        "/api/applications/reorder",
        json={
            "groups": [
                {
                    "application_ids": [a["id"]],
                    "expected_application_ids": [b["id"]],
                }
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422, resp.text


def test_reorder_applications_rejects_unknown_id(admin) -> None:
    client, csrf, _ = admin
    a = _seed_app(client, csrf, "KnownA", "https://example.com/ka", ["Red Team"])

    resp = client.post(
        "/api/applications/reorder",
        json={
            "groups": [
                {
                    "application_ids": [999999],
                    "expected_application_ids": [999999],
                }
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400, resp.text
    _ = a


def test_reorder_applications_rejects_mixed_approval_status(admin) -> None:
    client, csrf, _ = admin
    approved = _seed_app(client, csrf, "MixApproved", "https://example.com/mixa", ["Red Team"])
    password = _create_member(client, csrf, "mixreorder", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "mixreorder", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        pending = member.post(
            "/api/applications",
            json={"name": "MixPending", "url": "https://example.com/mixp", "teams": ["Red Team"]},
            headers={"X-CSRF-Token": mcsrf},
        ).json()
        assert pending["approval_status"] == "pending"

    resp = client.post(
        "/api/applications/reorder",
        json={
            "groups": [
                {
                    "application_ids": [pending["id"], approved["id"]],
                    "expected_application_ids": [pending["id"], approved["id"]],
                }
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400, resp.text
    assert "approval status" in resp.json()["detail"].lower()


def test_reorder_applications_nonadmin_gets_identical_response_for_missing_and_unowned(
    admin,
) -> None:
    """A non-admin must not be able to learn whether an arbitrary application
    id exists (e.g. a private application they cannot otherwise see) by
    comparing the response to a nonexistent id vs. one they simply don't own."""
    client, csrf, _ = admin
    admin_app = _seed_app(client, csrf, "EnumAdmin", "https://example.com/enuma", ["Red Team"])
    password = _create_member(client, csrf, "enumreorder", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "enumreorder", "password": password}
        )
        mcsrf = login.json()["csrf_token"]

        unowned = member.post(
            "/api/applications/reorder",
            json={
                "groups": [
                    {
                        "application_ids": [admin_app["id"]],
                        "expected_application_ids": [admin_app["id"]],
                    }
                ]
            },
            headers={"X-CSRF-Token": mcsrf},
        )
        nonexistent = member.post(
            "/api/applications/reorder",
            json={
                "groups": [
                    {
                        "application_ids": [999999],
                        "expected_application_ids": [999999],
                    }
                ]
            },
            headers={"X-CSRF-Token": mcsrf},
        )
    assert unowned.status_code == nonexistent.status_code == 403
    assert unowned.json()["detail"] == nonexistent.json()["detail"]


def test_reorder_applications_owner_cannot_reorder_others_apps(admin) -> None:
    client, csrf, _ = admin
    admin_app = _seed_app(client, csrf, "AdminOwned", "https://example.com/adm", ["Red Team"])
    password = _create_member(client, csrf, "reorderowner", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "reorderowner", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        own_app = member.post(
            "/api/applications",
            json={"name": "MemberOwned", "url": "https://example.com/memb", "teams": ["Red Team"]},
            headers={"X-CSRF-Token": mcsrf},
        ).json()
        resp = member.post(
            "/api/applications/reorder",
            json={
                "groups": [
                    {
                        "application_ids": [admin_app["id"], own_app["id"]],
                        "expected_application_ids": [admin_app["id"], own_app["id"]],
                    }
                ]
            },
            headers={"X-CSRF-Token": mcsrf},
        )
    assert resp.status_code == 403, resp.text


def test_reorder_applications_requires_csrf(admin) -> None:
    client, _csrf, _ = admin
    a = _seed_app(client, _csrf, "NoCsrfA", "https://example.com/ncsa", ["Red Team"])
    resp = client.post(
        "/api/applications/reorder",
        json={
            "groups": [
                {
                    "application_ids": [a["id"]],
                    "expected_application_ids": [a["id"]],
                }
            ]
        },
    )
    assert resp.status_code == 403, resp.text


def test_reorder_applications_emits_one_audit_event(admin) -> None:
    client, csrf, _ = admin
    a = _seed_app(client, csrf, "AuditA", "https://example.com/auda", ["Red Team"])
    b = _seed_app(client, csrf, "AuditB", "https://example.com/audb", ["Red Team"])
    before = client.get("/api/audit?category=application").json()

    client.post(
        "/api/applications/reorder",
        json={
            "groups": [
                {
                    "application_ids": [b["id"], a["id"]],
                    "expected_application_ids": [a["id"], b["id"]],
                }
            ]
        },
        headers={"X-CSRF-Token": csrf},
    )
    after = client.get("/api/audit?category=application").json()
    reorder_events = [
        e for e in after if e["action"] == "reorder" and e not in before
    ]
    assert len(reorder_events) == 1


def test_publisher_team_section_uses_application_sort_order(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "sortpublisher", ["Red Team"])
    user_id = next(
        u["id"] for u in client.get("/api/users").json() if u["username"] == "sortpublisher@example.com"
    )
    client.patch(
        f"/api/users/{user_id}",
        json={"self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    with TestClient(client.app) as publisher:
        login = publisher.post(
            "/api/auth/login", json={"username": "sortpublisher", "password": password}
        )
        pcsrf = login.json()["csrf_token"]
        first_created = publisher.post(
            "/api/applications",
            json={"name": "Second", "url": "https://example.com/second", "teams": ["Red Team"]},
            headers={"X-CSRF-Token": pcsrf},
        ).json()
        second_created = publisher.post(
            "/api/applications",
            json={"name": "First", "url": "https://example.com/first", "teams": ["Red Team"]},
            headers={"X-CSRF-Token": pcsrf},
        ).json()
        publisher.patch(
            f"/api/applications/{first_created['id']}",
            json={"sort_order": 20},
            headers={"X-CSRF-Token": pcsrf},
        )
        publisher.patch(
            f"/api/applications/{second_created['id']}",
            json={"sort_order": 10},
            headers={"X-CSRF-Token": pcsrf},
        )

    section = client.get("/api/applications", params={"publisher_team": "Red Team"})
    names = [app["name"] for app in section.json()]
    assert names.index("First") < names.index("Second")


def test_unknown_team_returns_404(admin) -> None:
    client, _csrf, _ = admin
    resp = client.get("/api/applications", params={"team": "Nonexistent Team"})
    assert resp.status_code == 404


def test_admin_can_query_any_team(admin) -> None:
    client, csrf, _ = admin
    _seed_app(client, csrf, "Hunt One", "https://example.com/h1", ["Threat Hunting"])
    _seed_app(
        client,
        csrf,
        "Shared Case",
        "https://example.com/case",
        ["Detect and Response", "Threat Hunting"],
    )
    resp = client.get("/api/applications", params={"team": "Threat Hunting"})
    assert resp.status_code == 200, resp.text
    names = {a["name"] for a in resp.json()}
    assert names == {"Hunt One", "Shared Case"}


def test_applications_require_authentication(client: TestClient) -> None:
    # No login performed: the listing must not be readable anonymously.
    assert client.get("/api/applications").status_code == 401


def test_applications_open_when_auth_disabled(client_no_auth: TestClient) -> None:
    # Auth disabled => caller is treated as admin; with no seed the catalogue
    # starts empty and a created app is immediately visible.
    resp = client_no_auth.get("/api/applications")
    assert resp.status_code == 200
    assert resp.json() == []
    client_no_auth.post(
        "/api/applications",
        json={
            "name": "Open Tool",
            "url": "https://example.com/open",
            "teams": ["Red Team"],
        },
    )
    scoped = client_no_auth.get("/api/applications", params={"team": "Red Team"})
    assert scoped.status_code == 200
    assert {a["name"] for a in scoped.json()} == {"Open Tool"}


# ---------------------------------------------------------------------------
# Administrator CRUD (Phase 4)
# ---------------------------------------------------------------------------


def _create_app(client, csrf, **overrides):
    payload = {
        "name": "New Tool",
        "url": "https://example.com/new-tool",
        "description": "A brand new tool.",
        "teams": ["Red Team"],
    }
    payload.update(overrides)
    return client.post(
        "/api/applications", json=payload, headers={"X-CSRF-Token": csrf}
    )


def test_admin_create_application(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(client, csrf)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "New Tool"
    assert body["teams"] == ["Red Team"]
    assert body["is_active"] is True
    # The new app is now visible to a Red Team member.
    password = _create_member(client, csrf, "newredder", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "newredder", "password": password}
        )
        names = {a["name"] for a in member.get("/api/applications").json()}
    assert "New Tool" in names


def test_create_rejects_non_http_url(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(client, csrf, url="javascript:alert(1)")
    assert resp.status_code == 422


def test_create_rejects_unknown_team(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(client, csrf, teams=["Nonexistent Team"])
    assert resp.status_code == 400


def test_create_requires_csrf(admin) -> None:
    client, _csrf, _ = admin
    resp = client.post(
        "/api/applications",
        json={"name": "No CSRF", "url": "https://example.com/x", "teams": []},
    )
    assert resp.status_code == 403


def test_admin_update_application(admin) -> None:
    client, csrf, _ = admin
    app_id = _create_app(client, csrf).json()["id"]
    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"is_active": False, "teams": ["Threat Hunting"], "name": "Renamed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["is_active"] is False
    assert body["teams"] == ["Threat Hunting"]


def test_update_missing_application_returns_404(admin) -> None:
    client, csrf, _ = admin
    resp = client.patch(
        "/api/applications/999999",
        json={"name": "Ghost"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 404


def test_inactive_app_hidden_from_members_but_visible_to_admin(admin) -> None:
    client, csrf, _ = admin
    _create_app(client, csrf, name="Hidden Tool", is_active=False, teams=["Red Team"])

    password = _create_member(client, csrf, "redder3", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "redder3", "password": password}
        )
        visible = {a["name"] for a in member.get("/api/applications").json()}
        scoped = member.get("/api/applications", params={"team": "Red Team"})
    assert "Hidden Tool" not in visible
    assert "Hidden Tool" not in {a["name"] for a in scoped.json()}

    # Admin default listing also excludes it; include_inactive reveals it.
    default = {a["name"] for a in client.get("/api/applications").json()}
    assert "Hidden Tool" not in default
    with_inactive = client.get(
        "/api/applications", params={"include_inactive": "true"}
    )
    assert "Hidden Tool" in {a["name"] for a in with_inactive.json()}


def test_member_cannot_request_inactive(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "redder4", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "redder4", "password": password}
        )
        resp = member.get(
            "/api/applications", params={"include_inactive": "true"}
        )
    assert resp.status_code == 403


def test_non_admin_create_empty_teams_rejected(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "redder5", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "redder5", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        resp = member.post(
            "/api/applications",
            json={"name": "Sneaky", "url": "https://example.com/s", "teams": []},
            headers={"X-CSRF-Token": member_csrf},
        )
    # Members must scope a submission to at least one team.
    assert resp.status_code == 400


def test_non_admin_can_share_with_other_team(admin) -> None:
    # Any signed-in user may share an application with any team (not just their
    # own). The submission is still queued for approval (pending).
    client, csrf, _ = admin
    password = _create_member(client, csrf, "redder6", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "redder6", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        resp = member.post(
            "/api/applications",
            json={
                "name": "Cross Team",
                "url": "https://example.com/x",
                "teams": ["Threat Hunting"],
            },
            headers={"X-CSRF-Token": member_csrf},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["teams"] == ["Threat Hunting"]
    assert body["approval_status"] == "pending"


def test_non_admin_can_add_foreign_team_on_update(admin) -> None:
    # A non-admin owner may broaden an app to another team on edit; the
    # substantive change re-queues it for approval.
    client, csrf, _ = admin
    password = _create_member(client, csrf, "redder7", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "redder7", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "Shareable",
                "url": "https://example.com/share",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": member_csrf},
        ).json()["id"]
        resp = member.patch(
            f"/api/applications/{app_id}",
            json={"teams": ["Red Team", "Threat Hunting"]},
            headers={"X-CSRF-Token": member_csrf},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["teams"]) == ["Red Team", "Threat Hunting"]
    assert body["approval_status"] == "pending"


def test_member_submission_is_pending_and_hidden(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "subm1", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "subm1", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        created = member.post(
            "/api/applications",
            json={
                "name": "Pending Tool",
                "url": "https://example.com/pending",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": member_csrf},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["approval_status"] == "pending"
        assert body["created_by"] == "subm1@example.com"

        # Hidden from the member's own catalogue until approved...
        home = {a["name"] for a in member.get("/api/applications").json()}
        assert "Pending Tool" not in home
        # ...but visible in their own management list with its status.
        mine = member.get("/api/applications/mine").json()
        mine_by_name = {a["name"]: a for a in mine}
        assert mine_by_name["Pending Tool"]["approval_status"] == "pending"
        assert mine_by_name["Pending Tool"]["created_by"] == "subm1@example.com"

    # The admin sees it in the management view and on Home it stays hidden.
    manage = {a["name"] for a in client.get("/api/applications/manage").json()}
    assert "Pending Tool" in manage
    assert "Pending Tool" not in {
        a["name"] for a in client.get("/api/applications").json()
    }


def test_self_service_member_submission_auto_approved(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "selfsvc", ["Red Team"])
    # Grant self-service so the submission bypasses approval.
    users = client.get("/api/users").json()
    uid = next(u["id"] for u in users if u["username"] == "selfsvc@example.com")
    patched = client.patch(
        f"/api/users/{uid}",
        json={"self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["self_service"] is True

    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "selfsvc", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        created = member.post(
            "/api/applications",
            json={
                "name": "Auto Tool",
                "url": "https://example.com/auto",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": member_csrf},
        )
        assert created.status_code == 201, created.text
        assert created.json()["approval_status"] == "approved"
        # Immediately visible to the member on Home.
        assert "Auto Tool" in {
            a["name"] for a in member.get("/api/applications").json()
        }


def test_admin_approve_makes_app_visible(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "subm2", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "subm2", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "To Approve",
                "url": "https://example.com/approve",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": member_csrf},
        ).json()["id"]

    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["approval_status"] == "approved"

    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "subm2", "password": password}
        )
        assert "To Approve" in {
            a["name"] for a in member.get("/api/applications").json()
        }


def test_admin_reject_keeps_app_hidden_but_listed(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "subm3", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "subm3", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "To Reject",
                "url": "https://example.com/reject",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": member_csrf},
        ).json()["id"]

    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "rejected"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    # Rejected apps are retained (not deleted) and remain in management views.
    manage = {a["name"]: a for a in client.get("/api/applications/manage").json()}
    assert manage["To Reject"]["approval_status"] == "rejected"
    assert "To Reject" not in {
        a["name"] for a in client.get("/api/applications").json()
    }


def test_member_cannot_set_approval_status(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "subm4", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "subm4", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "Self Approve",
                "url": "https://example.com/self",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": member_csrf},
        ).json()["id"]
        # The owner may not approve their own submission.
        resp = member.patch(
            f"/api/applications/{app_id}",
            json={"approval_status": "approved"},
            headers={"X-CSRF-Token": member_csrf},
        )
    assert resp.status_code == 403


def test_owner_substantive_edit_resubmits_to_pending(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "subm5", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "subm5", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "Editable",
                "url": "https://example.com/editable",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": member_csrf},
        ).json()["id"]

    # Admin approves it.
    client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )

    # A substantive owner edit knocks a non-self-service app back to pending.
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "subm5", "password": password}
        )
        member_csrf = login.json()["csrf_token"]
        resp = member.patch(
            f"/api/applications/{app_id}",
            json={"name": "Editable v2"},
            headers={"X-CSRF-Token": member_csrf},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["approval_status"] == "pending"


def test_owner_can_delete_but_not_foreign(admin) -> None:
    client, csrf, _ = admin
    pw_a = _create_member(client, csrf, "owner_a", ["Red Team"])
    pw_b = _create_member(client, csrf, "owner_b", ["Red Team"])
    with TestClient(client.app) as member_a:
        login = member_a.post(
            "/api/auth/login", json={"username": "owner_a", "password": pw_a}
        )
        a_csrf = login.json()["csrf_token"]
        app_id = member_a.post(
            "/api/applications",
            json={
                "name": "A's App",
                "url": "https://example.com/a",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": a_csrf},
        ).json()["id"]

    # Another member may not delete it.
    with TestClient(client.app) as member_b:
        login = member_b.post(
            "/api/auth/login", json={"username": "owner_b", "password": pw_b}
        )
        b_csrf = login.json()["csrf_token"]
        forbidden = member_b.request(
            "DELETE",
            f"/api/applications/{app_id}",
            headers={"X-CSRF-Token": b_csrf},
        )
    assert forbidden.status_code == 403

    # The owner can.
    with TestClient(client.app) as member_a:
        login = member_a.post(
            "/api/auth/login", json={"username": "owner_a", "password": pw_a}
        )
        a_csrf = login.json()["csrf_token"]
        ok = member_a.request(
            "DELETE",
            f"/api/applications/{app_id}",
            headers={"X-CSRF-Token": a_csrf},
        )
    assert ok.status_code == 200


def test_manage_endpoint_requires_admin(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "peeker", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "peeker", "password": password}
        )
        resp = member.get("/api/applications/manage")
    assert resp.status_code == 403


def test_alias_application_round_trips_verbatim(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(
        client,
        csrf,
        name="Local Grafana",
        url="/grafana",
        url_type="alias",
        teams=["Red Team"],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["url_type"] == "alias"
    # Leading slash is stripped; the relative path is stored verbatim.
    assert body["url"] == "grafana"


def test_alias_accepts_underscore(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(client, csrf, url="wiki_home", url_type="alias")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["url"] == "wiki_home"
    assert body["url_type"] == "alias"


def test_alias_auth_can_be_disabled_on_create(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(
        client,
        csrf,
        url="publicstatus",
        url_type="alias",
        apps_server="apps.example.com",
        apps_protocol="https",
        apps_port="8080",
        apps_path="dashboard",
        alias_auth_required=False,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["alias_auth_required"] is False
    assert body["apps_server"] == "apps.example.com"
    assert body["apps_protocol"] == "https"
    assert body["apps_port"] == "8080"
    assert body["apps_path"] == "/dashboard"
    assert body["pending_alias_auth_required"] is None


def test_alias_rewrite_root_persists_on_create_and_edit(admin) -> None:
    """The per-alias rewrite-root flag round-trips through create + edit and
    marks the app for a proxy re-push when toggled by an admin (immediate)."""
    client, csrf, _ = admin
    resp = _create_app(
        client,
        csrf,
        url="rooter",
        url_type="alias",
        apps_server="apps.example.com",
        apps_port="8080",
        apps_rewrite_root=True,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["apps_rewrite_root"] is True
    app_id = body["id"]

    # Toggle it off via edit.
    edited = client.patch(
        f"/api/applications/{app_id}",
        json={"apps_rewrite_root": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["apps_rewrite_root"] is False


def test_non_self_service_rewrite_root_change_is_staged(admin) -> None:
    """A non-self-service owner toggling rewrite-root on an approved alias
    stages the change (pending_apps_rewrite_root) instead of applying it live,
    and an admin approval applies it."""
    client, csrf, _ = admin
    password = _create_member(client, csrf, "rootstager", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "rootstager", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        created = member.post(
            "/api/applications",
            json={
                "name": "Root Alias",
                "url": "rootalias",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

    approved = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200, approved.text

    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "rootstager", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        staged = member.patch(
            f"/api/applications/{app_id}",
            json={"apps_rewrite_root": True},
            headers={"X-CSRF-Token": mcsrf},
        )
        assert staged.status_code == 200, staged.text
        # Live value unchanged; the change is staged pending review.
        assert staged.json()["apps_rewrite_root"] is False
        assert staged.json()["pending_apps_rewrite_root"] is True

    approved2 = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved2.status_code == 200, approved2.text
    assert approved2.json()["apps_rewrite_root"] is True
    assert approved2.json()["pending_apps_rewrite_root"] is None


def test_non_self_service_alias_auth_change_is_staged(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "authstager", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "authstager", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        created = member.post(
            "/api/applications",
            json={
                "name": "Member Alias",
                "url": "memberalias",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

    approved = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200, approved.text

    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "authstager", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        staged = member.patch(
            f"/api/applications/{app_id}",
            json={"alias_auth_required": False},
            headers={"X-CSRF-Token": mcsrf},
        )
    assert staged.status_code == 200, staged.text
    body = staged.json()
    assert body["alias_auth_required"] is True
    assert body["pending_alias_auth_required"] is False

    applied = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["alias_auth_required"] is False
    assert body["pending_alias_auth_required"] is None


def test_pass_authenticated_user_defaults_off_and_round_trips(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(
        client,
        csrf,
        url="withuserheader",
        url_type="alias",
        apps_server="apps.example.com",
        apps_port="8080",
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["pass_authenticated_user"] is False

    resp2 = _create_app(
        client,
        csrf,
        url="withuserheader2",
        url_type="alias",
        apps_server="apps.example.com",
        apps_port="8080",
        pass_authenticated_user=True,
    )
    assert resp2.status_code == 201, resp2.text
    body = resp2.json()
    assert body["pass_authenticated_user"] is True
    app_id = body["id"]

    edited = client.patch(
        f"/api/applications/{app_id}",
        json={"pass_authenticated_user": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["pass_authenticated_user"] is False


def test_pass_authenticated_user_rejected_for_public_alias(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(
        client,
        csrf,
        url="publicwithheader",
        url_type="alias",
        apps_server="apps.example.com",
        apps_port="8080",
        alias_auth_required=False,
        pass_authenticated_user=True,
    )
    assert resp.status_code == 400, resp.text


def test_pass_authenticated_user_rejected_for_non_alias(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(
        client,
        csrf,
        url="https://example.com/tool",
        url_type="url",
        pass_authenticated_user=True,
    )
    assert resp.status_code == 400, resp.text


def test_pass_authenticated_user_rejected_when_auth_disabled(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENABLE_AUTH", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        from app.main import create_app

        with TestClient(create_app()) as client:
            resp = _create_app(
                client,
                "",
                url="noauthheader",
                url_type="alias",
                apps_server="apps.example.com",
                apps_port="8080",
                pass_authenticated_user=True,
            )
            assert resp.status_code == 400, resp.text
    finally:
        monkeypatch.delenv("APP_ENABLE_AUTH", raising=False)
        get_settings.cache_clear()


def test_disabling_alias_auth_rejected_while_header_still_enabled(admin) -> None:
    client, csrf, _ = admin
    created = _create_app(
        client,
        csrf,
        url="headerthenauthoff",
        url_type="alias",
        apps_server="apps.example.com",
        apps_port="8080",
        pass_authenticated_user=True,
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"alias_auth_required": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400, resp.text


def test_non_self_service_pass_authenticated_user_change_is_staged(admin) -> None:
    client, csrf, _ = admin
    password = _create_member(client, csrf, "headerstager", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "headerstager", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        created = member.post(
            "/api/applications",
            json={
                "name": "Header Alias",
                "url": "headeralias",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

    approved = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200, approved.text

    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "headerstager", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        staged = member.patch(
            f"/api/applications/{app_id}",
            json={"pass_authenticated_user": True},
            headers={"X-CSRF-Token": mcsrf},
        )
    assert staged.status_code == 200, staged.text
    body = staged.json()
    # Live value unchanged; the change is staged pending review.
    assert body["pass_authenticated_user"] is False
    assert body["pending_pass_authenticated_user"] is True

    applied = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["pass_authenticated_user"] is True
    assert body["pending_pass_authenticated_user"] is None


def test_staged_pass_authenticated_user_never_applied_if_auth_disabled_meanwhile(
    admin,
) -> None:
    """A staged enable request must never silently start exposing identity if
    alias authentication was disabled on the live app between the request and
    the admin's approval."""
    client, csrf, _ = admin
    password = _create_member(client, csrf, "clampstager", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "clampstager", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        created = member.post(
            "/api/applications",
            json={
                "name": "Clamp Alias",
                "url": "clampalias",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

    client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )

    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "clampstager", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        member.patch(
            f"/api/applications/{app_id}",
            json={"pass_authenticated_user": True},
            headers={"X-CSRF-Token": mcsrf},
        )

    # Admin disables alias authentication live before approving the staged
    # header request.
    client.patch(
        f"/api/applications/{app_id}",
        json={"alias_auth_required": False},
        headers={"X-CSRF-Token": csrf},
    )
    approved = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["pass_authenticated_user"] is False
    assert body["pending_pass_authenticated_user"] is None


def test_non_self_service_owner_cannot_even_stage_disabling_auth_while_header_is_live(
    admin,
) -> None:
    """The request-time invariant (pass_authenticated_user implies alias auth)
    is enforced against the live state before staging is even considered, so a
    non-self-service owner cannot submit a disable request that would leave
    the header on with authentication off -- there is nothing to clamp later
    because the inconsistent state can never be persisted as a pending value."""
    client, csrf, _ = admin
    password = _create_member(client, csrf, "reverseclamp", ["Red Team"])
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "reverseclamp", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        created = member.post(
            "/api/applications",
            json={
                "name": "Reverse Clamp Alias",
                "url": "reverseclampalias",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

    client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    # Admin enables identity forwarding live (immediate, admin edit).
    enabled = client.patch(
        f"/api/applications/{app_id}",
        json={"pass_authenticated_user": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["pass_authenticated_user"] is True

    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "reverseclamp", "password": password}
        )
        mcsrf = login.json()["csrf_token"]
        rejected = member.patch(
            f"/api/applications/{app_id}",
            json={"alias_auth_required": False},
            headers={"X-CSRF-Token": mcsrf},
        )
    assert rejected.status_code == 400, rejected.text

    # The live app is unchanged: still requiring auth, still forwarding.
    current = client.get("/api/applications/manage").json()
    row = next(a for a in current if a["id"] == app_id)
    assert row["alias_auth_required"] is True
    assert row["pass_authenticated_user"] is True


def test_alias_rejects_path_separators(admin) -> None:
    client, csrf, _ = admin
    # Aliases are a single URL-safe segment: separators are rejected.
    resp = _create_app(
        client, csrf, url="tools/grafana", url_type="alias"
    )
    assert resp.status_code == 422


def test_alias_rejects_over_length(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(client, csrf, url="a" * 31, url_type="alias")
    assert resp.status_code == 422


def test_alias_rejects_protocol_relative(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(
        client, csrf, url="//evil.example.com/x", url_type="alias"
    )
    assert resp.status_code == 422


def test_alias_config_skips_non_alias_app(admin) -> None:
    client, csrf, _ = admin
    created = _create_app(client, csrf)
    assert created.status_code == 201, created.text

    resp = client.get(f"/api/applications/{created.json()['id']}/alias-config")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "skipped"


def test_alias_config_rejects_non_owner(admin) -> None:
    client, csrf, _ = admin
    owner_pw = _create_member(client, csrf, "aliasowner", ["Red Team"])
    other_pw = _create_member(client, csrf, "aliasother", ["Red Team"])
    with TestClient(client.app) as owner:
        login = owner.post(
            "/api/auth/login", json={"username": "aliasowner", "password": owner_pw}
        )
        ocsrf = login.json()["csrf_token"]
        created = owner.post(
            "/api/applications",
            json={
                "name": "Owned Alias",
                "url": "ownedalias",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_server": "apps.example.com",
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": ocsrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]

    with TestClient(client.app) as other:
        other.post(
            "/api/auth/login", json={"username": "aliasother", "password": other_pw}
        )
        resp = other.get(f"/api/applications/{app_id}/alias-config")

    assert resp.status_code == 403


def test_url_mode_rejects_relative_path(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(client, csrf, url="tools/grafana", url_type="url")
    assert resp.status_code == 422


def test_home_listing_exposes_publisher(admin) -> None:
    client, csrf, _ = admin
    _create_app(client, csrf, name="Owned", teams=["Red Team"])
    password = _create_member(client, csrf, "viewer", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "viewer", "password": password}
        )
        apps = member.get("/api/applications").json()
    assert apps, "expected at least one visible app"
    assert any(a["created_by"] == "admin" for a in apps)



def test_admin_delete_application(admin) -> None:
    client, csrf, _ = admin
    app_id = _create_app(client, csrf).json()["id"]
    resp = client.request(
        "DELETE",
        f"/api/applications/{app_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    # A second delete now reports not-found.
    again = client.request(
        "DELETE",
        f"/api/applications/{app_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert again.status_code == 404


def test_admin_can_transfer_application_ownership(admin) -> None:
    client, csrf, _ = admin
    new_owner = client.post(
        "/api/users",
        json={
            "username": "newowner@example.com",
            "role": "user",
            "teams": ["Red Team"],
            "apps_server": "apps.example.com",
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["user"]
    app_id = _create_app(client, csrf, name="Transfer Me").json()["id"]

    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"created_by": new_owner["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created_by"] == "newowner@example.com"


# ---------------------------------------------------------------------------
# Approved applications can no longer be rejected (issue_005)
# ---------------------------------------------------------------------------


def test_reject_approved_application_conflicts(admin) -> None:
    client, csrf, _ = admin
    # An admin-created app is auto-approved.
    created = _create_app(client, csrf, name="Already Approved")
    assert created.json()["approval_status"] == "approved"
    app_id = created.json()["id"]

    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "rejected"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409, resp.text
    # The status is unchanged.
    manage = {a["name"]: a for a in client.get("/api/applications/manage").json()}
    assert manage["Already Approved"]["approval_status"] == "approved"


def test_approved_application_can_still_be_disabled_and_deleted(admin) -> None:
    client, csrf, _ = admin
    app_id = _create_app(client, csrf, name="Approved Toggle").json()["id"]

    disabled = client.patch(
        f"/api/applications/{app_id}",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["is_active"] is False
    assert disabled.json()["approval_status"] == "approved"

    deleted = client.request(
        "DELETE",
        f"/api/applications/{app_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200


# ---------------------------------------------------------------------------
# Inline (uploaded) logo data URIs (issue_005)
# ---------------------------------------------------------------------------

_SMALL_PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMBAQDJ"
    "/1Z/AAAAAElFTkSuQmCC"
)


def test_create_accepts_small_png_data_uri(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(client, csrf, name="With Logo", icon_url=_SMALL_PNG_DATA_URI)
    assert resp.status_code == 201, resp.text
    assert resp.json()["icon_url"] == _SMALL_PNG_DATA_URI


def test_create_rejects_svg_data_uri(admin) -> None:
    client, csrf, _ = admin
    svg = "data:image/svg+xml;base64,PHN2Zy8+"
    resp = _create_app(client, csrf, icon_url=svg)
    assert resp.status_code == 422


def test_create_rejects_html_data_uri(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(client, csrf, icon_url="data:text/html;base64,PGgxPg==")
    assert resp.status_code == 422


def test_create_rejects_oversized_logo_data_uri(admin) -> None:
    client, csrf, _ = admin
    # Base64 payload large enough to exceed the decoded-size cap.
    oversized = "data:image/png;base64," + ("A" * 120_000)
    resp = _create_app(client, csrf, icon_url=oversized)
    assert resp.status_code == 422


def test_create_still_accepts_absolute_icon_url(admin) -> None:
    client, csrf, _ = admin
    resp = _create_app(
        client, csrf, icon_url="https://example.com/icon.png"
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["icon_url"] == "https://example.com/icon.png"


def test_create_accepts_relative_default_logo(admin) -> None:
    # Regression: the frontend assigns a bundled default logo as a relative
    # path (e.g. "logos/red-team-2.svg") when none is uploaded. This must be
    # accepted (previously rejected with 422).
    client, csrf, _ = admin
    resp = _create_app(client, csrf, name="Defaulted", icon_url="logos/red-team-2.svg")
    assert resp.status_code == 201, resp.text
    assert resp.json()["icon_url"] == "logos/red-team-2.svg"


def test_update_accepts_relative_default_logo(admin) -> None:
    client, csrf, _ = admin
    app_id = _create_app(client, csrf, name="To Relogo").json()["id"]
    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"icon_url": "logos/generic-1.svg"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["icon_url"] == "logos/generic-1.svg"


def test_create_rejects_logo_path_traversal(admin) -> None:
    # The relative-logo allow-list is intentionally narrow: only the bundled
    # catalogue shape is accepted, never an arbitrary relative path.
    client, csrf, _ = admin
    for bad in (
        "logos/../secret.svg",
        "/logos/generic-1.svg",
        "logos//generic-1.svg",
        "logos/generic-1.png",
        "logos/generic-4.svg",
        "logos/Generic-1.svg",
        "logos/a/b-1.svg",
        "logos/generic.svg",
    ):
        resp = _create_app(client, csrf, icon_url=bad)
        assert resp.status_code == 422, f"expected 422 for {bad!r}, got {resp.status_code}"


# ---------------------------------------------------------------------------
# issue_021: apps-server reference resolution (account apps_server is a
# template-name reference set at user creation; it resolves to the real host
# of a live, owned apps-server server at alias-push time).
# ---------------------------------------------------------------------------


def test_resolve_user_apps_server_host_prefers_creation_template(admin) -> None:
    from app.db import get_connection
    from app import repository
    from app.routers.applications import resolve_user_apps_server_host

    client, csrf, _ = admin
    owner = client.post(
        "/api/users",
        json={
            "username": "resolver@example.com", "role": "user", "teams": [],
            "apps_server": "AppsTemplate",
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["user"]

    with get_connection() as conn:
        other_template = repository.create_server_template(
            conn, vmid=9001, name="OtherTemplate", kind="lxc",
            is_apps_server=True,
        )
        creation_template = repository.create_server_template(
            conn, vmid=9002, name="AppsTemplate", kind="lxc",
            is_apps_server=True,
        )
        # Alphabetically first, but NOT the template referenced at creation.
        repository.create_user_server(
            conn, user_id=owner["id"], name="aaa-other",
            hostname="other.internal", template_id=other_template["id"],
            template_name=other_template["name"], kind="lxc", status="created",
        )
        # Alphabetically last, but IS the template referenced at creation.
        repository.create_user_server(
            conn, user_id=owner["id"], name="zzz-target",
            hostname="target.internal", template_id=creation_template["id"],
            template_name=creation_template["name"], kind="lxc",
            status="created",
        )
        owner_row = repository.get_user_by_id(conn, owner["id"])
        host = resolve_user_apps_server_host(conn, owner_row)
    assert host == "target.internal"


def test_resolve_user_apps_server_host_falls_back_to_first_then_literal(
    admin,
) -> None:
    from app.db import get_connection
    from app import repository
    from app.routers.applications import resolve_user_apps_server_host

    client, csrf, _ = admin
    owner = client.post(
        "/api/users",
        json={
            "username": "resolver2@example.com", "role": "user", "teams": [],
            "apps_server": "no-such-template",
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["user"]

    with get_connection() as conn:
        owner_row = repository.get_user_by_id(conn, owner["id"])
        # No apps-server servers yet: the literal reference is best effort.
        assert (
            resolve_user_apps_server_host(conn, owner_row)
            == "no-such-template"
        )

        template = repository.create_server_template(
            conn, vmid=9003, name="SomeTemplate", kind="lxc",
            is_apps_server=True,
        )
        repository.create_user_server(
            conn, user_id=owner["id"], name="first-server",
            hostname="first.internal", template_id=template["id"],
            template_name=template["name"], kind="lxc", status="created",
        )
        owner_row = repository.get_user_by_id(conn, owner["id"])
        # Reference doesn't match any template: falls back to the first
        # apps-server server.
        assert (
            resolve_user_apps_server_host(conn, owner_row)
            == "first.internal"
        )


def test_resolve_user_apps_server_host_literal_matches_a_candidate_host(
    admin,
) -> None:
    """When the admin typed a real hostname/IP directly (not a template
    name) that happens to match one of the owner's servers, prefer it over
    the alphabetically-first server."""
    from app.db import get_connection
    from app import repository
    from app.routers.applications import resolve_user_apps_server_host

    client, csrf, _ = admin
    owner = client.post(
        "/api/users",
        json={
            "username": "resolver3@example.com", "role": "user", "teams": [],
            "apps_server": "second.internal",
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["user"]

    with get_connection() as conn:
        template = repository.create_server_template(
            conn, vmid=9004, name="SomeTemplate", kind="lxc",
            is_apps_server=True,
        )
        # Alphabetically first, but not the referenced literal host.
        repository.create_user_server(
            conn, user_id=owner["id"], name="aaa-first",
            hostname="first.internal", template_id=template["id"],
            template_name=template["name"], kind="lxc", status="created",
        )
        # Alphabetically last, but its host matches the literal reference.
        repository.create_user_server(
            conn, user_id=owner["id"], name="zzz-second",
            hostname="second.internal", template_id=template["id"],
            template_name=template["name"], kind="lxc", status="created",
        )
        owner_row = repository.get_user_by_id(conn, owner["id"])
        host = resolve_user_apps_server_host(conn, owner_row)
    assert host == "second.internal"


def test_resolve_user_apps_server_host_none_owner_returns_empty() -> None:
    from app.routers.applications import resolve_user_apps_server_host

    assert resolve_user_apps_server_host(None, None) == ""


# --- Embedded applications (issue_local_029) ------------------------------


def _create_admin_alias(client, csrf, name: str, alias: str, teams=None) -> int:
    """Create an approved alias application (owned by the admin) and return its
    id. Embedded apps must reference an existing owner alias by its slug."""
    resp = client.post(
        "/api/applications",
        json={
            "name": name,
            "url": alias,
            "url_type": "alias",
            "teams": teams if teams is not None else ["Red Team"],
            "apps_server": "apps.internal",
            "apps_protocol": "http",
            "apps_port": "9000",
            "apps_path": "/",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_embedded_app_frames_existing_alias(admin) -> None:
    """An embedded app is created referencing an existing owner alias slug,
    stores that slug as its url, follows team visibility, and never itself
    touches nginx (it frames the alias, which owns the proxy config)."""
    client, csrf, _ = admin
    _create_admin_alias(client, csrf, "Coder Alias", "coder-app")
    created = client.post(
        "/api/applications",
        json={
            "name": "Coder Embed",
            "url": "coder-app",
            "url_type": "embedded",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["url_type"] == "embedded"
    assert body["url"] == "coder-app"
    app_id = body["id"]

    # A member of the shared team sees it; a member of another team does not.
    member_pw = _create_member(client, csrf, "embuser", ["Red Team"])
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login", json={"username": "embuser", "password": member_pw}
        )
        names = {a["name"] for a in member.get("/api/applications").json()}
    assert "Coder Embed" in names

    outsider_pw = _create_member(client, csrf, "embout", ["Threat Hunting"])
    with TestClient(client.app) as outsider:
        outsider.post(
            "/api/auth/login", json={"username": "embout", "password": outsider_pw}
        )
        names = {a["name"] for a in outsider.get("/api/applications").json()}
    assert "Coder Embed" not in names

    # The embedded app itself is excluded from nginx alias handling.
    cfg = client.get(f"/api/applications/{app_id}/alias-config")
    assert cfg.status_code == 200, cfg.text
    assert cfg.json()["status"] == "skipped"


def test_embedded_app_can_be_private(admin) -> None:
    """An 'Embedded App (private)' referencing the owner's alias is allowed
    (mediated behind login) and is owner/admin-only."""
    client, csrf, _ = admin
    owner = _create_share_user(client, csrf, "emb.owner", ["Red Team"], self_service=True)
    other = _create_share_user(client, csrf, "emb.other", ["Red Team"])
    with TestClient(client.app) as owner_client:
        login = owner_client.post(
            "/api/auth/login",
            json={"username": "emb.owner@example.com", "password": owner["password"]},
        ).json()
        owner_csrf = login["csrf_token"]
        # The owner first creates the alias the embedded app will frame.
        alias_resp = owner_client.post(
            "/api/applications",
            json={
                "name": "Owner Alias",
                "url": "owner-alias",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_server": "apps.internal",
                "apps_protocol": "http",
                "apps_port": "8080",
                "apps_path": "/",
            },
            headers={"X-CSRF-Token": owner_csrf},
        )
        assert alias_resp.status_code == 201, alias_resp.text
        created = owner_client.post(
            "/api/applications",
            json={
                "name": "Private Embed",
                "url": "owner-alias",
                "url_type": "embedded",
                "teams": [],
                "is_private": True,
            },
            headers={"X-CSRF-Token": owner_csrf},
        )
        assert created.status_code == 201, created.text
        app_id = created.json()["id"]
        assert {a["id"] for a in owner_client.get("/api/applications").json()} >= {app_id}
    with TestClient(client.app) as other_client:
        other_client.post(
            "/api/auth/login",
            json={"username": "emb.other@example.com", "password": other["password"]},
        )
        assert all(
            a["id"] != app_id for a in other_client.get("/api/applications").json()
        )


def test_embedded_app_rejects_nonexistent_alias(admin) -> None:
    """An embedded app referencing an alias that does not exist is rejected."""
    client, csrf, _ = admin
    resp = client.post(
        "/api/applications",
        json={
            "name": "Ghost Embed",
            "url": "no-such-alias",
            "url_type": "embedded",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400, resp.text
    assert "existing" in resp.json()["detail"].lower()


def test_embedded_app_rejects_alias_owned_by_someone_else(admin) -> None:
    """An embedded app can only frame the OWNER's own alias: referencing an
    alias owned by a different user is rejected."""
    client, csrf, _ = admin
    # Admin owns this alias.
    _create_admin_alias(client, csrf, "Admin Alias", "admin-only-alias")
    # A self-service user cannot reference the admin's alias from their own
    # embedded app.
    owner = _create_share_user(client, csrf, "emb.thief", ["Red Team"], self_service=True)
    with TestClient(client.app) as owner_client:
        login = owner_client.post(
            "/api/auth/login",
            json={"username": "emb.thief@example.com", "password": owner["password"]},
        ).json()
        resp = owner_client.post(
            "/api/applications",
            json={
                "name": "Stolen Embed",
                "url": "admin-only-alias",
                "url_type": "embedded",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": login["csrf_token"]},
        )
    assert resp.status_code == 400, resp.text
    assert "existing" in resp.json()["detail"].lower()


def test_embedded_metadata_edit_allowed_when_alias_removed(admin) -> None:
    """A metadata-only edit (e.g. disabling) an embedded app succeeds even after
    the referenced alias is gone: enforcement only runs when the source (url /
    url_type / owner) changes. The stale reference is surfaced in the UI card."""
    from app.db import get_connection

    client, csrf, _ = admin
    alias_id = _create_admin_alias(client, csrf, "Temp Alias", "temp-alias")
    created = client.post(
        "/api/applications",
        json={
            "name": "Framing Temp",
            "url": "temp-alias",
            "url_type": "embedded",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    # Remove the referenced alias entirely, orphaning the embedded reference.
    with get_connection() as conn:
        conn.execute("DELETE FROM applications WHERE id = ?", (alias_id,))

    # A metadata-only edit that does not touch url/url_type must still succeed.
    ok = client.patch(
        f"/api/applications/{app_id}",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["is_active"] is False


def test_embedded_app_edit_enforces_alias_exists(admin) -> None:
    """Editing an embedded app to reference a non-existent alias is rejected;
    switching to another existing owner alias succeeds."""
    client, csrf, _ = admin
    _create_admin_alias(client, csrf, "Alias One", "alias-one")
    _create_admin_alias(client, csrf, "Alias Two", "alias-two")
    created = client.post(
        "/api/applications",
        json={
            "name": "Editable Embed",
            "url": "alias-one",
            "url_type": "embedded",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    rejected = client.patch(
        f"/api/applications/{app_id}",
        json={"url": "does-not-exist", "url_type": "embedded"},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 400, rejected.text

    ok = client.patch(
        f"/api/applications/{app_id}",
        json={"url": "alias-two", "url_type": "embedded"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["url"] == "alias-two"


def test_embedded_app_rejects_inactive_or_unapproved_alias(admin) -> None:
    """An embedded app cannot frame an alias that is disabled or not yet
    approved, matching the active+approved backend contract."""
    from app.db import get_connection

    client, csrf, _ = admin
    alias_id = _create_admin_alias(client, csrf, "Pending Alias", "pending-alias")
    # Disable the alias, then try to frame it.
    with get_connection() as conn:
        conn.execute(
            "UPDATE applications SET is_active = 0 WHERE id = ?", (alias_id,)
        )
    resp = client.post(
        "/api/applications",
        json={
            "name": "Frames Disabled",
            "url": "pending-alias",
            "url_type": "embedded",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400, resp.text


def test_embedded_owner_reassignment_requires_new_owner_alias(admin) -> None:
    """When an admin reassigns an embedded app's owner, the referenced alias
    must belong to the NEW owner: reassigning to a user who does not own the
    alias is rejected."""
    client, csrf, _ = admin
    # Admin owns the alias and an embedded app framing it.
    _create_admin_alias(client, csrf, "Admin Alias RA", "admin-alias-ra")
    created = client.post(
        "/api/applications",
        json={
            "name": "Reassign Embed",
            "url": "admin-alias-ra",
            "url_type": "embedded",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    # A different user who does NOT own that alias.
    other = _create_share_user(client, csrf, "ra.other", ["Red Team"])
    other_id = other["user"]["id"]

    rejected = client.patch(
        f"/api/applications/{app_id}",
        json={"created_by": other_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert rejected.status_code == 400, rejected.text


def test_embedded_update_slug_without_url_type_is_validated(admin) -> None:
    """Repointing an embedded app's slug WITHOUT resending url_type is accepted
    (the router validates against the stored type) rather than 422'd."""
    client, csrf, _ = admin
    _create_admin_alias(client, csrf, "Slug A", "slug-a")
    _create_admin_alias(client, csrf, "Slug B", "slug-b")
    created = client.post(
        "/api/applications",
        json={
            "name": "Slug Embed",
            "url": "slug-a",
            "url_type": "embedded",
            "teams": ["Red Team"],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    app_id = created.json()["id"]

    ok = client.patch(
        f"/api/applications/{app_id}",
        json={"url": "slug-b"},  # url_type omitted
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["url"] == "slug-b"
