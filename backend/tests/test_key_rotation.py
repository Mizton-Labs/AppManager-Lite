"""Key-rotation propagation and the SSH Configuration File (issue_015 ph. 4)."""

from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from app import proxmox, servers


_VERSION_OK = (200, {"data": {"version": "8.2.4"}})


class _FakeProxmox:
    def __init__(self, monkeypatch):
        monkeypatch.setattr(proxmox, "_http_request", self)
        monkeypatch.setattr(proxmox, "_sleep", lambda s: None)

    def __call__(self, method, url, *, headers, verify, json_body=None):
        if "/version" in url:
            return _VERSION_OK
        if "/cluster/resources" in url:
            return (200, {"data": [
                {"vmid": 9001, "name": "tpl", "type": "lxc", "template": 1,
                 "node": "pve1"},
            ]})
        if "/cluster/nextid" in url:
            return (200, {"data": "121"})
        if "/clone" in url or "/status/start" in url:
            return (200, {"data": "UPID:pve1:1:x:"})
        if "/tasks/" in url:
            return (200, {"data": {"status": "stopped", "exitstatus": "OK"}})
        if "/interfaces" in url:
            return (200, {"data": [{"name": "eth0", "inet": "10.0.7.42/24"}]})
        if "/config" in url:
            return (200, {"data": {"cores": 1, "memory": 1024,
                                   "rootfs": "l:x,size=8G"}})
        raise AssertionError(url)


class _FakeSsh:
    def __init__(self, monkeypatch, *, rc=0, stdout="updated: /root/.ssh/authorized_keys"):
        self.commands: list[list[str]] = []
        self.rc = rc
        self.stdout = stdout
        monkeypatch.setattr(servers, "_run", self)

    def __call__(self, argv, *, timeout=20):
        self.commands.append(argv)
        return subprocess.CompletedProcess(
            argv, returncode=self.rc, stdout=self.stdout, stderr=""
        )


def _create_member(client, csrf, username="rotuser@example.com", **extra):
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


def _provision_server(client, csrf, user_id, monkeypatch, name="box"):
    _FakeProxmox(monkeypatch)
    _setup = client.patch(
        "/api/settings/provisioning",
        json={"provider_type": "proxmox", "proxmox_url": "https://pve:8006",
              "proxmox_token_name": "svc@pam!a", "proxmox_api_key": "sek"},
        headers={"X-CSRF-Token": csrf},
    )
    assert _setup.status_code == 200
    template = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "Tpl", "kind": "lxc",
              "admin_ssh_key_path": "/keys/admin"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": name,
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# rotate_public_key unit behavior
# ---------------------------------------------------------------------------


def test_rotate_public_key_command_shape(monkeypatch) -> None:
    ssh = _FakeSsh(monkeypatch)
    result = proxmox.ProxmoxResult()
    outcome = servers.rotate_public_key(
        ip="10.0.7.42",
        admin_key_path="/keys/admin",
        old_public_key="ssh-ed25519 AAAAOLDBLOB old@comment",
        new_public_key="ssh-ed25519 AAAANEWBLOB rotuser@example.com",
        result=result,
    )
    assert outcome == "updated"
    argv = ssh.commands[0]
    assert argv[0] == "ssh"
    assert argv[argv.index("-i") + 1] == "/keys/admin"
    assert argv[-2] == "root@10.0.7.42"
    remote = argv[-1]
    assert "AAAAOLDBLOB" in remote  # matched by blob, not the full line
    assert "AAAANEWBLOB" in remote
    assert "authorized_keys" in remote


def test_rotate_public_key_rejects_malformed_keys(monkeypatch) -> None:
    _FakeSsh(monkeypatch)
    result = proxmox.ProxmoxResult()
    assert servers.rotate_public_key(
        ip="10.0.0.1", admin_key_path="/k",
        old_public_key="garbage", new_public_key="ssh-ed25519 AAAA x",
        result=result,
    ) == "failed"
    assert result.status == "failed"


def test_rotate_public_key_noop_when_old_key_absent(monkeypatch) -> None:
    _FakeSsh(monkeypatch, stdout="updated:")
    result = proxmox.ProxmoxResult()
    outcome = servers.rotate_public_key(
        ip="10.0.0.1", admin_key_path="/k",
        old_public_key="ssh-ed25519 AAAAOLD x",
        new_public_key="ssh-ed25519 AAAANEW x",
        result=result,
    )
    assert outcome == "noop"
    assert "nothing to rotate" in result.transcript


def test_rotation_script_works_on_real_shell(tmp_path, monkeypatch) -> None:
    """Run the generated remote script against a local sh with a fake tree.

    This pins the shell logic itself (grep -v exiting 1 on single-line
    files must not abort the rewrite - the common single-key case).
    """
    import subprocess as sp

    old = "ssh-ed25519 AAAAOLDBLOB user@old"
    new = "ssh-ed25519 AAAANEWBLOB user@new"
    root_keys = tmp_path / "root/.ssh/authorized_keys"
    home_keys = tmp_path / "home/alice/.ssh/authorized_keys"
    other_keys = tmp_path / "home/bob/.ssh/authorized_keys"
    for path, content in (
        (root_keys, f"{old}\nssh-ed25519 AAAAKEEP root@keep\n"),
        (home_keys, f"{old}\n"),  # single-line: the grep -v rc=1 case
        (other_keys, "ssh-ed25519 AAAAOTHER bob@x\n"),
    ):
        path.parent.mkdir(parents=True)
        path.write_text(content)

    captured: dict[str, str] = {}

    def local_run(argv, *, timeout=20):
        # Rewrite the script to scan the temp tree, then execute locally.
        script = argv[-1]
        assert script.startswith("sh -c ")
        inner = script[len("sh -c "):]
        import shlex as _shlex

        body = _shlex.split(inner)[0]
        body = body.replace("/root/.ssh/authorized_keys", str(root_keys))
        body = body.replace(
            "/home/*/.ssh/authorized_keys",
            f"{tmp_path}/home/*/.ssh/authorized_keys",
        )
        proc = sp.run(["sh", "-c", body], capture_output=True, text=True)
        captured["stdout"] = proc.stdout
        return proc

    monkeypatch.setattr(servers, "_run", local_run)
    result = proxmox.ProxmoxResult()
    outcome = servers.rotate_public_key(
        ip="127.0.0.1", admin_key_path="/k",
        old_public_key=old, new_public_key=new, result=result,
    )
    assert outcome == "updated", result.transcript

    # Old key removed everywhere, new key present, other lines untouched.
    assert old not in root_keys.read_text()
    assert new in root_keys.read_text()
    assert "AAAAKEEP" in root_keys.read_text()
    assert home_keys.read_text() == f"{new}\n"
    assert other_keys.read_text() == "ssh-ed25519 AAAAOTHER bob@x\n"
    assert not (tmp_path / "home/alice/.ssh/authorized_keys.tmp").exists()
    assert str(home_keys) in captured["stdout"]


# ---------------------------------------------------------------------------
# Regeneration propagates to servers
# ---------------------------------------------------------------------------


def test_regenerate_rotates_key_on_servers(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    ssh = _FakeSsh(monkeypatch)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    _provision_server(client, csrf, user_id, monkeypatch)

    member, member_csrf = _login(
        client.app, "rotuser@example.com", created["password"]
    )
    old_pub = member.get("/api/account/ssh-key").json()["public_key"]

    resp = member.post(
        "/api/account/ssh-key/regenerate",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["public_key"] != old_pub
    assert len(body["rotation"]) == 1
    entry = body["rotation"][0]
    assert entry == {
        "server": "box",
        "ip_address": "10.0.7.42",
        "status": "updated",
        "detail": entry["detail"],
    }
    # SSH used the template's admin key against the server's IP; the old
    # blob is referenced for removal and the new key for installation.
    rotation_cmds = [c for c in ssh.commands if "authorized_keys" in c[-1]]
    assert rotation_cmds
    argv = rotation_cmds[-1]
    assert argv[argv.index("-i") + 1] == "/keys/admin"
    assert argv[-2] == "root@10.0.7.42"
    old_blob = old_pub.split()[1]
    assert old_blob in argv[-1]
    assert body["public_key"].split()[1] in argv[-1]

    # Server log gained the rotation transcript; audit has no key material.
    server = member.get(f"/api/users/{user_id}/servers").json()[0]
    assert "key rotation" in server["last_log"]
    events = client.get("/api/audit?category=user").json()
    regen = [e for e in events if e["action"] == "ssh_key_regenerate"][0]
    assert "box=updated" in regen["detail"]
    assert "AAAA" not in regen["detail"]


def test_regenerate_rotates_via_registry_key(admin, monkeypatch) -> None:
    """Rotation resolves the admin key from the registry (admin_ssh_key_id)."""
    client, csrf, _ = admin
    ssh = _FakeSsh(monkeypatch)
    _FakeProxmox(monkeypatch)
    client.patch(
        "/api/settings/provisioning",
        json={"provider_type": "proxmox", "proxmox_url": "https://pve:8006",
              "proxmox_token_name": "svc@pam!a", "proxmox_api_key": "sek"},
        headers={"X-CSRF-Token": csrf},
    )
    # Register a path-kind key and a template that references it by id.
    key = client.post(
        "/api/settings/ssh-keys",
        json={"name": "reg key", "kind": "path", "path": "/reg/admin_key"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    template = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "Reg Tpl", "kind": "lxc",
              "admin_ssh_key_id": key["id"]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "box",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text

    # The created server persisted the registry key id.
    from app.db import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT admin_ssh_key_id FROM user_servers WHERE user_id=?",
            (user_id,),
        ).fetchone()
        assert row["admin_ssh_key_id"] == key["id"]

    member, member_csrf = _login(
        client.app, "rotuser@example.com", created["password"]
    )
    ssh.commands.clear()
    r = member.post(
        "/api/account/ssh-key/regenerate",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rotation"][0]["status"] == "updated"
    # Rotation used the registry-resolved key path.
    rot = [c for c in ssh.commands if "authorized_keys" in c[-1]][-1]
    assert rot[rot.index("-i") + 1] == "/reg/admin_key"


def test_regenerate_reports_skips_and_failures(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _provision_server(client, csrf, user_id, monkeypatch)

    # A second server without an IP (VM-style record, added via repository).
    from app.db import get_connection
    from app import repository

    with get_connection() as conn:
        repository.create_user_server(
            conn, user_id=user_id, name="no-ip", kind="vm",
            status="created",
        )

    _FakeSsh(monkeypatch, rc=255, stdout="")
    member, member_csrf = _login(
        client.app, "rotuser@example.com", created["password"]
    )
    resp = member.post(
        "/api/account/ssh-key/regenerate",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 200, resp.text
    rotation = {r["server"]: r for r in resp.json()["rotation"]}
    assert rotation["box"]["status"] == "failed"
    assert rotation["no-ip"]["status"] == "skipped"
    assert "no IP address" in rotation["no-ip"]["detail"]
    assert server["id"]  # keep flake-happy reference


# ---------------------------------------------------------------------------
# SSH Configuration File (generic fallback + user_id mapping)
# ---------------------------------------------------------------------------


def test_mapping_source_user_id(admin) -> None:
    client, csrf, _ = admin
    template = client.post(
        "/api/settings/bundle-templates",
        json={"name": "With user id", "content": "id=UID",
              "mappings": [{"field_name": "UID", "source": "user_id"}]},
        headers={"X-CSRF-Token": csrf},
    )
    assert template.status_code == 201, template.text
    created = _create_member(client, csrf, username="john.doe@example.com")
    member, _ = _login(client.app, "john.doe@example.com", created["password"])
    download = member.get(
        f"/api/account/bundles/{template.json()['id']}/download"
    )
    assert download.status_code == 200
    assert download.text == "id=john-doe"


def test_default_ssh_config_generated_from_servers(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    _FakeSsh(monkeypatch)
    _provision_server(client, csrf, user_id, monkeypatch, name="coder box")

    member, _ = _login(client.app, "rotuser@example.com", created["password"])
    options = member.get("/api/account/bundles").json()
    default = next(o for o in options if o["name"] == "SSH Config Default")
    download = member.get(f"/api/account/bundles/{default['id']}/download")
    assert download.status_code == 200
    text = download.text
    assert "Host coder-box" in text
    assert "Hostname 10.0.7.42" in text
    assert "User rotuser" in text
    assert "IdentityFile ~/.ssh/id_ed25519" in text
    assert "Host *" in text  # builtin keepalive stanza


def test_admin_key_path_never_in_server_responses(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    _FakeSsh(monkeypatch)
    _provision_server(client, csrf, user_id, monkeypatch)
    listed = client.get(f"/api/users/{user_id}/servers")
    assert listed.status_code == 200
    assert "admin_ssh_key_path" not in listed.text
    assert "/keys/admin" not in listed.text


def test_any_mappingless_template_serves_generic_config(admin) -> None:
    """Intentional per issue_015: mapping-less templates always render the
    dynamically generated SSH config, not their stored content."""
    client, csrf, _ = admin
    template = client.post(
        "/api/settings/bundle-templates",
        json={"name": "Static no mappings", "content": "literal text",
              "mappings": []},
        headers={"X-CSRF-Token": csrf},
    ).json()
    created = _create_member(client, csrf)
    member, _ = _login(client.app, "rotuser@example.com", created["password"])
    download = member.get(f"/api/account/bundles/{template['id']}/download")
    assert download.status_code == 200
    assert "literal text" not in download.text
    assert "No servers" in download.text


def test_default_ssh_config_without_servers(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    member, _ = _login(client.app, "rotuser@example.com", created["password"])
    options = member.get("/api/account/bundles").json()
    default = next(o for o in options if o["name"] == "SSH Config Default")
    download = member.get(f"/api/account/bundles/{default['id']}/download")
    assert download.status_code == 200
    # The built-in config always includes the Host * keepalive stanza, even
    # with no servers (no per-server Host blocks).
    assert "Host *" in download.text
    assert "ServerAliveInterval 60" in download.text
