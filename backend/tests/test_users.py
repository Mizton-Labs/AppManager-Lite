"""Administrator user-management behavior and guardrails."""

from __future__ import annotations

import pytest

# Team names referenced by the membership tests in this file. Teams are no
# longer seeded, so they are created via the admin API before each test.
_TEAMS = ("Threat Hunting", "Red Team", "Threat Intel")


@pytest.fixture(autouse=True)
def _seed_teams(admin, make_team):
    for name in _TEAMS:
        make_team(name)


def _create(client, csrf, username, role="user", teams=None):
    username = username if "@" in username else f"{username}@example.com"
    return client.post(
        "/api/users",
        json={
            "username": username,
            "role": role,
            "teams": teams or [],
            "apps_server": "apps.example.com",
        },
        headers={"X-CSRF-Token": csrf},
    )


def test_create_user_returns_password_and_forces_change(admin) -> None:
    client, csrf, _ = admin
    resp = _create(client, csrf, "alice", teams=["Threat Hunting"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert isinstance(body["password"], str) and len(body["password"]) >= 12
    assert body["user"]["must_change_password"] is True
    assert body["user"]["teams"] == ["Threat Hunting"]
    assert body["user"]["role"] == "user"
    assert body["user"]["self_service"] is False


def test_create_user_with_self_service(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/users",
        json={
            "username": "olive@example.com",
            "role": "user",
            "teams": ["Red Team"],
            "self_service": True,
            "apps_server": "apps.example.com",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["self_service"] is True


def test_update_user_toggles_self_service(admin) -> None:
    client, csrf, _ = admin
    user_id = _create(client, csrf, "peggy", teams=["Red Team"]).json()["user"][
        "id"
    ]
    on = client.patch(
        f"/api/users/{user_id}",
        json={"self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert on.status_code == 200, on.text
    assert on.json()["self_service"] is True
    off = client.patch(
        f"/api/users/{user_id}",
        json={"self_service": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert off.json()["self_service"] is False


def test_admin_is_self_service_by_default(admin) -> None:
    client, _csrf, _ = admin
    admin_row = next(
        u for u in client.get("/api/users").json() if u["username"] == "admin"
    )
    assert admin_row["self_service"] is True


def test_create_user_rejects_unknown_team(admin) -> None:
    client, csrf, _ = admin
    resp = _create(client, csrf, "bob", teams=["Nonexistent Team"])
    assert resp.status_code == 400


def test_create_user_rejects_non_email_username(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/users",
        json={
            "username": "not-an-email",
            "role": "user",
            "teams": [],
            "apps_server": "apps.example.com",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


def test_create_user_rejects_duplicate_username(admin) -> None:
    client, csrf, _ = admin
    assert _create(client, csrf, "carol").status_code == 201
    assert _create(client, csrf, "carol").status_code == 409


def test_create_user_allows_empty_apps_server(admin) -> None:
    # issue_017: an apps-server location is optional; a user may be created
    # without one (they can still view applications).
    client, csrf, _ = admin
    resp = client.post(
        "/api/users",
        json={"username": "noserver@example.com", "role": "user", "teams": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["apps_server"] == ""
    assert resp.json()["user"]["apps_server_ip"] == ""


def test_create_user_accepts_apps_server_ip(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/users",
        json={
            "username": "ipuser@example.com",
            "role": "user",
            "teams": [],
            "apps_server_ip": "10.0.0.7",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["apps_server"] == ""
    assert resp.json()["user"]["apps_server_ip"] == "10.0.0.7"


def test_teams_endpoint_returns_created_teams(admin) -> None:
    client, _csrf, _ = admin
    resp = client.get("/api/teams")
    assert resp.status_code == 200
    # The endpoint now returns team objects in creation/sidebar order.
    assert [t["name"] for t in resp.json()] == list(_TEAMS)


def test_list_users_includes_admin_and_created(admin) -> None:
    client, csrf, _ = admin
    _create(client, csrf, "dave")
    usernames = {u["username"] for u in client.get("/api/users").json()}
    assert {"admin", "dave@example.com"} <= usernames


def test_update_user_role_teams_and_active(admin) -> None:
    client, csrf, _ = admin
    user_id = _create(client, csrf, "erin").json()["user"]["id"]
    resp = client.patch(
        f"/api/users/{user_id}",
        json={"role": "admin", "teams": ["Red Team", "Threat Intel"], "is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "admin"
    assert set(body["teams"]) == {"Red Team", "Threat Intel"}
    assert body["is_active"] is False


def test_disabled_user_cannot_login(admin) -> None:
    client, csrf, _ = admin
    created = _create(client, csrf, "frank").json()
    user_id = created["user"]["id"]
    password = created["password"]
    client.patch(
        f"/api/users/{user_id}",
        json={"is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    # A second client (same database) attempts to authenticate.
    from fastapi.testclient import TestClient

    with TestClient(client.app) as other:
        resp = other.post(
            "/api/auth/login", json={"username": "frank", "password": password}
        )
    assert resp.status_code == 401


def test_cannot_demote_last_admin(admin) -> None:
    client, csrf, _ = admin
    admin_id = next(u["id"] for u in client.get("/api/users").json() if u["username"] == "admin")
    resp = client.patch(
        f"/api/users/{admin_id}", json={"role": "user"}, headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 400


def test_cannot_disable_last_admin(admin) -> None:
    client, csrf, _ = admin
    admin_id = next(u["id"] for u in client.get("/api/users").json() if u["username"] == "admin")
    resp = client.patch(
        f"/api/users/{admin_id}", json={"is_active": False}, headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 400


def test_cannot_delete_self(admin) -> None:
    client, csrf, _ = admin
    admin_id = next(u["id"] for u in client.get("/api/users").json() if u["username"] == "admin")
    resp = client.request(
        "DELETE", f"/api/users/{admin_id}", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 400


def test_second_admin_allows_demoting_first(admin) -> None:
    client, csrf, _ = admin
    # Promote a second admin, then the original may be demoted.
    other_id = _create(client, csrf, "grace", role="admin").json()["user"]["id"]
    assert other_id
    admin_id = next(u["id"] for u in client.get("/api/users").json() if u["username"] == "admin")
    resp = client.patch(
        f"/api/users/{admin_id}", json={"role": "user"}, headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200


def test_reset_password_changes_value_and_forces_change(admin) -> None:
    client, csrf, _ = admin
    created = _create(client, csrf, "heidi").json()
    user_id = created["user"]["id"]
    original = created["password"]
    resp = client.post(
        f"/api/users/{user_id}/reset-password", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["password"] != original
    assert body["user"]["must_change_password"] is True


def test_delete_user_removes_it(admin) -> None:
    client, csrf, _ = admin
    user_id = _create(client, csrf, "ivan").json()["user"]["id"]
    resp = client.request(
        "DELETE", f"/api/users/{user_id}", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200
    usernames = {u["username"] for u in client.get("/api/users").json()}
    assert "ivan" not in usernames


def test_delete_user_transfers_owned_apps_to_admin(admin) -> None:
    client, csrf, _ = admin
    created = _create(client, csrf, "owner", teams=["Red Team"]).json()
    user_id = created["user"]["id"]
    password = created["password"]
    from fastapi.testclient import TestClient

    with TestClient(client.app) as owner:
        login = owner.post(
            "/api/auth/login", json={"username": "owner", "password": password}
        )
        ocsrf = login.json()["csrf_token"]
        app_id = owner.post(
            "/api/applications",
            json={"name": "Owned App", "url": "https://example.com/o", "teams": ["Red Team"]},
            headers={"X-CSRF-Token": ocsrf},
        ).json()["id"]

    resp = client.request(
        "DELETE", f"/api/users/{user_id}", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200, resp.text
    app = next(a for a in client.get("/api/applications/manage").json() if a["id"] == app_id)
    assert app["created_by"] == "admin"


def test_delete_user_can_delete_owned_apps(admin) -> None:
    client, csrf, _ = admin
    created = _create(client, csrf, "owner2", teams=["Red Team"]).json()
    user_id = created["user"]["id"]
    password = created["password"]
    from fastapi.testclient import TestClient

    with TestClient(client.app) as owner:
        login = owner.post(
            "/api/auth/login", json={"username": "owner2", "password": password}
        )
        ocsrf = login.json()["csrf_token"]
        app_id = owner.post(
            "/api/applications",
            json={"name": "Deleted App", "url": "https://example.com/d", "teams": ["Red Team"]},
            headers={"X-CSRF-Token": ocsrf},
        ).json()["id"]

    resp = client.request(
        "DELETE",
        f"/api/users/{user_id}?delete_apps=true",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert all(a["id"] != app_id for a in client.get("/api/applications/manage").json())


def test_non_admin_cannot_manage_users(admin) -> None:
    client, csrf, _ = admin
    created = _create(client, csrf, "judy").json()
    password = created["password"]
    user_id = created["user"]["id"]
    # The new standard user changes their password, then tries to list users.
    from fastapi.testclient import TestClient

    with TestClient(client.app) as user_client:
        login = user_client.post(
            "/api/auth/login", json={"username": "judy", "password": password}
        )
        user_csrf = login.json()["csrf_token"]
        new_pw = "JudyStrongPass123"
        user_client.post(
            "/api/account/password",
            json={
                "current_password": password,
                "new_password": new_pw,
                "confirm_password": new_pw,
            },
            headers={"X-CSRF-Token": user_csrf},
        )
        listing = user_client.get("/api/users")
        delete = user_client.request(
            "DELETE", f"/api/users/{user_id}", headers={"X-CSRF-Token": user_csrf}
        )
    assert listing.status_code == 403
    assert delete.status_code == 403


# ---------------------------------------------------------------------------
# issue_local_031: mutable sign-in email, immutable resource identity, and
# case-insensitive username matching.
# ---------------------------------------------------------------------------


def test_create_user_normalizes_email_case_and_whitespace(admin) -> None:
    client, csrf, _ = admin
    resp = _create(client, csrf, "  Mixed.Case@Example.COM  ")
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["username"] == "mixed.case@example.com"


def test_create_user_rejects_case_insensitive_duplicate(admin) -> None:
    client, csrf, _ = admin
    first = _create(client, csrf, "casedup@example.com")
    assert first.status_code == 201, first.text
    dup = _create(client, csrf, "CaseDup@Example.com")
    assert dup.status_code == 409, dup.text


def test_update_user_can_change_sign_in_email(admin) -> None:
    client, csrf, _ = admin
    created = _create(client, csrf, "renameme@example.com").json()["user"]
    old_derived_id = created["user_id"]
    resp = client.patch(
        f"/api/users/{created['id']}",
        json={"username": "renamed@example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "renamed@example.com"
    # The immutable resource identity never changes on rename.
    assert body["user_id"] == old_derived_id


def test_update_user_rejects_case_insensitive_conflict(admin) -> None:
    client, csrf, _ = admin
    _create(client, csrf, "existing@example.com")
    other = _create(client, csrf, "other@example.com").json()["user"]
    resp = client.patch(
        f"/api/users/{other['id']}",
        json={"username": "Existing@Example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409, resp.text


def test_update_user_email_only_preserves_other_fields(admin) -> None:
    client, csrf, _ = admin
    created = _create(
        client, csrf, "preserve@example.com", teams=["Red Team"]
    ).json()["user"]
    resp = client.patch(
        f"/api/users/{created['id']}",
        json={"username": "preserved@example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["teams"] == ["Red Team"]
    assert body["role"] == created["role"]


def test_rename_audit_records_old_and_new_username(admin) -> None:
    client, csrf, _ = admin
    created = _create(client, csrf, "auditme@example.com").json()["user"]
    client.patch(
        f"/api/users/{created['id']}",
        json={"username": "audited@example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    entries = client.get("/api/audit?category=user").json()
    rename_entry = next(
        e for e in entries
        if e["target_id"] == created["id"] and "username_old" in e["detail"]
    )
    assert "username_old=auditme@example.com" in rename_entry["detail"]
    assert "username_new=audited@example.com" in rename_entry["detail"]


def test_sso_matches_stored_mixed_case_username_case_insensitively(admin) -> None:
    """A legacy or admin-created mixed-case username still matches an SSO
    claim that a provider normalizes to a different case."""
    client, csrf, _ = admin
    from app.db import get_connection
    from app import repository

    with get_connection() as conn:
        row = repository.get_user_by_username(conn, "someone@example.com")
        assert row is None
        # Simulate a legacy mixed-case stored username (bypassing the
        # normalizing create_user() path, as an old row might predate it).
        conn.execute(
            "INSERT INTO users (username, derived_user_id, password_hash, role) "
            "VALUES ('Someone@Example.com', 'someone', 'x', 'user')"
        )
        found = repository.get_user_by_username(conn, "someone@example.com")
        assert found is not None
        assert found["username"] == "Someone@Example.com"

        found_upper = repository.get_user_by_username(conn, "SOMEONE@EXAMPLE.COM")
        assert found_upper is not None
        assert found_upper["id"] == found["id"]
