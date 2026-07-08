"""Per-user SSH keypairs and the derived user-id (issue_015 phase 1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import sshkeys
from app.repository import derive_user_id


def _create_member(client, csrf, username="keyuser@example.com", **extra):
    body = {
        "username": username,
        "role": "user",
        "teams": [],
        "apps_server": "apps.example.com",
    }
    body.update(extra)
    resp = client.post("/api/users", json=body, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(app, username: str, password: str) -> tuple[TestClient, str]:
    member = TestClient(app)
    login = member.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert login.status_code == 200, login.text
    return member, login.json()["csrf_token"]


# ---------------------------------------------------------------------------
# Derived user-id
# ---------------------------------------------------------------------------


def test_derive_user_id_replaces_dots_and_underscores() -> None:
    assert derive_user_id("john.doe@example.com") == "john-doe"
    assert derive_user_id("a_b.c@example.org") == "a-b-c"
    assert derive_user_id("Upper.Case@example.com") == "upper-case"
    # Non-email usernames (e.g. the first-run admin) use the whole name.
    assert derive_user_id("admin") == "admin"


def test_derive_user_id_restricts_character_set() -> None:
    # The identifier later names servers/config entries; anything outside
    # [a-z0-9-] is dropped rather than passed through.
    assert derive_user_id("john+tag@example.com") == "johntag"
    assert derive_user_id("j'ohn#1@example.com") == "john1"
    assert derive_user_id("_leading.trailing_@example.com") == "leading-trailing"
    assert derive_user_id("++@example.com") == ""


def test_create_user_rejects_user_id_collision(admin) -> None:
    client, csrf, _ = admin
    _create_member(client, csrf, username="john.doe@example.com")

    # Different email, same derived identifier ("john-doe").
    resp = client.post(
        "/api/users",
        json={
            "username": "john_doe@another.org",
            "role": "user",
            "teams": [],
            "apps_server": "apps.example.com",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409, resp.text
    assert "john-doe" in resp.json()["detail"]


def test_create_user_rejects_empty_derived_user_id(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/users",
        json={
            "username": "++@example.com",
            "role": "user",
            "teams": [],
            "apps_server": "apps.example.com",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409, resp.text


def test_user_out_includes_derived_user_id(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf, username="john.doe_x@example.com")
    assert created["user"]["user_id"] == "john-doe-x"

    listed = client.get("/api/users")
    assert listed.status_code == 200
    by_name = {u["username"]: u for u in listed.json()}
    assert by_name["john.doe_x@example.com"]["user_id"] == "john-doe-x"
    assert by_name["admin"]["user_id"] == "admin"


def test_session_user_includes_user_id(admin) -> None:
    client, _, _ = admin
    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["user"]["user_id"] == "admin"


# ---------------------------------------------------------------------------
# Keypair generation and account endpoints
# ---------------------------------------------------------------------------


def test_generate_keypair_shape() -> None:
    private_key, public_key = sshkeys.generate_keypair("john.doe@example.com")
    assert private_key.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert private_key.rstrip().endswith("-----END OPENSSH PRIVATE KEY-----")
    assert public_key.startswith("ssh-ed25519 ")
    assert public_key.endswith(" john.doe@example.com")
    # Comments are sanitized to a safe character set.
    _, weird = sshkeys.generate_keypair("evil user; rm -rf /@x")
    comment = weird.split(" ", 2)[2]
    assert ";" not in comment and "/" not in comment and " " not in comment


def test_new_user_gets_ssh_key(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)

    member, _ = _login(client.app, "keyuser@example.com", created["password"])
    resp = member.get("/api/account/ssh-key")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == "keyuser"
    assert body["public_key"].startswith("ssh-ed25519 ")
    assert body["generated_at"]
    # The info endpoint must never carry private key material.
    assert "private" not in resp.text.lower()


def test_user_listing_never_exposes_key_material(admin) -> None:
    client, csrf, _ = admin
    _create_member(client, csrf)
    listed = client.get("/api/users")
    assert listed.status_code == 200
    text = listed.text.lower()
    assert "ssh_private_key" not in text
    assert "private key" not in text
    assert "ssh-ed25519" not in text


def test_download_private_and_public_key(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    member, _ = _login(client.app, "keyuser@example.com", created["password"])

    private = member.get("/api/account/ssh-key/download?part=private")
    assert private.status_code == 200
    assert private.text.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert 'filename="id_ed25519"' in private.headers["content-disposition"]
    assert private.headers["cache-control"] == "no-store"

    public = member.get("/api/account/ssh-key/download?part=public")
    assert public.status_code == 200
    assert public.text.startswith("ssh-ed25519 ")
    assert 'filename="id_ed25519.pub"' in public.headers["content-disposition"]

    # ``part`` must be explicit; there is no implicit private-key download.
    missing = member.get("/api/account/ssh-key/download")
    assert missing.status_code == 422

    bad = member.get("/api/account/ssh-key/download?part=other")
    assert bad.status_code == 422


def test_private_key_download_is_audited_without_key_material(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    member, _ = _login(client.app, "keyuser@example.com", created["password"])

    member.get("/api/account/ssh-key/download?part=private")
    member.get("/api/account/ssh-key/download?part=public")

    events = client.get("/api/audit?category=user").json()
    downloads = [e for e in events if e["action"] == "ssh_key_download"]
    assert len(downloads) == 1  # public downloads are not audited
    assert downloads[0]["detail"] == "part=private"
    assert "ssh-ed25519" not in downloads[0]["detail"]
    assert "PRIVATE KEY" not in str(events)


def test_ssh_key_endpoints_require_auth(client) -> None:
    assert client.get("/api/account/ssh-key").status_code == 401
    assert (
        client.get("/api/account/ssh-key/download?part=private").status_code == 401
    )
    assert client.post("/api/account/ssh-key/regenerate").status_code == 401


def test_ssh_key_missing_for_synthetic_user_when_auth_disabled(
    client_no_auth,
) -> None:
    # The synthetic admin (id 0) has no database row and therefore no key;
    # the endpoints answer 404 instead of serving someone else's key.
    assert client_no_auth.get("/api/account/ssh-key").status_code == 404
    resp = client_no_auth.get("/api/account/ssh-key/download?part=private")
    assert resp.status_code == 404


def test_regenerate_replaces_key_and_audits(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    member, member_csrf = _login(
        client.app, "keyuser@example.com", created["password"]
    )

    before = member.get("/api/account/ssh-key").json()

    # CSRF is enforced.
    no_csrf = member.post("/api/account/ssh-key/regenerate")
    assert no_csrf.status_code == 403

    resp = member.post(
        "/api/account/ssh-key/regenerate",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 200, resp.text
    after = resp.json()
    assert after["public_key"].startswith("ssh-ed25519 ")
    assert after["public_key"] != before["public_key"]

    # The download now serves the new key.
    fresh = member.get("/api/account/ssh-key").json()
    assert fresh["public_key"] == after["public_key"]

    # Audited without key material.
    events = client.get("/api/audit?category=user").json()
    regen = [e for e in events if e["action"] == "ssh_key_regenerate"]
    assert regen, events
    assert regen[0]["target_name"] == "keyuser@example.com"
    assert "ssh-ed25519" not in (regen[0]["detail"] or "")


# ---------------------------------------------------------------------------
# Migration backfill
# ---------------------------------------------------------------------------


def test_backfill_generates_keys_for_existing_users(admin) -> None:
    client, csrf, _ = admin
    _create_member(client, csrf)

    from app.db import get_connection, init_db

    # Simulate accounts that predate the feature.
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET ssh_private_key = '', ssh_public_key = '', "
            "ssh_key_generated_at = NULL"
        )

    init_db()

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT username, ssh_private_key, ssh_public_key, "
            "ssh_key_generated_at FROM users"
        ).fetchall()
    assert rows
    from app import keystore

    for row in rows:
        # Private keys are stored encrypted at rest (issue_015-r1).
        assert keystore.is_encrypted(row["ssh_private_key"]), row["username"]
        assert keystore.decrypt(row["ssh_private_key"]).startswith(
            "-----BEGIN OPENSSH PRIVATE KEY-----"
        ), row["username"]
        assert row["ssh_public_key"].startswith("ssh-ed25519 ")
        assert row["ssh_key_generated_at"]
