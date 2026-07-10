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

    def __init__(self, monkeypatch, ips=("10.0.7.11", "10.0.7.12", "10.0.7.13"),
                 realms=None):
        self._ips = list(ips)
        self._assigned: dict[int, str] = {}
        self._next_id = 120
        # issue_025: observable pool state for assertions.
        self.realms = realms if realms is not None else [
            {"realm": "pam", "type": "pam", "comment": "Linux PAM"},
            {"realm": "pve", "type": "pve"},
        ]
        self.pools: dict[str, list[str]] = {}
        self.pool_calls: list[tuple[str, str]] = []  # (op, poolid)
        monkeypatch.setattr(proxmox, "_http_request", self)
        monkeypatch.setattr(proxmox, "_sleep", lambda s: None)

    def __call__(self, method, url, *, headers, verify, json_body=None):
        if "/version" in url:
            return _VERSION_OK
        if "/access/domains" in url:
            return (200, {"data": self.realms})
        if url.rstrip("/").endswith("/pools") and method == "GET":
            return (200, {"data": [
                {"poolid": pid} for pid in self.pools
            ]})
        if url.rstrip("/").endswith("/pools") and method == "POST":
            pid = (json_body or {}).get("poolid", "")
            self.pool_calls.append(("create", pid))
            self.pools.setdefault(pid, [])
            return (200, {"data": None})
        if "/pools/" in url and method == "PUT":
            import re as _re
            from urllib.parse import unquote
            m = _re.search(r"/pools/([^/?]+)", url)
            pid = unquote(m.group(1))
            self.pool_calls.append(("add", pid))
            vms = str((json_body or {}).get("vms", ""))
            self.pools.setdefault(pid, []).append(vms)
            return (200, {"data": None})
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


def test_apps_server_flag_defaults_off_and_roundtrips(admin) -> None:
    client, csrf, _ = admin
    default = _add_template(client, csrf, name="PlainSrv")
    assert default["is_apps_server"] is False

    apps = _add_template(client, csrf, name="AppsSrv", is_apps_server=True)
    assert apps["is_apps_server"] is True

    # Exposed to non-admins via the account options endpoint.
    options = client.get("/api/account/server-templates").json()
    by_name = {o["name"]: o for o in options}
    assert by_name["AppsSrv"]["is_apps_server"] is True
    assert by_name["PlainSrv"]["is_apps_server"] is False

    # Toggle off via PATCH.
    upd = client.patch(
        f"/api/settings/server-templates/{apps['id']}",
        json={"is_apps_server": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert upd.status_code == 200
    assert upd.json()["is_apps_server"] is False


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
    # No sudo/wheel group assignment (shell normalization is independent of
    # enable_sudo and may still issue its own usermod -s call).
    assert all("usermod -aG sudo" not in c[-1] for c in install_cmds)
    assert all("usermod -aG wheel" not in c[-1] for c in install_cmds)


def test_main_os_user_account_defaults_to_bash(admin, monkeypatch) -> None:
    """A template-configured main_os_user gets its account ensured with bash."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="MainUserBash",
                             main_os_user="cdt-coder")
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _mk_server(client, csrf, uid, template["id"], "box")
    install_cmds = [c for c in ssh.commands if "authorized_keys" in c[-1]]
    assert install_cmds
    assert all("useradd -m -s /bin/bash cdt-coder" in c[-1] for c in install_cmds)
    assert all("usermod -s /bin/bash cdt-coder" in c[-1] for c in install_cmds)


def test_free_form_pubkey_users_never_auto_creates_accounts(admin, monkeypatch) -> None:
    """Without a configured main_os_user, caller-supplied pubkey_users must
    never trigger account auto-creation/shell normalization - only a
    template-configured main user is trusted enough for that (issue found in
    security review: free text must not let a non-admin self-service request
    auto-create arbitrary OS accounts)."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="NoMainUser")
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    r = client.post(
        f"/api/users/{uid}/servers",
        json={"template_id": template["id"], "name": "box2",
              "install_pubkey": True, "pubkey_users": "arbitrary-name"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text
    install_cmds = [c for c in ssh.commands if "authorized_keys" in c[-1]]
    assert install_cmds
    for c in install_cmds:
        assert "useradd" not in c[-1]
        assert "usermod -s" not in c[-1]
        assert '"no such user"; exit 1' in c[-1]


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
    status = servers.reconcile_trusted_mesh(
        servers=[{"ip_address": "10.0.0.1"}, {"ip_address": "10.0.0.2"}],
        admin_key_path="/keys/admin",
        os_user="coder",
        result=result,
    )
    assert status == "established"
    # 2 keygen reads + 2 cross-installs (each server gets the other's key).
    keygen = [c for c in ssh.commands if "ssh-keygen" in c[-1]]
    installs = [c for c in ssh.commands
                if "authorized_keys" in c[-1] and "AAAAMESHKEY" in c[-1]]
    assert len(keygen) == 2
    assert len(installs) == 2
    # Mesh keys are stamped as AppManager-managed trusted keys.
    assert all("AppManager-trusted:coder" in c[-1] for c in installs)


def test_trusted_mesh_noop_single_server(monkeypatch) -> None:
    ssh = _FakeSsh(monkeypatch)
    result = proxmox.ProxmoxResult()
    status = servers.reconcile_trusted_mesh(
        servers=[{"ip_address": "10.0.0.1"}],
        admin_key_path="/k", os_user="coder", result=result,
    )
    assert status == "single_server"
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
    assert "no main os user" in s2["last_log"].lower()
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
    vm_row = [s for s in updated if s["name"] == vm["name"]][0]
    assert "trusted access" in vm_row["last_log"].lower()


# ---------------------------------------------------------------------------
# issue_023: mesh grouping, root-auth detection, deferred re-mesh, audit
# ---------------------------------------------------------------------------


class _AuthFailSsh:
    """SSH seam where the mesh (root@) connections are rejected by auth, but the
    initial provisioning key install (non-mesh) still succeeds."""

    def __init__(self, monkeypatch):
        self.commands: list[list[str]] = []
        monkeypatch.setattr(servers, "_run", self)

    def __call__(self, argv, *, timeout=20):
        self.commands.append(argv)
        remote = argv[-1]
        # The mesh uses ssh-keygen (read pub) and installs AAAAMESHKEY; simulate
        # a root-auth rejection for those, success otherwise.
        if "ssh-keygen" in remote or "AAAAMESHKEY" in remote:
            return subprocess.CompletedProcess(
                argv, returncode=255,
                stdout="", stderr="root@10.0.7.11: Permission denied (publickey).",
            )
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")


def test_mesh_meshes_all_groups_sharing_a_main_user(admin, monkeypatch) -> None:
    """issue_023: all of the user's reachable trusted servers that share a
    main OS user are meshed together, even across different templates."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    t1 = _add_template(client, csrf, name="A", main_os_user="coder",
                       enable_sudo=False, enable_trusted_access=True)
    # A second, different template that shares the same main OS user.
    t2 = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "B", "kind": "lxc",
              "admin_ssh_key_path": "/keys/admin", "main_os_user": "coder",
              "enable_sudo": False, "enable_trusted_access": True},
        headers={"X-CSRF-Token": csrf},
    ).json()
    created = _create_member(client, csrf)
    uid = created["user"]["id"]

    _mk_server(client, csrf, uid, t1["id"], "s1")
    ssh.commands.clear()
    # Second server from a DIFFERENT template but same main user -> meshed.
    s2 = _mk_server(client, csrf, uid, t2["id"], "s2")
    assert "mesh established" in s2["last_log"].lower()
    installs = [
        c for c in ssh.commands
        if "authorized_keys" in c[-1] and "AAAAMESHKEY" in c[-1]
    ]
    assert len(installs) >= 2  # keys cross-installed across the two templates


def test_mesh_root_auth_failure_is_actionable_and_audited(
    admin, monkeypatch
) -> None:
    """issue_023: a root-auth failure produces an actionable message and a
    server_mesh audit record (not a silent skip)."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _AuthFailSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="Trust", main_os_user="coder",
                             enable_sudo=False, enable_trusted_access=True)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _mk_server(client, csrf, uid, template["id"], "s1")
    s2 = _mk_server(client, csrf, uid, template["id"], "s2")

    log = s2["last_log"].lower()
    assert "cannot ssh as root" in log
    assert "authorizes it for root" in log

    events = client.get("/api/audit", params={"category": "user"}).json()
    mesh_events = [e for e in events if e["action"] == "server_mesh"]
    assert mesh_events, "expected a server_mesh audit event"
    assert any("status=failed" in e.get("detail", "") for e in mesh_events)


def test_mesh_records_no_main_user_audit(admin, monkeypatch) -> None:
    """A trusted template with no main OS user records a clear skip reason."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="T", main_os_user="",
                             enable_trusted_access=True)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _mk_server(client, csrf, uid, template["id"], "s1")
    _mk_server(client, csrf, uid, template["id"], "s2")

    events = client.get("/api/audit", params={"category": "user"}).json()
    mesh_events = [e for e in events if e["action"] == "server_mesh"]
    assert any(
        "no_main_user" in e.get("detail", "") for e in mesh_events
    )


def _sync_remesh(monkeypatch):
    """Run the deferred re-mesh inline (not in a background thread) and start
    with a clean per-process signature cache so assertions are deterministic."""
    from app.routers import provisioning
    monkeypatch.setattr(provisioning, "_MESH_REMESH_ASYNC", False)
    provisioning._mesh_signatures.clear()
    provisioning._mesh_inflight.clear()


def test_deferred_lxc_remesh_on_list_load(admin, monkeypatch) -> None:
    """issue_023: an LXC that had no IP at creation (so it was never meshed) is
    reconciled when the server list is next loaded once its IP is present."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    ssh = _FakeSsh(monkeypatch)
    _sync_remesh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="Trust", main_os_user="coder",
                             enable_sudo=False, enable_trusted_access=True)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]

    # Seed two reachable trusted servers directly (as if their IPs were
    # backfilled after creation, so no create-time mesh ran).
    from app.db import get_connection
    from app import repository
    with get_connection() as conn:
        for i, ip in enumerate(("10.0.5.1", "10.0.5.2"), start=1):
            repository.create_user_server(
                conn, user_id=uid, name=f"late{i}", kind="lxc",
                ip_address=ip, status="created",
                template_id=template["id"], admin_ssh_key_path="/keys/admin",
            )

    ssh.commands.clear()
    # Listing triggers the lazy re-mesh (runs inline in tests).
    r = client.get(f"/api/users/{uid}/servers")
    assert r.status_code == 200
    keygen = [c for c in ssh.commands if "ssh-keygen" in c[-1]]
    assert len(keygen) == 2  # both late servers meshed on list load

    # A second identical listing does not re-run the mesh (signature unchanged).
    ssh.commands.clear()
    client.get(f"/api/users/{uid}/servers")
    assert [c for c in ssh.commands if "ssh-keygen" in c[-1]] == []


def test_deferred_remesh_retries_after_transient_failure(
    admin, monkeypatch
) -> None:
    """issue_023: a mesh that fails is NOT cached as done -- it re-runs on the
    next list load (so a transient sshd-not-ready failure recovers)."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _sync_remesh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="Trust", main_os_user="coder",
                             enable_sudo=False, enable_trusted_access=True)
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    from app.db import get_connection
    from app import repository
    with get_connection() as conn:
        for i, ip in enumerate(("10.0.6.1", "10.0.6.2"), start=1):
            repository.create_user_server(
                conn, user_id=uid, name=f"late{i}", kind="lxc",
                ip_address=ip, status="created",
                template_id=template["id"], admin_ssh_key_path="/keys/admin",
            )

    # First: every mesh SSH fails transiently (connection refused).
    class _FailingSsh:
        def __init__(self):
            self.commands: list[list[str]] = []

        def __call__(self, argv, *, timeout=20):
            self.commands.append(argv)
            return subprocess.CompletedProcess(
                argv, returncode=255, stdout="",
                stderr="connect to host 10.0.6.1 port 22: Connection refused",
            )

    failing = _FailingSsh()
    monkeypatch.setattr(servers, "_run", failing)
    # Keep the mesh retry fast so the test doesn't sleep.
    monkeypatch.setattr(servers, "_MESH_SSH_ATTEMPTS", 1)
    client.get(f"/api/users/{uid}/servers")
    assert any("ssh-keygen" in c[-1] for c in failing.commands)

    # Next load: the failure was NOT cached, so the mesh re-runs -- and now the
    # SSH succeeds.
    ok = _FakeSsh(monkeypatch)
    client.get(f"/api/users/{uid}/servers")
    assert len([c for c in ok.commands if "ssh-keygen" in c[-1]]) == 2


# ---------------------------------------------------------------------------
# issue_025: Proxmox realms + auto add-to-pool
# ---------------------------------------------------------------------------


def test_realms_endpoint_lists_provider_realms(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, realms=[
        {"realm": "pam", "type": "pam", "comment": "Linux PAM"},
        {"realm": "corp-ldap", "type": "ldap", "comment": "Corp LDAP"},
    ])
    _setup_provider(client, csrf)
    r = client.get("/api/settings/provisioning/realms")
    assert r.status_code == 200, r.text
    realms = {x["realm"]: x for x in r.json()}
    assert "pam" in realms and "corp-ldap" in realms
    assert realms["corp-ldap"]["type"] == "ldap"


def test_realms_endpoint_empty_when_provider_unconfigured(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    # No provider configured -> graceful empty list (never an error).
    r = client.get("/api/settings/provisioning/realms")
    assert r.status_code == 200
    assert r.json() == []


def test_provisioning_settings_realms_prefix_toggle_roundtrip(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _setup_provider(client, csrf)
    r = client.patch(
        "/api/settings/provisioning",
        json={
            "proxmox_realms": ["pve", "pve", "corp-ldap", ""],  # dedup + drop blank
            "proxmox_pool_prefix": "lab-",
            "provisioning_add_to_pool": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proxmox_realms"] == ["pve", "corp-ldap"]
    assert body["proxmox_pool_prefix"] == "lab-"
    assert body["provisioning_add_to_pool"] is False
    # Persisted across reads.
    got = client.get("/api/settings/provisioning").json()
    assert got["proxmox_realms"] == ["pve", "corp-ldap"]
    assert got["provisioning_add_to_pool"] is False


def test_provisioning_add_to_pool_defaults_on(admin) -> None:
    client, csrf, _ = admin
    assert client.get("/api/settings/provisioning").json()["provisioning_add_to_pool"] is True


def test_invalid_pool_prefix_rejected(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _setup_provider(client, csrf)
    r = client.patch(
        "/api/settings/provisioning",
        json={"proxmox_pool_prefix": "bad prefix!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422


def test_server_create_adds_to_pool_creating_it_when_missing(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    fake = _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    # Prefix the pool ids.
    client.patch(
        "/api/settings/provisioning",
        json={"proxmox_pool_prefix": "lab-"},
        headers={"X-CSRF-Token": csrf},
    )
    template = _add_template(client, csrf, name="Box")
    created = _create_member(client, csrf, "pooluser@example.com")
    uid = created["user"]["id"]
    server = _mk_server(client, csrf, uid, template["id"], "s1")
    assert server["status"] == "created"

    # Pool id = prefix + derived user id (pooluser@example.com -> pooluser).
    assert ("create", "lab-pooluser") in fake.pool_calls
    assert ("add", "lab-pooluser") in fake.pool_calls
    assert server["vmid"] and str(server["vmid"]) in fake.pools["lab-pooluser"]
    # Audited.
    events = client.get("/api/audit", params={"category": "user"}).json()
    assert any(
        e["action"] == "server_pool_add" and "status=ok" in e.get("detail", "")
        for e in events
    )


def test_server_create_skips_pool_when_toggle_off(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    fake = _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_add_to_pool": False},
        headers={"X-CSRF-Token": csrf},
    )
    template = _add_template(client, csrf, name="Box")
    created = _create_member(client, csrf, "nopool@example.com")
    uid = created["user"]["id"]
    server = _mk_server(client, csrf, uid, template["id"], "s1")
    assert server["status"] == "created"
    assert fake.pool_calls == []


def test_server_create_pool_failure_never_blocks_create(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    fake = _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf, name="Box")
    created = _create_member(client, csrf, "failpool@example.com")
    uid = created["user"]["id"]

    # Make pool operations fail after the guest is cloned.
    orig = fake.__call__

    def boom(method, url, *, headers, verify, json_body=None):
        if "/pools" in url:
            return (500, {"errors": "pool backend down"})
        return orig(method, url, headers=headers, verify=verify, json_body=json_body)

    monkeypatch.setattr(proxmox, "_http_request", boom)
    server = _mk_server(client, csrf, uid, template["id"], "s1")
    # Server still created despite the pool failure.
    assert server["status"] == "created"
    events = client.get("/api/audit", params={"category": "user"}).json()
    assert any(
        e["action"] == "server_pool_add" and "status=failed" in e.get("detail", "")
        for e in events
    )


def test_add_guest_to_pool_tolerates_existing_pool(monkeypatch) -> None:
    """issue_025: a POST /pools failure is tolerated when the pool actually
    exists (verified by re-checking), rather than string-matching the error."""
    calls: list[tuple[str, str]] = []
    state = {"exists": False}

    def fake_http(method, url, *, headers, verify, json_body=None):
        calls.append((method, url))
        if url.rstrip("/").endswith("/pools") and method == "GET":
            data = [{"poolid": "team-x"}] if state["exists"] else []
            return (200, {"data": data})
        if url.rstrip("/").endswith("/pools") and method == "POST":
            # Simulate a racing/creating duplicate: POST fails, but now it exists.
            state["exists"] = True
            return (500, {"errors": "pool 'team-x' already in use"})
        if "/pools/" in url and method == "PUT":
            return (200, {"data": None})
        raise AssertionError(url)

    monkeypatch.setattr(proxmox, "_http_request", fake_http)
    result = proxmox.ProxmoxResult()
    ok = proxmox.add_guest_to_pool(
        {"proxmox_url": "https://pve:8006", "proxmox_token_name": "t",
         "proxmox_api_key": "k", "proxmox_verify_tls": False},
        "team-x", 105, create_if_missing=True, result=result,
    )
    assert ok is True
    # The PUT to add the guest still ran after the tolerated POST.
    assert any(m == "PUT" and "/pools/team-x" in u for m, u in calls)


def test_add_guest_to_pool_rejects_invalid_poolid(monkeypatch) -> None:
    result = proxmox.ProxmoxResult()
    ok = proxmox.add_guest_to_pool(
        {"proxmox_url": "https://pve:8006", "proxmox_token_name": "t",
         "proxmox_api_key": "k", "proxmox_verify_tls": False},
        "bad pool!", 1, result=result,
    )
    assert ok is False
    assert "invalid pool id" in result.transcript.lower()
