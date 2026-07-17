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
        denied = other_client.get("/api/auth/proxy-check", params={"application_id":app_id,"alias":"private-lab"})
        assert denied.status_code == 403
    assert client.get("/api/auth/proxy-check", params={"application_id":app_id,"alias":"private-lab"}).status_code == 204


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
