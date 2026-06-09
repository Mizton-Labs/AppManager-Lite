"""Administrator-managed teams: CRUD, reordering, validation, and access."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_team(client, csrf, name, icon=""):
    return client.post(
        "/api/settings/teams",
        json={"name": name, "icon": icon},
        headers={"X-CSRF-Token": csrf},
    )


def _create_member(client, csrf, username, teams):
    username = username if "@" in username else f"{username}@example.com"
    return client.post(
        "/api/users",
        json={"username": username, "role": "user", "teams": teams},
        headers={"X-CSRF-Token": csrf},
    )


def test_no_teams_seeded(admin) -> None:
    client, _csrf, _ = admin
    resp = client.get("/api/teams")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_create_team_appends_and_returns_object(admin) -> None:
    client, csrf, _ = admin
    first = _create_team(client, csrf, "Platform")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["name"] == "Platform"
    assert body["sort_order"] == 0
    assert body["icon"] == ""
    assert isinstance(body["id"], int)

    second = _create_team(client, csrf, "Security", icon="team-icons/shield.svg")
    assert second.status_code == 201, second.text
    assert second.json()["sort_order"] == 1
    assert second.json()["icon"] == "team-icons/shield.svg"

    listing = client.get("/api/teams").json()
    assert [t["name"] for t in listing] == ["Platform", "Security"]


def test_create_team_rejects_duplicate_name(admin) -> None:
    client, csrf, _ = admin
    assert _create_team(client, csrf, "Platform").status_code == 201
    dup = _create_team(client, csrf, "platform")  # case-insensitive
    assert dup.status_code == 400, dup.text


def test_create_team_rejects_slug_collision(admin) -> None:
    client, csrf, _ = admin
    assert _create_team(client, csrf, "Red Team").status_code == 201
    # "Red-Team" collapses to the same slug "red-team".
    collide = _create_team(client, csrf, "Red-Team")
    assert collide.status_code == 400, collide.text


def test_create_team_rejects_bad_name(admin) -> None:
    client, csrf, _ = admin
    # Disallowed character.
    assert _create_team(client, csrf, "Ops/Team").status_code == 422
    # Empty slug (no alphanumerics).
    assert _create_team(client, csrf, "---").status_code == 422
    # Too long.
    assert _create_team(client, csrf, "A" * 41).status_code == 422


def test_create_team_rejects_bad_icon(admin) -> None:
    client, csrf, _ = admin
    # Traversal / arbitrary path is not an allowed catalogue path.
    resp = _create_team(client, csrf, "Platform", icon="team-icons/../secret.svg")
    assert resp.status_code == 422, resp.text
    # A non-image scheme is rejected.
    resp = _create_team(client, csrf, "Platform2", icon="javascript:alert(1)")
    assert resp.status_code == 422, resp.text


def test_create_team_accepts_catalogue_and_data_uri(admin) -> None:
    client, csrf, _ = admin
    ok_path = _create_team(client, csrf, "Net", icon="team-icons/network.svg")
    assert ok_path.status_code == 201, ok_path.text
    # A hyphenated cybersecurity-catalogue path is also accepted.
    ok_cyber = _create_team(
        client, csrf, "Blue Team", icon="team-icons/defensive-security-1.svg"
    )
    assert ok_cyber.status_code == 201, ok_cyber.text
    assert ok_cyber.json()["icon"] == "team-icons/defensive-security-1.svg"
    # A tiny 1x1 PNG data URI is accepted (same raster policy as app logos).
    png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    )
    ok_data = _create_team(client, csrf, "Cloud", icon=png)
    assert ok_data.status_code == 201, ok_data.text


def test_update_team_renames_in_place_preserving_membership(admin, make_team) -> None:
    client, csrf, _ = admin
    team = make_team("Old Name")
    # Assign a member to the team.
    member = _create_member(client, csrf, "memberx", ["Old Name"])
    assert member.status_code == 201, member.text
    user_id = member.json()["user"]["id"]

    # Rename the team in place.
    resp = client.patch(
        f"/api/settings/teams/{team['id']}",
        json={"name": "New Name"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Name"

    # The member's membership followed the rename (stored by id).
    users = client.get("/api/users").json()
    target = next(u for u in users if u["id"] == user_id)
    assert target["teams"] == ["New Name"]


def test_update_team_change_icon(admin, make_team) -> None:
    client, csrf, _ = admin
    team = make_team("Platform")
    resp = client.patch(
        f"/api/settings/teams/{team['id']}",
        json={"icon": "team-icons/server.svg"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["icon"] == "team-icons/server.svg"


def test_update_team_rejects_duplicate_name(admin, make_team) -> None:
    client, csrf, _ = admin
    make_team("Alpha")
    beta = make_team("Beta")
    resp = client.patch(
        f"/api/settings/teams/{beta['id']}",
        json={"name": "Alpha"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400, resp.text


def test_update_unknown_team_404(admin) -> None:
    client, csrf, _ = admin
    resp = client.patch(
        "/api/settings/teams/999",
        json={"name": "Nope"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 404, resp.text


def test_delete_team_cascades_membership(admin, make_team) -> None:
    client, csrf, _ = admin
    team = make_team("Temp")
    member = _create_member(client, csrf, "tempmember", ["Temp"])
    user_id = member.json()["user"]["id"]

    resp = client.delete(
        f"/api/settings/teams/{team['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text

    assert client.get("/api/teams").json() == []
    users = client.get("/api/users").json()
    target = next(u for u in users if u["id"] == user_id)
    assert target["teams"] == []


def test_delete_unknown_team_404(admin) -> None:
    client, csrf, _ = admin
    resp = client.delete(
        "/api/settings/teams/999", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 404, resp.text


def test_reorder_teams(admin, make_team) -> None:
    client, csrf, _ = admin
    a = make_team("A")
    b = make_team("B")
    c = make_team("C")
    resp = client.post(
        "/api/settings/teams/reorder",
        json={"team_ids": [c["id"], a["id"], b["id"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert [t["name"] for t in resp.json()] == ["C", "A", "B"]
    # Persisted in the public listing.
    assert [t["name"] for t in client.get("/api/teams").json()] == ["C", "A", "B"]


def test_reorder_rejects_incomplete_set(admin, make_team) -> None:
    client, csrf, _ = admin
    a = make_team("A")
    make_team("B")
    resp = client.post(
        "/api/settings/teams/reorder",
        json={"team_ids": [a["id"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400, resp.text


def test_teams_readable_by_member(admin, make_team) -> None:
    client, csrf, _ = admin
    make_team("Shared")
    member = _create_member(client, csrf, "readermember", ["Shared"])
    password = member.json()["password"]
    with TestClient(client.app) as user:
        user.post(
            "/api/auth/login",
            json={"username": "readermember", "password": password},
        )
        resp = user.get("/api/teams")
    assert resp.status_code == 200, resp.text
    assert [t["name"] for t in resp.json()] == ["Shared"]


def test_team_mutations_require_admin(admin, make_team) -> None:
    client, csrf, _ = admin
    make_team("Shared")
    member = _create_member(client, csrf, "mutmember", ["Shared"])
    password = member.json()["password"]
    with TestClient(client.app) as user:
        user.post(
            "/api/auth/login",
            json={"username": "mutmember", "password": password},
        )
        member_csrf = user.get("/api/session").json().get("csrf_token") or ""
        create = user.post(
            "/api/settings/teams",
            json={"name": "Sneaky"},
            headers={"X-CSRF-Token": member_csrf},
        )
        admin_list = user.get("/api/settings/teams")
    assert create.status_code == 403
    assert admin_list.status_code == 403


def test_team_create_requires_csrf(admin, make_team) -> None:
    client, _csrf, _ = admin
    # No CSRF header -> rejected.
    resp = client.post("/api/settings/teams", json={"name": "NoCsrf"})
    assert resp.status_code == 403, resp.text
