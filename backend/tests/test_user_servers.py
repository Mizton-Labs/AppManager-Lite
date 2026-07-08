"""User-server creation, quotas, and lifecycle (issue_015 phase 3)."""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from app import proxmox, servers


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_VERSION_OK = (200, {"data": {"version": "8.2.4"}})


def _resources_payload(entries):
    return (200, {"data": entries})


_TEMPLATE_ENTRY = {
    "vmid": 9001, "name": "tpl-debian", "type": "lxc", "template": 1,
    "node": "pve1",
}


class _FakeProxmox:
    """Scripted responses for the proxmox HTTP seam, keyed by URL fragment."""

    def __init__(self, monkeypatch, *, template_resources=None,
                 clone_ok=True, start_ok=True, ip="10.0.7.42"):
        self.calls: list[str] = []
        self.template_resources = template_resources or {
            "cores": 2, "memory": 4096, "rootfs": "local:9001/x,size=20G",
        }
        self.clone_ok = clone_ok
        self.start_ok = start_ok
        self.ip = ip
        monkeypatch.setattr(proxmox, "_http_request", self)
        monkeypatch.setattr(proxmox, "_sleep", lambda s: None)

    def __call__(self, method, url, *, headers, verify, json_body=None):
        self.calls.append(f"{method} {url}")
        if "/version" in url:
            return _VERSION_OK
        if "/cluster/resources" in url:
            return _resources_payload([_TEMPLATE_ENTRY])
        if "/cluster/nextid" in url:
            return (200, {"data": "120"})
        if "/clone" in url:
            return (200, {"data": "UPID:pve1:0001:clone:"})
        if "/status/start" in url:
            return (200, {"data": "UPID:pve1:0002:start:"})
        if "/tasks/" in url:
            if "clone" in url and not self.clone_ok:
                return (200, {"data": {"status": "stopped",
                                       "exitstatus": "clone error"}})
            if "start" in url and not self.start_ok:
                return (200, {"data": {"status": "stopped",
                                       "exitstatus": "start error"}})
            return (200, {"data": {"status": "stopped", "exitstatus": "OK"}})
        if "/interfaces" in url:
            if not self.ip:
                return (200, {"data": []})
            return (200, {"data": [
                {"name": "lo", "inet": "127.0.0.1/8"},
                {"name": "eth0", "inet": f"{self.ip}/24"},
            ]})
        if "/config" in url and (json_body is None):
            return (200, {"data": self.template_resources})
        if "/config" in url or "/resize" in url:
            return (200, {"data": "UPID:pve1:0003:resize:"})
        raise AssertionError(f"unexpected URL: {url}")


class _FakeSsh:
    def __init__(self, monkeypatch, *, rc=0):
        self.commands: list[list[str]] = []
        self.rc = rc
        monkeypatch.setattr(servers, "_run", self)

    def __call__(self, argv, *, timeout=20):
        self.commands.append(argv)
        return subprocess.CompletedProcess(
            argv, returncode=self.rc, stdout="", stderr="ssh failed"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_provider(client, csrf):
    resp = client.patch(
        "/api/settings/provisioning",
        json={
            "provider_type": "proxmox",
            "proxmox_url": "https://pve:8006",
            "proxmox_token_name": "svc@pam!app",
            "proxmox_api_key": "sekret",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text


def _add_template(client, csrf, *, key_path="/home/svc/.ssh/id_ed25519"):
    resp = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "Debian Coder", "kind": "lxc",
              "admin_ssh_key_path": key_path},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_member(client, csrf, username="srvuser@example.com", **extra):
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
# Unit: name/ip/os-user validation
# ---------------------------------------------------------------------------


def test_server_name_and_ip_validation() -> None:
    assert servers.validate_server_name(" My Server-1 ") == "My Server-1"
    assert servers.hostname_for("My Server-1") == "my-server-1"
    for bad in ("", "-lead", "a" * 41, "semi;colon"):
        with pytest.raises(servers.ServerError):
            servers.validate_server_name(bad)
    assert servers.validate_ip("10.0.0.7") == "10.0.0.7"
    for bad_ip in ("999.1.1.1", "10.0.0", "evil"):
        with pytest.raises(servers.ServerError):
            servers.validate_ip(bad_ip)


def test_os_user_parsing() -> None:
    assert servers.parse_os_users("alice, bob-1,_svc") == ["alice", "bob-1", "_svc"]
    for bad in ("Root", "a b", "x;y", "-dash"):
        with pytest.raises(servers.ServerError):
            servers.parse_os_users(bad)


# ---------------------------------------------------------------------------
# Creation flows
# ---------------------------------------------------------------------------


def test_admin_creates_lxc_server_with_key_install(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    fake = _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]

    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "coder box",
              "install_pubkey": True, "pubkey_users": "srvuser, deploy"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    server = resp.json()
    assert server["status"] == "created"
    assert server["vmid"] == 120
    assert server["ip_address"] == "10.0.7.42"
    assert server["kind"] == "lxc"
    assert server["cpus"] == 2
    assert server["memory_gb"] == 4
    assert server["disk_gb"] == 20
    # Admin-created servers are exempt from user quotas.
    assert server["admin_modified"] is True
    assert "Server created successfully" in server["last_log"]
    assert "sekret" not in server["last_log"]

    # Key installed for both requested OS users via ssh -i <admin key>.
    assert len(ssh.commands) == 2
    for argv in ssh.commands:
        assert argv[0] == "ssh"
        assert argv[argv.index("-i") + 1] == "/home/svc/.ssh/id_ed25519"
        assert argv[-2] == "root@10.0.7.42"
        assert "ssh-ed25519" in argv[-1]

    listed = client.get(f"/api/users/{user_id}/servers")
    assert [s["name"] for s in listed.json()] == ["coder box"]


def test_failed_clone_recorded_as_failed(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, clone_ok=False)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]

    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "doomed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201
    server = resp.json()
    assert server["status"] == "failed"
    assert "clone error" in server["last_log"]


def test_self_service_creation_and_policy_gate(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )

    # Policy toggle off -> 403 even for self-service users.
    resp = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "mine"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 403

    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    resp = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "mine"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "created"
    # Self-created servers count against quotas.
    assert resp.json()["admin_modified"] is False


def test_normal_user_cannot_create_servers(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=False)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    resp = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "nope"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 403

    # And no user may touch someone else's servers.
    other = _create_member(client, csrf, username="other@example.com")
    resp = member.get(f"/api/users/{other['user']['id']}/servers")
    assert resp.status_code == 403


def test_max_servers_quota(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True,
              "provisioning_max_servers": 1},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    first = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "one"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert first.status_code == 201
    second = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "two"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert second.status_code == 400
    assert "limit" in second.json()["detail"].lower()


def test_resource_quota_blocks_oversized_template(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, template_resources={
        "cores": 16, "memory": 4096, "rootfs": "local:9001/x,size=20G",
    })
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    resp = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "huge"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 400
    assert "CPUs" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Updates: manual IP (VM), resource changes, deletion
# ---------------------------------------------------------------------------


def _create_server_for(client, csrf, user_id, template_id, name="box"):
    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template_id, "name": name,
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_manual_ip_entry(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])

    resp = client.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"ip_address": "10.9.9.9"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ip_address"] == "10.9.9.9"

    bad = client.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"ip_address": "999.9.9.9"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400


def test_admin_resource_change_marks_admin_modified(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    # Self-created server (counts against quota).
    resp = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "mine",
              "install_pubkey": False},
        headers={"X-CSRF-Token": member_csrf},
    )
    server = resp.json()
    assert server["admin_modified"] is False

    # Admin change exceeding the per-user cap is allowed and exempts the
    # server from quota accounting afterwards.
    resp = client.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 32},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cpus"] == 32
    assert resp.json()["admin_modified"] is True


def test_self_service_resource_change_policy_and_quota(
    admin, monkeypatch
) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    server = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "mine",
              "install_pubkey": False},
        headers={"X-CSRF-Token": member_csrf},
    ).json()

    # Resource edits disabled by policy -> 403.
    resp = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 4},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 403

    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_allow_resource_edit": True},
        headers={"X-CSRF-Token": csrf},
    )
    ok = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 4},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["cpus"] == 4
    assert ok.json()["admin_modified"] is False

    # Exceeding the cap -> 400 with a helpful message.
    too_big = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 50},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert too_big.status_code == 400
    assert "limit exceeded" in too_big.json()["detail"]

    # Disk shrink is rejected by the client module.
    shrink = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"disk_gb": 5},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert shrink.status_code == 502
    assert "grown" in shrink.json()["detail"]


def test_delete_server_record_only(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])

    resp = client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    assert "not deleted" in resp.json()["detail"]
    assert client.get(f"/api/users/{user_id}/servers").json() == []


def test_duplicate_server_name_rejected(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    _create_server_for(client, csrf, user_id, template["id"], name="box")
    dup = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "BOX"},
        headers={"X-CSRF-Token": csrf},
    )
    assert dup.status_code == 409


def test_account_server_helpers(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    created = _create_member(client, csrf, self_service=True)
    member, _ = _login(client.app, "srvuser@example.com", created["password"])

    # Provider unconfigured -> cannot create, reason given.
    access = member.get("/api/account/server-access").json()
    assert access["can_create"] is False
    assert "provider" in access["reason"].lower()

    _setup_provider(client, csrf)
    template = _add_template(client, csrf)

    # Self-service toggle still off.
    access = member.get("/api/account/server-access").json()
    assert access["can_create"] is False
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    access = member.get("/api/account/server-access").json()
    assert access["can_create"] is True

    # Template options hide vmid and the admin key path.
    options = member.get("/api/account/server-templates")
    assert options.status_code == 200
    assert options.json() == [
        {"id": template["id"], "name": "Debian Coder", "kind": "lxc"}
    ]
    assert "vmid" not in options.text
    assert "id_ed25519" not in options.text


def test_key_install_failure_keeps_created_record(admin, monkeypatch) -> None:
    """A running guest whose key install failed still counts and is tracked."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch, rc=255)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "box",
              "install_pubkey": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201
    server = resp.json()
    # The clone exists (vmid recorded), so the record is 'created' - the
    # transcript carries the key-install error.
    assert server["status"] == "created"
    assert server["vmid"] == 120
    assert "key installation" in server["last_log"]
    # Default OS user is the owner's derived user-id.
    assert "srvuser" in server["last_log"]


def test_failed_with_guest_counts_against_quota(admin, monkeypatch) -> None:
    """Guests that were cloned still consume the max-servers quota even
    when a later step failed (no unlimited-provisioning loophole)."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, ip="")  # IP discovery fails after clone+start
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True,
              "provisioning_max_servers": 1},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    first = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "one",
              "install_pubkey": False},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert first.status_code == 201
    body = first.json()
    assert body["vmid"] == 120  # the guest was cloned
    assert "could not determine the container IP" in body["last_log"]

    second = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "two",
              "install_pubkey": False},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert second.status_code == 400
    assert "limit" in second.json()["detail"].lower()


def test_quota_check_fails_closed_when_provider_unreadable(
    admin, monkeypatch
) -> None:
    client, csrf, _ = admin
    fake = _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )

    def broken(method, url, **kw):
        if "/cluster/resources" in url:
            return (500, "boom")
        return fake(method, url, **kw)

    import app.proxmox as proxmox_mod

    monkeypatch.setattr(proxmox_mod, "_http_request", broken)
    resp = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "mine"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 502
    assert "quota" in resp.json()["detail"].lower()


def test_vm_flow_clones_without_start_or_key(admin, monkeypatch) -> None:
    client, csrf, _ = admin

    class _VmProxmox(_FakeProxmox):
        def __call__(self, method, url, *, headers, verify, json_body=None):
            if "/cluster/resources" in url:
                self.calls.append(f"{method} {url}")
                return (200, {"data": [
                    {"vmid": 9100, "name": "tpl-win", "type": "qemu",
                     "template": 1, "node": "pve2"},
                ]})
            if "/status/start" in url or "/interfaces" in url:
                raise AssertionError("VM flow must not start or read IPs")
            return super().__call__(
                method, url, headers=headers, verify=verify,
                json_body=json_body,
            )

    _VmProxmox(monkeypatch, template_resources={"cores": 4, "memory": 8192})
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    resp = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9100, "name": "Win VM", "kind": "vm"},
        headers={"X-CSRF-Token": csrf},
    )
    template = resp.json()
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]

    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "winbox",
              "install_pubkey": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    server = resp.json()
    assert server["status"] == "created"
    assert server["kind"] == "vm"
    assert server["ip_address"] == ""
    assert "configure it in Proxmox" in server["last_log"]
    assert "skipped for VMs" in server["last_log"]
    assert ssh.commands == []  # no SSH attempted


def test_ip_update_authorization(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    # Self-created LXC: the owner cannot rewrite its auto-discovered IP.
    server = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "mine",
              "install_pubkey": False},
        headers={"X-CSRF-Token": member_csrf},
    ).json()
    denied = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"ip_address": "10.66.66.66"},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert denied.status_code == 403
    # Admins may correct any record.
    ok = client.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"ip_address": "10.66.66.66"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200


def test_non_self_service_owner_cannot_delete(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf, self_service=False)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    resp = member.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 403
