"""SSH key registry + at-rest encryption (issue_015-r1 phase A)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import keystore, sshkeys


# A throwaway unencrypted ed25519 private key (generated once for tests).
_PRIV, _PUB = sshkeys.generate_keypair("test@example.com")


# ---------------------------------------------------------------------------
# keystore
# ---------------------------------------------------------------------------


def test_encrypt_roundtrip_and_idempotency(client) -> None:
    # client fixture configures the per-test data dir + master key.
    token = keystore.encrypt("secret-value")
    assert token.startswith("enc:v1:")
    assert keystore.is_encrypted(token)
    assert keystore.decrypt(token) == "secret-value"
    # Encrypting an already-encrypted value is a no-op.
    assert keystore.encrypt(token) == token
    # Empty stays empty.
    assert keystore.encrypt("") == ""
    assert keystore.decrypt("") == ""
    # Plaintext passes through decrypt unchanged (rolling-migration read).
    assert keystore.decrypt("plain") == "plain"


def test_master_key_file_created_0600(client, tmp_path) -> None:
    import os
    keystore.encrypt("x")  # forces key creation
    key_file = tmp_path / "data" / "master.key"
    assert key_file.exists()
    assert (os.stat(key_file).st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------------
# sshkeys derivation helpers
# ---------------------------------------------------------------------------


def test_public_key_and_fingerprint_derivation() -> None:
    pub = sshkeys.public_key_from_private(_PRIV)
    assert pub.startswith("ssh-ed25519 ")
    # Derived public key matches the generated one (ignoring comment).
    assert pub.split()[1] == _PUB.split()[1]
    fp = sshkeys.fingerprint(pub)
    assert fp.startswith("SHA256:")


def test_reject_malformed_private_key() -> None:
    with pytest.raises(sshkeys.SshKeyError):
        sshkeys.public_key_from_private("not a key")


# ---------------------------------------------------------------------------
# Registry API
# ---------------------------------------------------------------------------


def test_ssh_key_crud_path_and_stored(admin) -> None:
    client, csrf, _ = admin

    # kind=path
    p = client.post(
        "/api/settings/ssh-keys",
        json={"name": "proxy key", "kind": "path",
              "path": "/data/keys/proxy_ed25519"},
        headers={"X-CSRF-Token": csrf},
    )
    assert p.status_code == 201, p.text
    assert p.json()["kind"] == "path"
    assert p.json()["has_private_key"] is False

    # kind=stored: private key accepted, public key + fingerprint derived,
    # secret never returned.
    s = client.post(
        "/api/settings/ssh-keys",
        json={"name": "stored key", "kind": "stored", "private_key": _PRIV},
        headers={"X-CSRF-Token": csrf},
    )
    assert s.status_code == 201, s.text
    body = s.json()
    assert body["kind"] == "stored"
    assert body["public_key"].startswith("ssh-ed25519 ")
    assert body["fingerprint"].startswith("SHA256:")
    assert body["has_private_key"] is True
    assert "BEGIN OPENSSH" not in s.text
    assert "enc:v1:" not in s.text
    assert _PRIV.split()[0] not in s.text

    listed = client.get("/api/settings/ssh-keys")
    assert {k["name"] for k in listed.json()} == {"proxy key", "stored key"}
    assert "BEGIN OPENSSH" not in listed.text

    # Duplicate name -> 409.
    dup = client.post(
        "/api/settings/ssh-keys",
        json={"name": "proxy key", "kind": "path", "path": "/x/y"},
        headers={"X-CSRF-Token": csrf},
    )
    assert dup.status_code == 409

    # Delete (unreferenced) works.
    d = client.delete(
        f"/api/settings/ssh-keys/{body['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert d.status_code == 200
    assert client.get("/api/settings/ssh-keys").status_code == 200


def test_stored_key_persisted_encrypted(admin) -> None:
    client, csrf, _ = admin
    client.post(
        "/api/settings/ssh-keys",
        json={"name": "enc key", "kind": "stored", "private_key": _PRIV},
        headers={"X-CSRF-Token": csrf},
    )
    from app.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT encrypted_private_key FROM ssh_keys WHERE name='enc key'"
        ).fetchone()
    assert keystore.is_encrypted(row["encrypted_private_key"])
    assert keystore.decrypt(row["encrypted_private_key"]).startswith(
        "-----BEGIN OPENSSH PRIVATE KEY-----"
    )


def test_invalid_stored_key_rejected(admin) -> None:
    client, csrf, _ = admin
    r = client.post(
        "/api/settings/ssh-keys",
        json={"name": "bad", "kind": "stored", "private_key": "garbage"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400

    # path kind requires a path; metachars rejected; must be absolute.
    for path in ("", "relative/path", "/x; rm -rf /"):
        r = client.post(
            "/api/settings/ssh-keys",
            json={"name": f"p-{path}", "kind": "path", "path": path},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code in (400, 422), (path, r.text)


def test_oversize_private_key_not_echoed_in_error(admin) -> None:
    client, csrf, _ = admin
    secret = "-----BEGIN OPENSSH PRIVATE KEY-----\n" + "A" * 40000
    r = client.post(
        "/api/settings/ssh-keys",
        json={"name": "big", "kind": "stored", "private_key": secret},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422
    # The pasted secret must not be reflected back in the validation body.
    assert "BEGIN OPENSSH" not in r.text
    assert "A" * 100 not in r.text
    assert "redacted" in r.text.lower()


def test_resolve_ssh_key_path_and_stored(admin) -> None:
    client, csrf, _ = admin
    from app.db import get_connection
    from app import servers

    # path kind -> returns the path unchanged
    p = client.post(
        "/api/settings/ssh-keys",
        json={"name": "pk", "kind": "path", "path": "/data/keys/k"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    # stored kind -> materialized 0600 file whose content decrypts to the key
    s = client.post(
        "/api/settings/ssh-keys",
        json={"name": "sk", "kind": "stored", "private_key": _PRIV},
        headers={"X-CSRF-Token": csrf},
    ).json()

    import os
    with get_connection() as conn:
        assert servers.resolve_ssh_key(conn, p["id"]) == "/data/keys/k"
        path = servers.resolve_ssh_key(conn, s["id"])
        assert path.endswith(f"ssh-key-{s['id']}")
        assert (os.stat(path).st_mode & 0o777) == 0o600
        assert open(path).read().startswith(
            "-----BEGIN OPENSSH PRIVATE KEY-----"
        )
        # Unknown / None id -> fallback path
        assert servers.resolve_ssh_key(conn, None, fallback_path="/fb") == "/fb"
        assert servers.resolve_ssh_key(conn, 99999, fallback_path="/fb") == "/fb"

    # Deleting the stored key removes the materialized plaintext file.
    client.delete(
        f"/api/settings/ssh-keys/{s['id']}", headers={"X-CSRF-Token": csrf}
    )
    assert not os.path.exists(path)


def test_import_dedupes_names_for_distinct_paths(admin) -> None:
    from app.db import get_connection, init_db

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO server_templates (vmid, name, kind, admin_ssh_key_path) "
            "VALUES (11, 'A', 'lxc', '/a/id_ed25519')"
        )
        conn.execute(
            "INSERT INTO server_templates (vmid, name, kind, admin_ssh_key_path) "
            "VALUES (12, 'B', 'lxc', '/b/id_ed25519')"
        )
    init_db()
    with get_connection() as conn:
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM ssh_keys WHERE kind='path' AND name LIKE 'id_ed25519%'"
        )]
    # Same basename, distinct paths -> unique names.
    assert len(names) == len(set(names)) == 2


def test_delete_referenced_key_blocked(admin) -> None:
    client, csrf, _ = admin
    key = client.post(
        "/api/settings/ssh-keys",
        json={"name": "tpl key", "kind": "path", "path": "/data/keys/k"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    # Reference it from a server template.
    from app.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO server_templates (vmid, name, kind, admin_ssh_key_id) "
            "VALUES (9001, 'T', 'lxc', ?)",
            (key["id"],),
        )
    d = client.delete(
        f"/api/settings/ssh-keys/{key['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert d.status_code == 409
    assert "in use" in d.json()["detail"].lower()


def test_reverse_proxy_settings_use_key_registry(admin) -> None:
    client, csrf, _ = admin
    key = client.post(
        "/api/settings/ssh-keys",
        json={"name": "rp key", "kind": "path", "path": "/data/keys/rp"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    # Select the registry key for reverse proxy.
    r = client.patch(
        "/api/settings/reverse-proxy",
        json={"reverse_proxy_ssh_key_id": key["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["reverse_proxy_ssh_key_id"] == key["id"]

    # Unknown key id -> 400.
    bad = client.patch(
        "/api/settings/reverse-proxy",
        json={"reverse_proxy_ssh_key_id": 99999},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400

    # The resolver returns the registered path.
    from app.db import get_connection
    from app import repository
    with get_connection() as conn:
        row = repository.get_settings_row(conn)
        assert repository.reverse_proxy_key_path(conn, row) == "/data/keys/rp"


def test_reverse_proxy_response_never_leaks_materialized_path(admin) -> None:
    """A stored key resolves to a data/keys/... file for ssh, but that
    materialized path must never appear in the settings response."""
    client, csrf, _ = admin
    key = client.post(
        "/api/settings/ssh-keys",
        json={"name": "stored rp", "kind": "stored", "private_key": _PRIV},
        headers={"X-CSRF-Token": csrf},
    ).json()
    r = client.patch(
        "/api/settings/reverse-proxy",
        json={"reverse_proxy_ssh_key_id": key["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reverse_proxy_ssh_key_id"] == key["id"]
    # Response keeps the (empty) raw path, NOT the materialized key file.
    assert "data/keys/ssh-key-" not in r.text
    assert body["ssh_key_path"] == ""
    # But the resolver does return the materialized path for ssh use.
    from app.db import get_connection
    from app import repository
    with get_connection() as conn:
        resolved = repository.reverse_proxy_key_path(
            conn, repository.get_settings_row(conn)
        )
    assert resolved.endswith(f"ssh-key-{key['id']}")


def test_clear_server_template_key(admin) -> None:
    client, csrf, _ = admin
    key = client.post(
        "/api/settings/ssh-keys",
        json={"name": "clr key", "kind": "path", "path": "/data/keys/c"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    tpl = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "C", "kind": "lxc",
              "admin_ssh_key_id": key["id"]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert tpl["admin_ssh_key_id"] == key["id"]
    # Explicit null clears the assignment.
    cleared = client.patch(
        f"/api/settings/server-templates/{tpl['id']}",
        json={"admin_ssh_key_id": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["admin_ssh_key_id"] is None
    # Omitting the field leaves it unchanged (rename only).
    renamed = client.patch(
        f"/api/settings/server-templates/{tpl['id']}",
        json={"name": "C2"},
        headers={"X-CSRF-Token": csrf},
    )
    assert renamed.json()["admin_ssh_key_id"] is None
    # Now that it is unreferenced, the key can be deleted.
    assert client.delete(
        f"/api/settings/ssh-keys/{key['id']}", headers={"X-CSRF-Token": csrf}
    ).status_code == 200


def test_server_template_uses_key_registry(admin) -> None:
    client, csrf, _ = admin
    key = client.post(
        "/api/settings/ssh-keys",
        json={"name": "tpl key", "kind": "path", "path": "/data/keys/tpl"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    created = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "T", "kind": "lxc",
              "admin_ssh_key_id": key["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    assert created.json()["admin_ssh_key_id"] == key["id"]

    # Deleting the referenced key is blocked.
    d = client.delete(
        f"/api/settings/ssh-keys/{key['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert d.status_code == 409

    # Unknown key on create -> 400.
    bad = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9002, "name": "T2", "kind": "lxc",
              "admin_ssh_key_id": 99999},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400


def test_ssh_keys_require_admin(admin) -> None:
    client, csrf, _ = admin
    created = client.post(
        "/api/users",
        json={"username": "keyu@example.com", "role": "user", "teams": [],
              "apps_server": "a.example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login",
            json={"username": "keyu@example.com",
                  "password": created.json()["password"]},
        )
        mc = login.json()["csrf_token"]
        assert member.get("/api/settings/ssh-keys").status_code == 403
        assert member.post(
            "/api/settings/ssh-keys",
            json={"name": "x", "kind": "path", "path": "/a"},
            headers={"X-CSRF-Token": mc},
        ).status_code == 403


# ---------------------------------------------------------------------------
# Migration: import paths + re-encrypt user keys
# ---------------------------------------------------------------------------


def test_migration_imports_paths_and_reencrypts(admin) -> None:
    client, csrf, _ = admin
    from app.db import get_connection, init_db

    with get_connection() as conn:
        # Simulate a legacy deployment: plaintext user key + configured paths.
        conn.execute(
            "UPDATE users SET ssh_private_key = ? WHERE username = 'admin'",
            ("-----BEGIN OPENSSH PRIVATE KEY-----\nlegacy\n"
             "-----END OPENSSH PRIVATE KEY-----\n",),
        )
        conn.execute(
            "UPDATE settings SET ssh_key_path = '/data/keys/proxy', "
            "reverse_proxy_ssh_key_id = NULL WHERE id = 1"
        )
        conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")

    init_db()  # re-run migration (idempotent)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT ssh_private_key FROM users WHERE username='admin'"
        ).fetchone()
        assert keystore.is_encrypted(row["ssh_private_key"])
        assert "legacy" in keystore.decrypt(row["ssh_private_key"])

        s = conn.execute(
            "SELECT reverse_proxy_ssh_key_id FROM settings WHERE id=1"
        ).fetchone()
        assert s["reverse_proxy_ssh_key_id"] is not None
        imported = conn.execute(
            "SELECT * FROM ssh_keys WHERE id = ?",
            (s["reverse_proxy_ssh_key_id"],),
        ).fetchone()
        assert imported["kind"] == "path"
        assert imported["path"] == "/data/keys/proxy"

    # Re-running does not double-import.
    init_db()
    with get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM ssh_keys WHERE path='/data/keys/proxy'"
        ).fetchone()["c"]
        assert n == 1
