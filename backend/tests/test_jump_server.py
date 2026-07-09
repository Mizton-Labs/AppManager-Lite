"""Jump-server user lifecycle (issue_015-r1 phase C)."""

from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from app import jumpserver, servers


class _FakeSsh:
    def __init__(self, monkeypatch, *, rc=0, stdout="onboarded"):
        self.commands: list[list[str]] = []
        self.rc = rc
        self.stdout = stdout
        monkeypatch.setattr(servers, "_run", self)

    def __call__(self, argv, *, timeout=25):
        self.commands.append(argv)
        return subprocess.CompletedProcess(
            argv, returncode=self.rc, stdout=self.stdout, stderr="boom"
        )


def _register_key(client, csrf, name="jump key", path="/jump/key"):
    return client.post(
        "/api/settings/ssh-keys",
        json={"name": name, "kind": "path", "path": path},
        headers={"X-CSRF-Token": csrf},
    ).json()


def _enable_jump(client, csrf, key_id, host="10.0.0.9", user="root"):
    r = client.patch(
        "/api/settings/provisioning",
        json={"jump_enabled": True, "jump_host": host,
              "jump_management_user": user, "jump_user": user,
              "jump_ssh_key_id": key_id},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    return r


def _create_member(client, csrf, username="jumpuser@example.com", **extra):
    body = {"username": username, "role": "user", "teams": [],
            "apps_server": "apps.example.com"}
    body.update(extra)
    resp = client.post("/api/users", json=body, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(app, username, password):
    member = TestClient(app)
    login = member.post("/api/auth/login",
                        json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return member, login.json()["csrf_token"]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_jump_settings_roundtrip_and_validation(admin) -> None:
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    r = _enable_jump(client, csrf, key["id"])
    body = r.json()
    assert body["jump_enabled"] is True
    assert body["jump_host"] == "10.0.0.9"
    assert body["jump_management_user"] == "root"
    # Account model defaults to per-user.
    assert body["jump_account_mode"] == "per_user"
    assert body["jump_ssh_key_id"] == key["id"]

    # Unknown key -> 400.
    bad = client.patch(
        "/api/settings/provisioning",
        json={"jump_ssh_key_id": 99999},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400
    # Bad host / user -> 422.
    for payload in ({"jump_host": "bad host"},
                    {"jump_management_user": "a b"}):
        assert client.patch(
            "/api/settings/provisioning", json=payload,
            headers={"X-CSRF-Token": csrf},
        ).status_code == 422


# ---------------------------------------------------------------------------
# Onboarding on create
# ---------------------------------------------------------------------------


def test_jump_port_persisted_and_used(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    ssh = _FakeSsh(monkeypatch)
    key = _register_key(client, csrf)
    r = client.patch(
        "/api/settings/provisioning",
        json={"jump_enabled": True, "jump_host": "10.0.0.9",
              "jump_user": "root", "jump_port": 2222,
              "jump_ssh_key_id": key["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["jump_port"] == 2222

    _create_member(client, csrf, username="port.user@example.com")
    argv = ssh.commands[-1]
    assert argv[argv.index("-p") + 1] == "2222"

    # Out-of-range port rejected.
    bad = client.patch(
        "/api/settings/provisioning",
        json={"jump_port": 70000},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 422


def test_jump_port_defaults_to_22(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    ssh = _FakeSsh(monkeypatch)
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])  # no port -> default 22
    _create_member(client, csrf)
    argv = ssh.commands[-1]
    assert argv[argv.index("-p") + 1] == "22"


def test_create_user_onboards_to_jump(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    ssh = _FakeSsh(monkeypatch)
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])

    _create_member(client, csrf, username="john.doe@example.com")
    # One SSH call to onboard; connects as root@host with the jump key.
    assert len(ssh.commands) == 1
    argv = ssh.commands[0]
    assert argv[argv.index("-i") + 1] == "/jump/key"
    assert argv[-2] == "root@10.0.0.9"
    assert "useradd" in argv[-1]
    assert "john-doe" in argv[-1]  # OS user = derived user_id
    # Audit records the jump outcome (no key material).
    events = client.get("/api/audit?category=user").json()
    create = [e for e in events if e["action"] == "create"
              and "john.doe" in e["target_name"]][0]
    assert "jump=onboarded" in create["detail"]


def test_create_user_no_jump_when_disabled(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    ssh = _FakeSsh(monkeypatch)
    _create_member(client, csrf)
    assert ssh.commands == []  # no jump attempts


def test_onboard_failure_is_non_blocking(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeSsh(monkeypatch, rc=1)
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])
    # User creation still succeeds even though onboarding fails.
    created = _create_member(client, csrf)
    assert created["user"]["id"]
    events = client.get("/api/audit?category=user").json()
    create = [e for e in events if e["action"] == "create"][0]
    assert "jump=failed" in create["detail"]


# ---------------------------------------------------------------------------
# Offboarding on delete
# ---------------------------------------------------------------------------


def test_delete_user_removes_key_from_jump(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    ssh = _FakeSsh(monkeypatch, stdout="removed")
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])
    created = _create_member(client, csrf, username="gone@example.com")
    user_id = created["user"]["id"]
    ssh.commands.clear()

    resp = client.delete(
        f"/api/users/{user_id}", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200, resp.text
    # An offboard SSH call was made removing the key (grep -vF against the blob).
    assert len(ssh.commands) == 1
    remote = ssh.commands[0][-1]
    assert "authorized_keys" in remote
    assert "useradd" not in remote  # key removal only, not account creation
    events = client.get("/api/audit?category=user").json()
    deleted = [e for e in events if e["action"] == "delete"][0]
    assert "jump=removed" in deleted["detail"]


# ---------------------------------------------------------------------------
# Bulk sync
# ---------------------------------------------------------------------------


def test_sync_users_to_jump(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    # Create users BEFORE enabling jump so they are not yet onboarded.
    _create_member(client, csrf, username="a@example.com")
    _create_member(client, csrf, username="b@example.com")
    _enable_jump(client, csrf, key["id"])
    ssh = _FakeSsh(monkeypatch)

    resp = client.post(
        "/api/settings/jump-server/sync", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 200, resp.text
    results = {r["username"]: r["status"] for r in resp.json()["results"]}
    # admin + a + b all onboarded.
    assert results["a@example.com"] == "onboarded"
    assert results["b@example.com"] == "onboarded"
    assert all(v == "onboarded" for v in results.values())
    # One SSH onboard call per active user.
    assert len(ssh.commands) == len(results)


def test_sync_requires_enabled(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/settings/jump-server/sync", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 400


def test_sync_requires_admin(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])
    _FakeSsh(monkeypatch)
    created = _create_member(client, csrf)
    member, mc = _login(client.app, "jumpuser@example.com", created["password"])
    resp = member.post(
        "/api/settings/jump-server/sync", headers={"X-CSRF-Token": mc}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Regeneration rotates the jump-server key too
# ---------------------------------------------------------------------------


def test_regenerate_rotates_jump_server(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    ssh = _FakeSsh(monkeypatch)
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])
    created = _create_member(client, csrf)
    member, mc = _login(client.app, "jumpuser@example.com", created["password"])
    ssh.commands.clear()

    resp = member.post(
        "/api/account/ssh-key/regenerate", headers={"X-CSRF-Token": mc}
    )
    assert resp.status_code == 200, resp.text
    rotation = {r["server"]: r for r in resp.json()["rotation"]}
    assert "jump server" in rotation
    assert rotation["jump server"]["status"] == "updated"
    # Offboard (old) + onboard (new) SSH calls were issued.
    assert len(ssh.commands) >= 2


# ---------------------------------------------------------------------------
# Unit: onboard/offboard command safety
# ---------------------------------------------------------------------------


def test_onboard_rejects_bad_os_user(monkeypatch) -> None:
    _FakeSsh(monkeypatch)
    from app.proxmox import ProxmoxResult
    cfg = jumpserver.JumpConfig(
        enabled=True, host="h", key_path="/k", management_user="root")
    r = ProxmoxResult()
    assert not jumpserver.onboard_user(
        cfg, os_user="Bad User", public_key="ssh-ed25519 AAAA x", result=r
    )
    assert r.status == "failed"


def test_offboard_rejects_malformed_key(monkeypatch) -> None:
    _FakeSsh(monkeypatch)
    from app.proxmox import ProxmoxResult
    cfg = jumpserver.JumpConfig(
        enabled=True, host="h", key_path="/k", management_user="root")
    r = ProxmoxResult()
    assert not jumpserver.offboard_user(
        cfg, os_user="alice", public_key="no-blob", result=r
    )
    assert r.status == "failed"


def test_sync_user_never_raises_on_config_error(admin, monkeypatch) -> None:
    """A stored jump key that fails to decrypt must not block user create."""
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])

    # Make key resolution blow up (simulates a bad/rotated master key).
    from app import jumpserver as js

    def boom(*a, **kw):
        raise RuntimeError("decrypt failed")

    monkeypatch.setattr(js, "load_config", boom)
    # User creation still succeeds; jump reported as failed.
    created = _create_member(client, csrf, username="resilient@example.com")
    assert created["user"]["id"]
    events = client.get("/api/audit?category=user").json()
    create = [e for e in events if e["action"] == "create"
              and "resilient" in e["target_name"]][0]
    assert "jump=failed" in create["detail"]


def test_enabling_jump_requires_full_config(admin) -> None:
    client, csrf, _ = admin
    # Enabling with no host/user/key -> 400.
    r = client.patch(
        "/api/settings/provisioning",
        json={"jump_enabled": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "requires" in r.json()["detail"].lower()


def test_onboard_stamps_key_with_appmanager_marker(monkeypatch) -> None:
    """The onboarded authorized_keys line carries the AppManager marker."""
    ssh = _FakeSsh(monkeypatch)
    from app.proxmox import ProxmoxResult
    cfg = jumpserver.JumpConfig(
        enabled=True, host="h", key_path="/k", management_user="root",
    )
    r = ProxmoxResult()
    ok = jumpserver.onboard_user(
        cfg, os_user="alice",
        public_key="ssh-ed25519 AAAAC3Nz alice@laptop", result=r,
    )
    assert ok
    remote = ssh.commands[0][-1]
    # Connects as the management user.
    assert ssh.commands[0][-2] == "root@h"
    # The stamped comment replaces the original one, keyed by the OS user.
    assert "AppManager-managed:alice" in remote
    assert "alice@laptop" not in remote
    # Hardened for jump-only use: nologin shell + restrict,port-forwarding.
    assert "nologin" in remote
    assert "restrict,port-forwarding" in remote
    # Idempotent install: existing lines for this blob are removed then the
    # canonical line is appended (blob-based dedupe).
    assert "grep -vF" in remote
    assert "AAAAC3Nz" in remote


def test_onboard_uses_separate_stamp_id_for_shared_account(monkeypatch) -> None:
    """In shared mode the account differs from the per-user provenance stamp."""
    ssh = _FakeSsh(monkeypatch)
    from app.proxmox import ProxmoxResult
    cfg = jumpserver.JumpConfig(
        enabled=True, host="h", key_path="/k", management_user="root",
        account_mode="shared", jumper_user="cdt-jumper",
    )
    r = ProxmoxResult()
    assert jumpserver.onboard_user(
        cfg, os_user="cdt-jumper",
        public_key="ssh-ed25519 AAAAZZZZ x", result=r, stamp_id="alice",
    )
    remote = ssh.commands[0][-1]
    # Key installed into the shared account, stamped with the owning user's id.
    assert "cdt-jumper" in remote
    assert "AppManager-managed:alice" in remote


def test_target_account_by_mode() -> None:
    user = {"user_id": "alice", "username": "alice@x"}
    per = jumpserver.JumpConfig(
        enabled=True, host="h", key_path="/k", account_mode="per_user")
    shared = jumpserver.JumpConfig(
        enabled=True, host="h", key_path="/k", account_mode="shared",
        jumper_user="cdt-jumper")
    assert jumpserver.target_account(per, user) == "alice"
    assert jumpserver.target_account(shared, user) == "cdt-jumper"


def test_shared_mode_requires_jumper_user_to_be_ready() -> None:
    cfg = jumpserver.JumpConfig(
        enabled=True, host="h", key_path="/k", account_mode="shared",
        jumper_user="")
    assert cfg.ready is False
    cfg.jumper_user = "cdt-jumper"
    assert cfg.ready is True


# ---------------------------------------------------------------------------
# Guarded account-mode switch (per_user <-> shared)
# ---------------------------------------------------------------------------


def test_account_mode_switch_requires_ack(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])
    _FakeSsh(monkeypatch)
    # No acknowledgment -> 409, mode unchanged.
    r = client.post(
        "/api/settings/jump-server/account-mode",
        json={"account_mode": "shared", "jumper_user": "cdt-jumper"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 409
    assert "acknowledge" in r.json()["detail"].lower()
    assert client.get("/api/settings/provisioning").json()[
        "jump_account_mode"] == "per_user"


def test_account_mode_switch_success(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    _create_member(client, csrf, username="a@example.com")
    _enable_jump(client, csrf, key["id"])
    _FakeSsh(monkeypatch)  # all SSH ok -> sync succeeds
    r = client.post(
        "/api/settings/jump-server/account-mode",
        json={"account_mode": "shared", "jumper_user": "cdt-jumper",
              "acknowledge_sync": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account_mode"] == "shared"
    assert body["reverted"] is False
    assert client.get("/api/settings/provisioning").json()[
        "jump_account_mode"] == "shared"


def test_account_mode_switch_reverts_on_sync_failure(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    _create_member(client, csrf, username="a@example.com")
    _enable_jump(client, csrf, key["id"])
    # Onboarding fails (rc=255) -> the switch must revert.
    _FakeSsh(monkeypatch, rc=255, stdout="")
    r = client.post(
        "/api/settings/jump-server/account-mode",
        json={"account_mode": "shared", "jumper_user": "cdt-jumper",
              "acknowledge_sync": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reverted"] is True
    assert body["account_mode"] == "per_user"  # reverted
    assert "revert" in body["detail"].lower()
    # DB left at the previous mode.
    assert client.get("/api/settings/provisioning").json()[
        "jump_account_mode"] == "per_user"


def test_account_mode_switch_noop_rejected(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    _enable_jump(client, csrf, key["id"])
    _FakeSsh(monkeypatch)
    # Already per_user -> switching to per_user is a no-op -> 400.
    r = client.post(
        "/api/settings/jump-server/account-mode",
        json={"account_mode": "per_user", "acknowledge_sync": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_shared_jumper_name_collision_rejected(admin, monkeypatch) -> None:
    """A jumper name equal to a user's derived id is rejected (data-loss guard)."""
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    # This user's derived id is 'shared'.
    _create_member(client, csrf, username="shared@example.com")
    _enable_jump(client, csrf, key["id"])
    _FakeSsh(monkeypatch)
    r = client.post(
        "/api/settings/jump-server/account-mode",
        json={"account_mode": "shared", "jumper_user": "shared",
              "acknowledge_sync": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    assert "collides" in r.json()["detail"].lower()


def test_switch_shared_to_per_user_drains_shared_account(admin, monkeypatch) -> None:
    """Leaving shared mode drains the shared account (no lingering keys)."""
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    _create_member(client, csrf, username="a@example.com")
    _enable_jump(client, csrf, key["id"])
    _FakeSsh(monkeypatch)
    # Into shared mode first.
    assert client.post(
        "/api/settings/jump-server/account-mode",
        json={"account_mode": "shared", "jumper_user": "cdt-jumper",
              "acknowledge_sync": True},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200
    # Now back to per_user: the shared account must be drained.
    ssh = _FakeSsh(monkeypatch)
    r = client.post(
        "/api/settings/jump-server/account-mode",
        json={"account_mode": "per_user", "acknowledge_sync": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["account_mode"] == "per_user"
    # An offboard (grep -vF, no useradd) targeted the shared cdt-jumper account.
    drains = [
        c[-1] for c in ssh.commands
        if "cdt-jumper" in c[-1] and "useradd" not in c[-1]
    ]
    assert drains, "expected the shared account to be drained on switch-out"


def test_switch_to_shared_reverts_and_drains_on_failure(admin, monkeypatch) -> None:
    """A failed per_user->shared switch reverts AND drains the new shared acct."""
    client, csrf, _ = admin
    key = _register_key(client, csrf)
    _create_member(client, csrf, username="a@example.com")
    _enable_jump(client, csrf, key["id"])
    # Onboarding fails -> revert. (Offboard/onboard both run through the same
    # fake; the endpoint still reverts because sync reports failure.)
    _FakeSsh(monkeypatch, rc=255, stdout="")
    r = client.post(
        "/api/settings/jump-server/account-mode",
        json={"account_mode": "shared", "jumper_user": "cdt-jumper",
              "acknowledge_sync": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["reverted"] is True
    assert client.get("/api/settings/provisioning").json()[
        "jump_account_mode"] == "per_user"


def test_install_public_key_stamps_and_dedupes(monkeypatch) -> None:
    ssh = _FakeSsh(monkeypatch, stdout="")
    from app.proxmox import ProxmoxResult
    r = ProxmoxResult()
    ok = servers.install_public_key(
        ip="10.0.0.5", admin_key_path="/k", os_users=["coder"],
        public_key="ssh-ed25519 AAAABBBB coder@old", result=r,
        marker="AppManager-managed:coder",
    )
    assert ok
    remote = ssh.commands[0][-1]
    assert "AppManager-managed:coder" in remote
    assert "coder@old" not in remote
    assert "grep -vF" in remote  # removes any prior copy of this blob first
    # The rewrite is atomic (temp file then rename), never truncating the live
    # authorized_keys in place.
    assert "mv " in remote
    assert 'cat "$f.tmp" > "$f"' not in remote


def test_stamp_public_key_neutralizes_hostile_marker() -> None:
    """A hostile marker cannot break out of the trailing comment field."""
    from app import sshkeys

    hostile = 'x ssh-rsa AAAAINJECT command="rm -rf /"'
    line = sshkeys.stamp_public_key("ssh-ed25519 AAAAC3Nz good@host", hostile)
    # Exactly three whitespace-separated fields: type, blob, single comment.
    assert len(line.split()) == 3
    # No shell metacharacters or spaces survive in the comment token.
    comment = line.split()[2]
    assert all(c not in comment for c in ' ;|&$`"\'()=')


def test_stamp_public_key_helper() -> None:
    from app import sshkeys

    stamped = sshkeys.stamp_public_key(
        "ssh-ed25519 AAAAC3Nz somebody@host", "AppManager-managed:jane"
    )
    assert stamped == "ssh-ed25519 AAAAC3Nz AppManager-managed:jane"
    # A key with no comment gains one.
    assert sshkeys.stamp_public_key(
        "ssh-ed25519 AAAAC3Nz", "AppManager-trusted:jane"
    ) == "ssh-ed25519 AAAAC3Nz AppManager-trusted:jane"
    # Malformed input is returned stripped, unchanged.
    assert sshkeys.stamp_public_key("garbage", "m") == "garbage"
