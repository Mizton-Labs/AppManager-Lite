"""Server template options: main user, sudo, trusted mesh (issue_015-r2 C)."""

from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from app import proxmox, servers


_VERSION_OK = (200, {"data": {"version": "8.2.4"}})
_TEMPLATE_ENTRY = {
    "vmid": 9001, "name": "tpl", "type": "lxc", "template": 1, "node": "pve1",
}


class _FakeProxmox:
    """Scripted proxmox HTTP seam; each created server gets a distinct IP."""

    def __init__(self, monkeypatch, ips=("10.0.7.11", "10.0.7.12", "10.0.7.13")):
        self._ips = list(ips)
        self._assigned: dict[int, str] = {}
        self._next_id = 120
        monkeypatch.setattr(proxmox, "_http_request", self)
        monkeypatch.setattr(proxmox, "_sleep", lambda s: None)

    def __call__(self, method, url, *, headers, verify, json_body=None):
        if "/version" in url:
            return _VERSION_OK
        if "/cluster/resources" in url:
            return (200, {"data": [_TEMPLATE_ENTRY]})
        if "/cluster/nextid" in url:
            vmid = self._next_id
            self._next_id += 1
            return (200, {"data": str(vmid)})
        if "/clone" in url:
            return (200, {"data": "UPID:pve1:1:clone:"})
        if "/status/start" in url:
            return (200, {"data": "UPID:pve1:2:start:"})
        if "/tasks/" in url:
            return (200, {"data": {"status": "stopped", "exitstatus": "OK"}})
        if "/interfaces" in url:
            # Assign the next free IP to whichever new vmid is being read.
            import re
            m = re.search(r"/lxc/(\d+)/interfaces", url)
            vmid = int(m.group(1))
            if vmid not in self._assigned and self._ips:
                self._assigned[vmid] = self._ips.pop(0)
            ip = self._assigned.get(vmid, "10.0.7.99")
            return (200, {"data": [{"name": "eth0", "inet": f"{ip}/24"}]})
        if "/config" in url:
            return (200, {"data": {"cores": 1, "memory": 1024,
                                   "rootfs": "l:x,size=8G"}})
        raise AssertionError(url)


class _FakeSsh:
    def __init__(self, monkeypatch, *, rc=0):
        self.commands: list[list[str]] = []
        self.rc = rc
        monkeypatch.setattr(servers, "_run", self)

    def __call__(self, argv, *, timeout=20):
        self.commands.append(argv)
        remote = argv[-1]
        # keygen step must echo a public key on stdout.
        stdout = ""
        if "ssh-keygen" in remote and "cat" in remote:
            stdout = "ssh-ed25519 AAAAMESHKEY generated@server\n"
        return subprocess.CompletedProcess(
            argv, returncode=self.rc, stdout=stdout, stderr="err"
        )


def _setup_provider(client, csrf):
    assert client.patch(
        "/api/settings/provisioning",
        json={"provider_type": "proxmox", "proxmox_url": "https://pve:8006",
              "proxmox_token_name": "svc@pam!a", "proxmox_api_key": "sek"},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200


def _add_template(client, csrf, **opts):
    body = {"vmid": 9001, "name": opts.pop("name", "T"), "kind": "lxc",
            "admin_ssh_key_path": "/keys/admin"}
    body.update(opts)
    r = client.post("/api/settings/server-templates", json=body,
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text
    return r.json()


def _create_member(client, csrf, username="tuser@example.com"):
    r = client.post("/api/users",
                    json={"username": username, "role": "user", "teams": [],
                          "apps_server": "a.example.com"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text
    return r.json()


def _mk_server(client, csrf, user_id, template_id, name):
    r = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template_id, "name": name, "install_pubkey": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Template option persistence + defaults
# ---------------------------------------------------------------------------


def test_template_options_default_on_and_roundtrip(admin) -> None:
    client, csrf, _ = admin
    t = _add_template(client, csrf, name="Defaults")
    assert t["enable_sudo"] is True
    assert t["enable_trusted_access"] is True
    assert t["main_os_user"] == ""

    t2 = _add_template(client, csrf, name="Custom", main_os_user="coder",
                       enable_sudo=False, enable_trusted_access=False)
    assert t2["main_os_user"] == "coder"
    assert t2["enable_sudo"] is False
    assert t2["enable_trusted_access"] is False

    # Update clears/sets fields.
    upd = client.patch(
        f"/api/settings/server-templates/{t2['id']}",
        json={"main_os_user": "dev", "enable_sudo": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert upd.status_code == 200
    assert upd.json()["main_os_user"] == "dev"
    assert upd.json()["enable_sudo"] is True


def test_invalid_main_os_user_rejected(admin) -> None:
    client, csrf, _ = admin
    r = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "Bad", "kind": "lxc",
              "main_os_user": "Bad User"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Main user + sudo on server creation
# ---------------------------------------------------------------------------


def test_key_installed_only_for_main_user_with_sudo(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="Coder", main_os_user="coder",
                             enable_sudo=True, enable_trusted_access=False)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]

    server = _mk_server(client, csrf, uid, template["id"], "box")
    assert server["status"] == "created"
    # The install command targets 'coder' (main user), not the derived user id,
    # and includes the sudo group step.
    install_cmds = [c for c in ssh.commands if "authorized_keys" in c[-1]]
    assert install_cmds
    joined = install_cmds[-1][-1]
    # Key installed into the 'coder' home (chown -R coder), not derived user id.
    assert "chown -R coder:" in joined
    assert "chown -R tuser" not in joined
    assert "getent passwd coder" in joined
    assert "usermod -aG sudo coder" in joined


def test_no_sudo_step_when_disabled(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="NoSudo", main_os_user="coder",
                             enable_sudo=False, enable_trusted_access=False)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _mk_server(client, csrf, uid, template["id"], "box")
    install_cmds = [c for c in ssh.commands if "authorized_keys" in c[-1]]
    assert install_cmds
    assert all("usermod" not in c[-1] for c in install_cmds)


# ---------------------------------------------------------------------------
# Trusted mesh
# ---------------------------------------------------------------------------


def test_trusted_mesh_established_on_second_server(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="Trust", main_os_user="coder",
                             enable_sudo=False, enable_trusted_access=True)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]

    # First server: only one server, so no mesh yet.
    _mk_server(client, csrf, uid, template["id"], "s1")
    keygen_after_first = [c for c in ssh.commands if "ssh-keygen" in c[-1]]
    assert keygen_after_first == []  # <2 servers, mesh skipped

    ssh.commands.clear()
    # Second server: mesh reconciles across both.
    s2 = _mk_server(client, csrf, uid, template["id"], "s2")
    assert "trusted access" in s2["last_log"].lower()
    keygen_cmds = [c for c in ssh.commands if "ssh-keygen" in c[-1]]
    # Both servers get a keygen/read-pub step.
    assert len(keygen_cmds) == 2
    # Each server has the other's pubkey installed for 'coder'.
    mesh_installs = [
        c for c in ssh.commands
        if "authorized_keys" in c[-1] and "AAAAMESHKEY" in c[-1]
    ]
    assert len(mesh_installs) >= 2
    for c in mesh_installs:
        assert "coder" in c[-1]


def test_reconcile_trusted_mesh_unit(monkeypatch) -> None:
    ssh = _FakeSsh(monkeypatch)
    result = proxmox.ProxmoxResult()
    ok = servers.reconcile_trusted_mesh(
        servers=[{"ip_address": "10.0.0.1"}, {"ip_address": "10.0.0.2"}],
        admin_key_path="/keys/admin",
        os_user="coder",
        result=result,
    )
    assert ok
    # 2 keygen reads + 2 cross-installs (each server gets the other's key).
    keygen = [c for c in ssh.commands if "ssh-keygen" in c[-1]]
    installs = [c for c in ssh.commands
                if "authorized_keys" in c[-1] and "AAAAMESHKEY" in c[-1]]
    assert len(keygen) == 2
    assert len(installs) == 2


def test_trusted_mesh_noop_single_server(monkeypatch) -> None:
    ssh = _FakeSsh(monkeypatch)
    result = proxmox.ProxmoxResult()
    ok = servers.reconcile_trusted_mesh(
        servers=[{"ip_address": "10.0.0.1"}],
        admin_key_path="/k", os_user="coder", result=result,
    )
    assert ok
    assert ssh.commands == []
    assert "fewer than two" in result.transcript


def test_trusted_enabled_without_main_user_notes_and_skips(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    # trusted access on, but no main_os_user
    template = _add_template(client, csrf, name="T", main_os_user="",
                             enable_trusted_access=True)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _mk_server(client, csrf, uid, template["id"], "s1")
    s2 = _mk_server(client, csrf, uid, template["id"], "s2")
    assert "no main user" in s2["last_log"].lower()
    assert [c for c in ssh.commands if "ssh-keygen" in c[-1]] == []


def test_mesh_failure_does_not_block_creation(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch, rc=255)  # every SSH fails, incl. mesh
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="T", main_os_user="coder",
                             enable_sudo=False, enable_trusted_access=True)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _mk_server(client, csrf, uid, template["id"], "s1")
    # Second create still returns 201 despite mesh SSH failures.
    r = client.post(
        f"/api/users/{uid}/servers",
        json={"template_id": template["id"], "name": "s2",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201


def test_vm_joins_mesh_on_ip_entry(admin, monkeypatch) -> None:
    client, csrf, _ = admin

    class _VmProxmox(_FakeProxmox):
        def __call__(self, method, url, *, headers, verify, json_body=None):
            if "/cluster/resources" in url:
                return (200, {"data": [
                    {"vmid": 9100, "name": "tpl-vm", "type": "qemu",
                     "template": 1, "node": "pve2"},
                ]})
            if "/status/start" in url or "/interfaces" in url:
                raise AssertionError("VM must not start or read IP")
            return super().__call__(method, url, headers=headers,
                                    verify=verify, json_body=json_body)

    _VmProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    r = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9100, "name": "VMt", "kind": "vm",
              "admin_ssh_key_path": "/keys/admin", "main_os_user": "coder",
              "enable_trusted_access": True},
        headers={"X-CSRF-Token": csrf},
    )
    template = r.json()
    created = _create_member(client, csrf)
    uid = created["user"]["id"]

    # An LXC peer (trusted, same main user) so the mesh has 2 members.
    lxc_tpl = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "LXCt", "kind": "lxc",
              "admin_ssh_key_path": "/keys/admin", "main_os_user": "coder",
              "enable_trusted_access": True},
        headers={"X-CSRF-Token": csrf},
    )
    # LXC uses a different proxmox fake path; skip actual LXC create here and
    # instead insert a reachable reference peer directly.
    from app.db import get_connection
    with get_connection() as conn:
        from app import repository
        repository.create_user_server(
            conn, user_id=uid, name="peer", kind="lxc",
            ip_address="10.0.9.9", status="created",
            template_id=lxc_tpl.json()["id"], admin_ssh_key_path="/keys/admin",
        )

    vm = client.post(
        f"/api/users/{uid}/servers",
        json={"template_id": template["id"], "name": "winbox",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert vm["ip_address"] == ""  # VM has no IP yet -> no mesh
    ssh.commands.clear()

    # Enter the VM IP -> mesh reconciles across VM + peer.
    upd = client.patch(
        f"/api/users/{uid}/servers/{vm['id']}",
        json={"ip_address": "10.0.9.10"},
        headers={"X-CSRF-Token": csrf},
    )
    assert upd.status_code == 200, upd.text
    keygen = [c for c in ssh.commands if "ssh-keygen" in c[-1]]
    assert len(keygen) == 2  # both VM and peer keyed
    updated = client.get(f"/api/users/{uid}/servers").json()
    vm_row = [s for s in updated if s["name"] == "winbox"][0]
    assert "trusted access" in vm_row["last_log"].lower()
