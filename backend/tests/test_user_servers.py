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
                 clone_ok=True, start_ok=True, ip="10.0.7.42",
                 extra_iface_ips=None, destroy_ok=True, live_vmids=None,
                 template_entry=None, reboot_ok=True):
        self.calls: list[str] = []
        self.template_resources = template_resources or {
            "cores": 2, "memory": 4096, "rootfs": "local:9001/x,size=20G",
        }
        # Cluster-resources entry describing the clone source (issue_021: a
        # VM test overrides this with a "qemu" entry so the cloned server
        # ends up kind="vm").
        self.template_entry = template_entry or _TEMPLATE_ENTRY
        self.clone_ok = clone_ok
        self.start_ok = start_ok
        self.reboot_ok = reboot_ok
        self.ip = ip
        # Additional IPv4s the hypervisor reports on the guest's interfaces
        # (beyond the primary ``ip``). Used to exercise F2 corroboration: an
        # in-guest report is only adopted if it appears in the hypervisor view.
        self.extra_iface_ips = list(extra_iface_ips or [])
        # Deferred-deletion (F1) destroy controls. When ``live_vmids`` is set,
        # those vmids appear in /cluster/resources so stop+destroy actually run
        # (otherwise a guest is treated as already-gone). ``destroy_ok`` decides
        # whether the destroy task succeeds.
        self.destroy_ok = destroy_ok
        self.live_vmids = set(live_vmids or [])
        monkeypatch.setattr(proxmox, "_http_request", self)
        monkeypatch.setattr(proxmox, "_sleep", lambda s: None)

    def __call__(self, method, url, *, headers, verify, json_body=None):
        self.calls.append(f"{method} {url}")
        if "/version" in url:
            return _VERSION_OK
        if "/cluster/resources" in url:
            entries = [self.template_entry]
            for vmid in sorted(self.live_vmids):
                entries.append({
                    "vmid": vmid, "name": f"guest-{vmid}", "type": "lxc",
                    "template": 0, "node": "pve1",
                })
            return _resources_payload(entries)
        if "/cluster/nextid" in url:
            return (200, {"data": "120"})
        if "/clone" in url:
            return (200, {"data": "UPID:pve1:0001:clone:"})
        if "/status/current" in url:
            return (200, {"data": {"status": "running"}})
        if "/status/start" in url:
            return (200, {"data": "UPID:pve1:0002:start:"})
        if "/status/stop" in url:
            return (200, {"data": "UPID:pve1:0004:stop:"})
        if "/status/reboot" in url:
            return (200, {"data": "UPID:pve1:0006:reboot:"})
        if method == "DELETE" and ("/lxc/" in url or "/qemu/" in url):
            return (200, {"data": "UPID:pve1:0005:destroy:"})
        if "/tasks/" in url:
            if "clone" in url and not self.clone_ok:
                return (200, {"data": {"status": "stopped",
                                       "exitstatus": "clone error"}})
            if "start" in url and not self.start_ok:
                return (200, {"data": {"status": "stopped",
                                       "exitstatus": "start error"}})
            if "destroy" in url and not self.destroy_ok:
                return (200, {"data": {"status": "stopped",
                                       "exitstatus": "destroy error"}})
            if "reboot" in url and not self.reboot_ok:
                return (200, {"data": {"status": "stopped",
                                       "exitstatus": "reboot error"}})
            return (200, {"data": {"status": "stopped", "exitstatus": "OK"}})
        if "/interfaces" in url:
            if not self.ip:
                return (200, {"data": []})
            ifaces = [
                {"name": "lo", "inet": "127.0.0.1/8"},
                {"name": "eth0", "inet": f"{self.ip}/24"},
            ]
            for i, extra in enumerate(self.extra_iface_ips, start=1):
                ifaces.append({"name": f"eth{i}", "inet": f"{extra}/24"})
            return (200, {"data": ifaces})
        if "/rrddata" in url:
            # Two synthetic samples for the stats endpoint (F2).
            return (200, {"data": [
                {"time": 1000, "cpu": 0.25, "mem": 1024, "maxmem": 4096,
                 "disk": 500, "maxdisk": 2000, "netin": 10, "netout": 20},
                {"time": 1060, "cpu": 0.5, "mem": 2048, "maxmem": 4096,
                 "disk": 600, "maxdisk": 2000, "netin": 30, "netout": 40},
            ]})
        if "/config" in url and (json_body is None):
            return (200, {"data": self.template_resources})
        if "/config" in url or "/resize" in url:
            return (200, {"data": "UPID:pve1:0003:resize:"})
        raise AssertionError(f"unexpected URL: {url}")


class _FakeSsh:
    def __init__(self, monkeypatch, *, rc=0, guest_ip=""):
        self.commands: list[list[str]] = []
        self.rc = rc
        # When set, the in-guest IP read (issue_015-r4 F2) resolves to this
        # address; otherwise it returns nothing and the caller keeps the
        # hypervisor-reported IP.
        self.guest_ip = guest_ip
        monkeypatch.setattr(servers, "_run", self)

    def __call__(self, argv, *, timeout=20):
        self.commands.append(argv)
        remote = argv[-1] if argv else ""
        if self.guest_ip and "ip -4 -o addr show scope global" in remote:
            stdout = (
                "---ip---\n"
                f"2: eth0    inet {self.guest_ip}/24 brd x scope global eth0\n"
                "---hostname---\n"
                f"{self.guest_ip} \n"
            )
            return subprocess.CompletedProcess(
                argv, returncode=0, stdout=stdout, stderr=""
            )
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
    # 63 chars is the maximum; 64 is rejected.
    assert servers.validate_server_name("a" * 63) == "a" * 63
    for bad in ("", "-lead", "a" * 64, "semi;colon"):
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

    # First SSH call reads the in-guest IP (F2); it returned nothing here
    # (default fake), so the hypervisor-reported 10.0.7.42 is kept.
    ip_reads = [c for c in ssh.commands if "ip -4 -o addr" in c[-1]]
    assert len(ip_reads) == 1
    assert ip_reads[0][-2] == "root@10.0.7.42"
    # Key installed for both requested OS users via ssh -i <admin key>.
    installs = [c for c in ssh.commands if "authorized_keys" in c[-1]]
    assert len(installs) == 2
    for argv in installs:
        assert argv[0] == "ssh"
        assert argv[argv.index("-i") + 1] == "/home/svc/.ssh/id_ed25519"
        assert argv[-2] == "root@10.0.7.42"
        assert "ssh-ed25519" in argv[-1]
        # These OS users came from free-form pubkey_users text, not a
        # template-configured main_os_user, so the bash-default/auto-create
        # behavior must NOT apply (it would let a caller auto-create arbitrary
        # accounts); the account must already exist.
        assert "useradd -m -s /bin/bash" not in argv[-1]
        assert "usermod -s /bin/bash" not in argv[-1]
        assert '"no such user"; exit 1' in argv[-1]

    listed = client.get(f"/api/users/{user_id}/servers")
    # The stored name carries the slugified static prefix "<template>-<owner-id>-";
    # the request's "coder box" is only the suffix.
    assert [s["name"] for s in listed.json()] == ["debian-coder-srvuser-coder box"]


def test_lxc_records_in_guest_ip_over_hypervisor_ip(admin, monkeypatch) -> None:
    """issue_015-r4 F2: the address the guest actually holds is recorded,
    not the hypervisor's primary reported address, when they differ AND the
    hypervisor corroborates the guest-reported address."""
    client, csrf, _ = admin
    # Proxmox's primary interface is 10.0.7.42, but it ALSO attributes
    # 10.10.50.77 to the guest; the guest reports 10.10.50.77 as its address.
    _FakeProxmox(monkeypatch, ip="10.0.7.42",
                 extra_iface_ips=["10.10.50.77"])
    ssh = _FakeSsh(monkeypatch, guest_ip="10.10.50.77")
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]

    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "coder box",
              "install_pubkey": True, "pubkey_users": "srvuser"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    server = resp.json()
    # The corroborated in-guest address wins and is what gets recorded.
    assert server["ip_address"] == "10.10.50.77"
    assert "corroborated by the hypervisor; recording 10.10.50.77" \
        in server["last_log"]
    # The IP read reached the guest at the hypervisor primary address...
    ip_reads = [c for c in ssh.commands if "ip -4 -o addr" in c[-1]]
    assert len(ip_reads) == 1
    assert ip_reads[0][-2] == "root@10.0.7.42"
    # ...and the key install then used the confirmed in-guest address.
    installs = [c for c in ssh.commands if "authorized_keys" in c[-1]]
    assert installs and all(c[-2] == "root@10.10.50.77" for c in installs)


def test_lxc_ignores_uncorroborated_in_guest_ip(admin, monkeypatch) -> None:
    """F2 security: a guest-reported address the hypervisor does NOT attribute
    to the guest is ignored (a compromised guest cannot steer the record)."""
    client, csrf, _ = admin
    # Hypervisor only knows 10.0.7.42; the guest lies and claims 10.99.99.9.
    _FakeProxmox(monkeypatch, ip="10.0.7.42")
    ssh = _FakeSsh(monkeypatch, guest_ip="10.99.99.9")
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]

    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "coder box",
              "install_pubkey": True, "pubkey_users": "srvuser"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    server = resp.json()
    # Uncorroborated report ignored -> hypervisor address kept.
    assert server["ip_address"] == "10.0.7.42"
    assert "not corroborated by the hypervisor" in server["last_log"]
    # The install must NOT have been redirected to the guest-claimed address.
    installs = [c for c in ssh.commands if "authorized_keys" in c[-1]]
    assert installs and all(c[-2] == "root@10.0.7.42" for c in installs)
    assert not any("10.99.99.9" in c[-2] for c in ssh.commands)


def test_lxc_falls_back_to_hypervisor_ip_when_guest_unreachable(
    admin, monkeypatch
) -> None:
    """F2 fallback: if the in-guest read yields nothing (no usable output),
    the hypervisor-reported IP is kept -- no regression."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, ip="10.0.7.42")
    # guest_ip unset -> IP read returns empty -> fallback.
    ssh = _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]

    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "coder box",
              "install_pubkey": True, "pubkey_users": "srvuser"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    server = resp.json()
    assert server["ip_address"] == "10.0.7.42"
    assert "keeping hypervisor-reported address" in server["last_log"]
    assert ssh.commands, "expected an in-guest IP read attempt"


# ---------------------------------------------------------------------------
# In-guest IP parsing/read (issue_015-r4 F2) - unit level
# ---------------------------------------------------------------------------


def test_pick_guest_ip_prefers_ip_addr_output() -> None:
    ip_out = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0    inet 10.10.50.12/24 brd 10.10.50.255 scope global eth0\n"
    )
    # A different usable address in hostname -I must NOT win: the `ip` section
    # takes precedence.
    assert servers._pick_guest_ip(ip_out, "10.20.30.40") == "10.10.50.12"


def test_pick_guest_ip_falls_back_to_hostname_and_skips_special() -> None:
    # No usable line in `ip` output -> use hostname -I, skipping IPv6,
    # loopback, and link-local.
    assert servers._pick_guest_ip("", "fe80::1 169.254.9.9 127.0.0.1 192.168.5.6") \
        == "192.168.5.6"


def test_pick_guest_ip_returns_empty_when_nothing_usable() -> None:
    assert servers._pick_guest_ip("", "::1 fe80::abcd") == ""


def test_is_usable_ipv4_rejects_special_and_malformed() -> None:
    assert servers._is_usable_ipv4("10.0.0.1") is True
    assert servers._is_usable_ipv4("127.0.0.1") is False       # loopback
    assert servers._is_usable_ipv4("169.254.1.1") is False     # link-local
    assert servers._is_usable_ipv4("999.1.1.1") is False       # out of range
    assert servers._is_usable_ipv4("not-an-ip") is False


def test_read_ip_from_guest_no_key_returns_empty(monkeypatch) -> None:
    from app.proxmox import ProxmoxResult
    ran = []
    monkeypatch.setattr(servers, "_run", lambda *a, **k: ran.append(a))
    r = ProxmoxResult()
    assert servers.read_ip_from_guest(
        ip="10.0.7.42", admin_key_path="", corroborating_ips=set(), result=r
    ) == ""
    assert ran == [], "must not SSH without an admin key"


def test_read_ip_from_guest_bad_reach_ip_returns_empty(monkeypatch) -> None:
    from app.proxmox import ProxmoxResult
    ran = []
    monkeypatch.setattr(servers, "_run", lambda *a, **k: ran.append(a))
    r = ProxmoxResult()
    assert servers.read_ip_from_guest(
        ip="not-an-ip", admin_key_path="/keys/admin",
        corroborating_ips=set(), result=r
    ) == ""
    assert ran == [], "must not SSH to an invalid reach address"


def test_read_ip_from_guest_empty_output_returns_empty(monkeypatch) -> None:
    """SSH succeeds but the guest yields no usable address -> keep hypervisor IP."""
    from app.proxmox import ProxmoxResult
    monkeypatch.setattr(
        servers, "_run",
        lambda argv, *, timeout=20: subprocess.CompletedProcess(
            argv, returncode=0, stdout="---ip---\n---hostname---\n", stderr=""
        ),
    )
    r = ProxmoxResult()
    assert servers.read_ip_from_guest(
        ip="10.0.7.42", admin_key_path="/keys/admin",
        corroborating_ips={"10.0.7.42"}, result=r
    ) == ""
    assert "no usable address" in r.transcript


def test_read_ip_from_guest_adopts_corroborated_report(monkeypatch) -> None:
    """A differing in-guest IP is adopted when the hypervisor corroborates it.

    The read is the ONLY SSH call -- no reachability probe (corroboration is
    checked against the hypervisor's interface list, not by connecting).
    """
    from app.proxmox import ProxmoxResult
    calls = []

    def fake_run(argv, *, timeout=20):
        calls.append(argv[-1])
        assert argv[-2] == "root@10.0.7.42"
        assert "ip -4 -o addr show scope global" in argv[-1]
        return subprocess.CompletedProcess(
            argv, returncode=0,
            stdout=("---ip---\n2: eth0    inet 10.10.50.5/24 scope global eth0\n"
                    "---hostname---\n10.10.50.5 \n"),
            stderr="",
        )

    monkeypatch.setattr(servers, "_run", fake_run)
    r = ProxmoxResult()
    assert servers.read_ip_from_guest(
        ip="10.0.7.42", admin_key_path="/keys/admin",
        corroborating_ips={"10.0.7.42", "10.10.50.5"}, result=r
    ) == "10.10.50.5"
    assert len(calls) == 1, "only the read SSH call; no probe"
    assert "corroborated by the hypervisor; recording 10.10.50.5" in r.transcript


def test_read_ip_from_guest_ignores_uncorroborated_report(monkeypatch) -> None:
    """F2 security: a guest-reported IP absent from the hypervisor view is
    NOT adopted -- the hypervisor address is kept, and no probe is made."""
    from app.proxmox import ProxmoxResult
    calls = []

    def fake_run(argv, *, timeout=20):
        calls.append(argv[-1])
        return subprocess.CompletedProcess(
            argv, returncode=0,
            stdout=("---ip---\n2: eth1    inet 10.99.99.9/24 scope global eth1\n"
                    "---hostname---\n10.99.99.9 \n"),
            stderr="",
        )

    monkeypatch.setattr(servers, "_run", fake_run)
    r = ProxmoxResult()
    assert servers.read_ip_from_guest(
        ip="10.0.7.42", admin_key_path="/keys/admin",
        corroborating_ips={"10.0.7.42"}, result=r
    ) == ""
    assert len(calls) == 1, "no extra SSH probe to the uncorroborated address"
    assert "not corroborated by the hypervisor" in r.transcript


def test_read_ip_from_guest_matching_ip_needs_no_corroboration(monkeypatch) -> None:
    """When the guest confirms the hypervisor's own primary address, it is
    accepted directly (a single read call, no probe)."""
    from app.proxmox import ProxmoxResult
    calls = []

    def fake_run(argv, *, timeout=20):
        calls.append(argv[-1])
        return subprocess.CompletedProcess(
            argv, returncode=0,
            stdout=("---ip---\n2: eth0    inet 10.0.7.42/24 scope global eth0\n"
                    "---hostname---\n10.0.7.42 \n"),
            stderr="",
        )

    monkeypatch.setattr(servers, "_run", fake_run)
    r = ProxmoxResult()
    assert servers.read_ip_from_guest(
        ip="10.0.7.42", admin_key_path="/keys/admin",
        corroborating_ips={"10.0.7.42"}, result=r
    ) == "10.0.7.42"
    assert len(calls) == 1  # exactly one SSH call (the read); no probe
    assert "Confirmed in-guest IP 10.0.7.42" in r.transcript


def test_read_ip_from_guest_ssh_failure_returns_empty(monkeypatch) -> None:
    from app.proxmox import ProxmoxResult
    monkeypatch.setattr(
        servers, "_run",
        lambda argv, *, timeout=20: subprocess.CompletedProcess(
            argv, returncode=255, stdout="", stderr="conn refused"
        ),
    )
    r = ProxmoxResult()
    assert servers.read_ip_from_guest(
        ip="10.0.7.42", admin_key_path="/keys/admin",
        corroborating_ips={"10.0.7.42"}, result=r
    ) == ""
    assert "keeping hypervisor-reported address" in r.transcript


def test_list_lxc_ips_collects_validated_non_loopback(monkeypatch) -> None:
    from app.proxmox import ProxmoxResult

    def fake_request(method, url, *, headers, verify, json_body=None):
        assert "/interfaces" in url
        return (200, {"data": [
            {"name": "lo", "inet": "127.0.0.1/8"},
            {"name": "eth0", "inet": "10.0.7.42/24"},
            {"name": "eth1", "inet": "10.10.50.5/24"},
            {"name": "bad", "inet": "999.1.1.1/24"},   # rejected (out of range)
            {"name": "noip"},                            # rejected (no inet)
        ]})

    monkeypatch.setattr(proxmox, "_http_request", fake_request)
    r = ProxmoxResult()
    got = proxmox.list_lxc_ips({"proxmox_url": "https://pve:8006",
                                "proxmox_api_key": "k",
                                "proxmox_token_name": "t@pam!x",
                                "proxmox_verify_tls": False},
                               "pve1", 120, result=r)
    assert got == {"10.0.7.42", "10.10.50.5"}


def test_list_lxc_ips_returns_empty_on_read_failure(monkeypatch) -> None:
    from app.proxmox import ProxmoxResult

    def boom(method, url, *, headers, verify, json_body=None):
        return (500, {"errors": "nope"})

    monkeypatch.setattr(proxmox, "_http_request", boom)
    r = ProxmoxResult()
    got = proxmox.list_lxc_ips({"proxmox_url": "https://pve:8006",
                                "proxmox_api_key": "k",
                                "proxmox_token_name": "t@pam!x",
                                "proxmox_verify_tls": False},
                               "pve1", 120, result=r)
    assert got == set()


def _proxmox_cfg() -> dict:
    return {"proxmox_url": "https://pve:8006", "proxmox_api_key": "k",
            "proxmox_token_name": "t@pam!x", "proxmox_verify_tls": False}


def test_destroy_guest_absent_is_success(monkeypatch) -> None:
    """Destroying an already-absent guest is a no-op success (idempotent)."""
    from app.proxmox import ProxmoxResult
    calls = []

    def fake(method, url, *, headers, verify, json_body=None):
        calls.append(f"{method} {url}")
        if "/cluster/resources" in url:
            return (200, {"data": []})  # guest 120 not present
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(proxmox, "_http_request", fake)
    monkeypatch.setattr(proxmox, "_sleep", lambda s: None)
    r = ProxmoxResult()
    assert proxmox.destroy_guest(_proxmox_cfg(), "pve1", 120, "lxc", result=r)
    # No DELETE was issued for an already-gone guest.
    assert not any(c.startswith("DELETE ") for c in calls)


def test_stop_guest_absent_is_success(monkeypatch) -> None:
    from app.proxmox import ProxmoxResult

    def fake(method, url, *, headers, verify, json_body=None):
        if "/cluster/resources" in url:
            return (200, {"data": []})
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(proxmox, "_http_request", fake)
    monkeypatch.setattr(proxmox, "_sleep", lambda s: None)
    r = ProxmoxResult()
    assert proxmox.stop_guest(_proxmox_cfg(), "pve1", 120, "lxc", result=r)


def test_stop_guest_already_stopped_skips_stop(monkeypatch) -> None:
    from app.proxmox import ProxmoxResult
    calls = []

    def fake(method, url, *, headers, verify, json_body=None):
        calls.append(f"{method} {url}")
        if "/cluster/resources" in url:
            return (200, {"data": [{"vmid": 120, "type": "lxc",
                                    "node": "pve1", "name": "g"}]})
        if "/status/current" in url:
            return (200, {"data": {"status": "stopped"}})
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(proxmox, "_http_request", fake)
    monkeypatch.setattr(proxmox, "_sleep", lambda s: None)
    r = ProxmoxResult()
    assert proxmox.stop_guest(_proxmox_cfg(), "pve1", 120, "lxc", result=r)
    assert not any("/status/stop" in c for c in calls)


def test_destroy_server_no_vmid_is_success() -> None:
    from app import servers as s
    out = s.destroy_server(
        provider_config=_proxmox_cfg(), node="", vmid=None, kind="lxc"
    )
    assert out["status"] == "ok"


def test_destroy_guest_uses_purge_only_and_quotes_node(monkeypatch) -> None:
    """destroy_guest must NOT reap unreferenced disks and must quote the node."""
    from app.proxmox import ProxmoxResult
    seen = []

    def fake(method, url, *, headers, verify, json_body=None):
        seen.append(f"{method} {url}")
        if "/cluster/resources" in url:
            return (200, {"data": [{"vmid": 120, "type": "lxc",
                                    "node": "pve/odd", "name": "g"}]})
        if method == "DELETE":
            return (200, {"data": "UPID:x:1:d:"})
        if "/tasks/" in url:
            return (200, {"data": {"status": "stopped", "exitstatus": "OK"}})
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(proxmox, "_http_request", fake)
    monkeypatch.setattr(proxmox, "_sleep", lambda s: None)
    r = ProxmoxResult()
    assert proxmox.destroy_guest(_proxmox_cfg(), "pve/odd", 120, "lxc", result=r)
    delete = [c for c in seen if c.startswith("DELETE ")][0]
    assert "purge=1" in delete
    assert "destroy-unreferenced-disks" not in delete
    # The node with a '/' is percent-encoded, not left to split the path.
    assert "pve%2Fodd" in delete
    assert "/nodes/pve/odd/" not in delete


def test_deferred_deletion_sweep_is_capped(admin, monkeypatch) -> None:
    """At most _SWEEP_MAX_PER_CALL guests are destroyed per sweep call; a
    backlog drains across successive list requests."""
    from app.routers import provisioning
    client, csrf, _ = admin
    live = {201, 202, 203, 204, 205}
    _FakeProxmox(monkeypatch, live_vmids=live)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]

    # Insert 5 servers with vmids, all scheduled and past grace.
    from app.db import get_connection
    from app import repository
    for vmid in sorted(live):
        with get_connection() as conn:
            srv = repository.create_user_server(
                conn, user_id=user_id, name=f"s{vmid}", kind="lxc",
                status="created", vmid=vmid, node="pve1",
            )
        client.delete(
            f"/api/users/{user_id}/servers/{srv['id']}",
            headers={"X-CSRF-Token": csrf},
        )
        _backdate_deletion(user_id, srv["id"], hours=25)

    cap = provisioning._SWEEP_MAX_PER_CALL
    total = len(live)
    # The first list request sweeps at most `cap`; the rest remain pending.
    remaining = client.get(f"/api/users/{user_id}/servers").json()
    assert len(remaining) == total - cap
    assert all(s["deletion_pending"] for s in remaining)
    # The backlog drains over successive list requests (each sweeps ≤ cap).
    guard = 0
    while client.get(f"/api/users/{user_id}/servers").json():
        guard += 1
        assert guard <= total + 2, "sweep failed to drain the backlog"
    # More than one pass was required (proving the per-call cap took effect).
    assert total > cap and guard >= 1


def test_malformed_deletion_timestamp_flagged_for_admin(admin, monkeypatch) -> None:
    """A corrupt deletion timestamp is surfaced as an admin error, never left
    silently pending forever."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, live_vmids={120})
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    # Corrupt the timestamp directly.
    from app.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_servers SET deletion_requested_at = 'not-a-date' "
            "WHERE id = ?",
            (server["id"],),
        )
    admin_view = client.get(f"/api/users/{user_id}/servers").json()
    assert len(admin_view) == 1
    assert admin_view[0]["deletion_failed"] is True
    assert "unreadable" in admin_view[0]["deletion_error"].lower()


def test_sweep_provider_unconfigured_records_error(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, live_vmids={120})
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    _backdate_deletion(user_id, server["id"], hours=25)
    # Unconfigure the provider so the sweep cannot destroy.
    client.patch(
        "/api/settings/provisioning",
        json={"proxmox_url": "", "proxmox_token_name": "", "proxmox_api_key": ""},
        headers={"X-CSRF-Token": csrf},
    )
    admin_view = client.get(f"/api/users/{user_id}/servers").json()
    assert len(admin_view) == 1
    assert admin_view[0]["deletion_failed"] is True
    assert "not configured" in admin_view[0]["deletion_error"].lower()


def test_cancel_after_grace_before_sweep(admin, monkeypatch) -> None:
    """Cancelling wins if it beats the sweep, even once past the grace window."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, live_vmids={120})
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    _backdate_deletion(user_id, server["id"], hours=25)
    # Cancel directly (no list call in between, so no sweep has run).
    cancel = client.post(
        f"/api/users/{user_id}/servers/{server['id']}/cancel-deletion",
        headers={"X-CSRF-Token": csrf},
    )
    assert cancel.status_code == 200
    assert cancel.json()["deletion_pending"] is False


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
# Usage endpoint (issue_015-r4 F3) - quota bars data source
# ---------------------------------------------------------------------------


def test_server_usage_reports_committed_usage_and_limits(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)  # template default: 2 cores, 4 GB, 20 GB disk
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True,
              "provisioning_max_servers": 5, "provisioning_max_cpus": 10,
              "provisioning_max_memory_gb": 32, "provisioning_max_disk_gb": 100},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )

    # Before any server: zero usage, limits reflected.
    usage = member.get(f"/api/users/{user_id}/servers/usage")
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["unlimited"] is False
    assert body["servers"] == {"used": 0, "limit": 5}
    assert body["cpus"] == {"used": 0, "limit": 10}
    assert body["memory_gb"] == {"used": 0, "limit": 32}
    assert body["disk_gb"] == {"used": 0, "limit": 100}

    # After creating one server, committed usage reflects the template footprint.
    assert member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "one"},
        headers={"X-CSRF-Token": member_csrf},
    ).status_code == 201
    body = member.get(f"/api/users/{user_id}/servers/usage").json()
    assert body["servers"] == {"used": 1, "limit": 5}
    assert body["cpus"] == {"used": 2, "limit": 10}
    assert body["memory_gb"] == {"used": 4, "limit": 32}
    assert body["disk_gb"] == {"used": 20, "limit": 100}


def test_server_usage_unlimited_for_admin(admin) -> None:
    client, csrf, _ = admin
    # The admin views their own usage (admin user id is 1).
    me = client.get("/api/session").json()["user"]
    usage = client.get(f"/api/users/{me['id']}/servers/usage")
    assert usage.status_code == 200, usage.text
    body = usage.json()
    assert body["unlimited"] is True
    # Limits are reported as 0 (no cap) with committed usage still present.
    assert body["servers"]["limit"] == 0
    assert body["cpus"]["limit"] == 0


def test_server_usage_admin_created_servers_counted(admin, monkeypatch) -> None:
    """issue_018: all non-failed servers (including admin-created ones) count in
    usage so the quota bars reflect real committed resources."""
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
    # Admin creates a server FOR the member (admin_modified).
    assert client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "admin-made"},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 201

    member, _ = _login(client.app, "srvuser@example.com", created["password"])
    body = member.get(f"/api/users/{user_id}/servers/usage").json()
    # The server counts toward both the count AND the resource sums now.
    assert body["servers"]["used"] == 1
    assert body["cpus"]["used"] == 2
    assert body["memory_gb"]["used"] == 4
    assert body["disk_gb"]["used"] == 20


def test_server_usage_authorization(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    other = _create_member(client, csrf, username="other@example.com")
    member, _ = _login(client.app, "srvuser@example.com", created["password"])
    # A user cannot read another user's usage.
    assert member.get(
        f"/api/users/{other['user']['id']}/servers/usage"
    ).status_code == 403
    # Admin can read any user's usage.
    assert client.get(f"/api/users/{user_id}/servers/usage").status_code == 200


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


def test_self_service_resource_change_and_quota(admin, monkeypatch) -> None:
    """issue_021: any self-service user may change their own server's
    resources -- the admin-toggle gate from issue_017 is gone -- but the
    per-user quota is still enforced."""
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

    # No admin toggle needed: self-service ownership alone is sufficient.
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


def test_non_self_service_user_cannot_change_resources(
    admin, monkeypatch
) -> None:
    """A non-self-service owner still cannot edit their own server's resources."""
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
    resp = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 4},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 403


def test_account_server_access_allow_resource_edit_by_role(
    admin, monkeypatch
) -> None:
    """issue_021: allow_resource_edit no longer depends on the admin toggle."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)

    self_service_user = _create_member(
        client, csrf, "self-service@example.com", self_service=True
    )
    non_self_service_user = _create_member(
        client, csrf, "plain@example.com", self_service=False
    )

    self_service_member, _ = _login(
        client.app, "self-service@example.com", self_service_user["password"]
    )
    plain_member, _ = _login(
        client.app, "plain@example.com", non_self_service_user["password"]
    )

    # provisioning_allow_resource_edit is left at its (off) default.
    assert self_service_member.get(
        "/api/account/server-access"
    ).json()["allow_resource_edit"] is True
    assert plain_member.get(
        "/api/account/server-access"
    ).json()["allow_resource_edit"] is False
    assert client.get(
        "/api/account/server-access"
    ).json()["allow_resource_edit"] is True


_VM_TEMPLATE_ENTRY = {
    "vmid": 9100, "name": "tpl-vm", "type": "qemu", "template": 1,
    "node": "pve2",
}


def _create_vm_server(client, csrf, monkeypatch, *, self_service=True):
    """Provision a self-service member's VM server via a fake VM template.

    Returns ``(user_id, server, member_client, member_csrf, fake_proxmox)``.
    """
    fake = _FakeProxmox(
        monkeypatch,
        template_entry=_VM_TEMPLATE_ENTRY,
        template_resources={"cores": 2, "memory": 4096},
    )
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9100, "name": "Win VM", "kind": "vm"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(
        client, csrf, "vmowner@example.com", self_service=self_service
    )
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "vmowner@example.com", created["password"]
    )
    # A non-self-service member cannot create their own server, so the admin
    # provisions it on their behalf (mirrors an admin-managed account).
    creator, creator_csrf = (member, member_csrf) if self_service else (client, csrf)
    server = creator.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "vmbox"},
        headers={"X-CSRF-Token": creator_csrf},
    ).json()
    assert server["kind"] == "vm"
    return user_id, server, member, member_csrf, fake


def test_vm_resource_change_cpu_memory_sets_reboot_required(
    admin, monkeypatch
) -> None:
    """issue_021: VMs support CPU/memory changes; a reboot is then advised."""
    client, csrf, _ = admin
    user_id, server, member, member_csrf, _fake = _create_vm_server(
        client, csrf, monkeypatch
    )

    resp = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 4, "memory_gb": 8},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cpus"] == 4
    assert body["memory_gb"] == 8
    assert body["reboot_required"] is True

    # A plain re-fetch never reports reboot_required (transient, not stored).
    listed = client.get(f"/api/users/{user_id}/servers").json()
    assert listed[0]["reboot_required"] is False


def test_vm_disk_change_rejected(admin, monkeypatch) -> None:
    """issue_021: VM disk resize is out of scope for self-service edits."""
    client, csrf, _ = admin
    user_id, server, member, member_csrf, _fake = _create_vm_server(
        client, csrf, monkeypatch
    )

    resp = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"disk_gb": 40},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 400
    assert "disk" in resp.json()["detail"].lower()


def test_reboot_owner_self_service_succeeds_and_is_audited(
    admin, monkeypatch
) -> None:
    client, csrf, _ = admin
    user_id, server, member, member_csrf, _fake = _create_vm_server(
        client, csrf, monkeypatch
    )

    resp = member.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 200, resp.text

    events = client.get("/api/audit", params={"category": "user"}).json()
    reboots = [e for e in events if e["action"] == "server_reboot"]
    assert len(reboots) == 1
    assert reboots[0]["target_name"] == server["name"]


def test_reboot_admin_succeeds_on_others_server(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    user_id, server, _member, _member_csrf, _fake = _create_vm_server(
        client, csrf, monkeypatch
    )

    resp = client.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text


def test_reboot_lxc_server_succeeds(admin, monkeypatch) -> None:
    """issue_021: reboot works for LXC guests too, not just VMs."""
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
    assert server["kind"] == "lxc"

    resp = member.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 200, resp.text


def test_reboot_self_service_owner_allowed_on_admin_modified_server(
    admin, monkeypatch
) -> None:
    """A reboot is a plain power operation; like resource edits (issue_023) it
    is not blocked by admin_modified for the self-service owner."""
    client, csrf, _ = admin
    user_id, server, member, member_csrf, _fake = _create_vm_server(
        client, csrf, monkeypatch
    )
    admin_marked = client.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 4},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert admin_marked["admin_modified"] is True

    resp = member.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 200, resp.text


def test_self_service_owner_can_resize_admin_modified_server(
    admin, monkeypatch
) -> None:
    """issue_023: admin_modified only marks an admin-sized server; it no longer
    locks the self-service OWNER out of resizing their own server (quota still
    applies)."""
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
    # Admin provisions the server for the member -> admin_modified=True.
    server = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "mine",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert server["admin_modified"] is True

    # The owner may now resize it within quota (was a 403 before issue_023).
    ok = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 3},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["cpus"] == 3

    # Quota is still enforced for the owner.
    too_big = member.patch(
        f"/api/users/{user_id}/servers/{server['id']}",
        json={"cpus": 50},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert too_big.status_code == 400
    assert "limit exceeded" in too_big.json()["detail"]


def test_reboot_proxmox_failure_returns_502(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    user_id, server, member, member_csrf, fake = _create_vm_server(
        client, csrf, monkeypatch
    )
    fake.reboot_ok = False

    resp = member.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 502
    assert "reboot" in resp.json()["detail"].lower()


def test_reboot_rejects_deletion_pending_server(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    user_id, server, member, member_csrf, _fake = _create_vm_server(
        client, csrf, monkeypatch
    )
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )

    resp = member.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 409


def test_reboot_rejects_non_owner(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    user_id, server, _member, _member_csrf, _fake = _create_vm_server(
        client, csrf, monkeypatch
    )
    other = _create_member(client, csrf, "other@example.com", self_service=True)
    other_member, other_csrf = _login(
        client.app, "other@example.com", other["password"]
    )

    resp = other_member.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": other_csrf},
    )
    assert resp.status_code == 403


def test_reboot_rejects_non_self_service_owner(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    user_id, server, member, member_csrf, _fake = _create_vm_server(
        client, csrf, monkeypatch, self_service=False
    )
    resp = member.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 403


def test_reboot_rejects_failed_server(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, clone_ok=False)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "doomed"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert server["status"] == "failed"

    resp = client.post(
        f"/api/users/{user_id}/servers/{server['id']}/reboot",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400


def test_delete_requests_deferred_deletion(admin, monkeypatch) -> None:
    """DELETE schedules a deferred deletion (24h grace); it does not remove."""
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
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deletion_pending"] is True
    assert body["deletion_requested_at"] != ""
    # The server is still listed (pending), not removed.
    listed = client.get(f"/api/users/{user_id}/servers").json()
    assert len(listed) == 1
    assert listed[0]["deletion_pending"] is True


def _backdate_deletion(user_id: int, server_id: int, hours: float) -> None:
    """Move a server's deletion request timestamp into the past."""
    from datetime import datetime, timedelta, timezone
    from app.db import get_connection

    when = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_servers SET deletion_requested_at = ? "
            "WHERE id = ? AND user_id = ?",
            (when, server_id, user_id),
        )


def test_deferred_deletion_destroys_after_grace(admin, monkeypatch) -> None:
    """After 24h the lazy sweep stops+destroys the guest and removes the row."""
    client, csrf, _ = admin
    fake = _FakeProxmox(monkeypatch, live_vmids={120})
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])

    # Schedule, then backdate past the 24h window.
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    _backdate_deletion(user_id, server["id"], hours=25)

    # A list request triggers the sweep: the guest is destroyed and removed.
    listed = client.get(f"/api/users/{user_id}/servers").json()
    assert listed == []
    # stop + destroy were actually issued against the live guest.
    assert any("/status/stop" in c for c in fake.calls)
    assert any(c.startswith("DELETE ") and "/lxc/120" in c for c in fake.calls)


def test_deferred_deletion_not_destroyed_before_grace(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    fake = _FakeProxmox(monkeypatch, live_vmids={120})
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    _backdate_deletion(user_id, server["id"], hours=1)  # still within grace

    listed = client.get(f"/api/users/{user_id}/servers").json()
    assert len(listed) == 1
    assert listed[0]["deletion_pending"] is True
    assert not any("/status/stop" in c for c in fake.calls)


def test_cancel_deletion_before_grace(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, live_vmids={120})
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    cancel = client.post(
        f"/api/users/{user_id}/servers/{server['id']}/cancel-deletion",
        headers={"X-CSRF-Token": csrf},
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["deletion_pending"] is False
    # A cancelled deletion leaves the server active and unscheduled.
    listed = client.get(f"/api/users/{user_id}/servers").json()
    assert len(listed) == 1
    assert listed[0]["deletion_pending"] is False


def test_cancel_deletion_when_not_pending_conflicts(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    resp = client.post(
        f"/api/users/{user_id}/servers/{server['id']}/cancel-deletion",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409


def test_failed_destroy_kept_for_admin_hidden_from_user(admin, monkeypatch) -> None:
    """A destroy failure keeps the row in the admin list (with error) but
    hides it from the owner."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, live_vmids={120}, destroy_ok=False)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    # Configure trusted access off to keep the member creation simple.
    client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf, self_service=True)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    _backdate_deletion(user_id, server["id"], hours=25)

    # Sweep runs on list; destroy fails -> row kept, marked failed.
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    # Owner no longer sees the failed-destroy server.
    owner_view = member.get(f"/api/users/{user_id}/servers").json()
    assert owner_view == []
    # Admin still sees it, flagged failed, with the error detail.
    admin_view = client.get(f"/api/users/{user_id}/servers").json()
    assert len(admin_view) == 1
    assert admin_view[0]["deletion_failed"] is True
    assert admin_view[0]["deletion_error"] != ""


def test_admin_force_remove_after_failed_destroy(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, live_vmids={120}, destroy_ok=False)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    client.delete(
        f"/api/users/{user_id}/servers/{server['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    _backdate_deletion(user_id, server["id"], hours=25)
    client.get(f"/api/users/{user_id}/servers")  # trigger failed sweep

    force = client.post(
        f"/api/users/{user_id}/servers/{server['id']}/force-remove",
        headers={"X-CSRF-Token": csrf},
    )
    assert force.status_code == 200, force.text
    assert "removed" in force.json()["detail"].lower()
    assert client.get(f"/api/users/{user_id}/servers").json() == []


def test_force_remove_requires_admin(admin, monkeypatch) -> None:
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
    server = _create_server_for(client, csrf, user_id, template["id"])
    member, member_csrf = _login(
        client.app, "srvuser@example.com", created["password"]
    )
    resp = member.post(
        f"/api/users/{user_id}/servers/{server['id']}/force-remove",
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 403


def test_server_log_hidden_from_non_admin_owner(admin, monkeypatch) -> None:
    """issue_020: the provisioning log is admin-only, enforced at the API."""
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
    server = _create_server_for(client, csrf, user_id, template["id"])
    assert server  # admin create succeeded

    # Admin sees last_log via the list endpoint.
    admin_view = client.get(f"/api/users/{user_id}/servers").json()
    assert any(s.get("last_log") for s in admin_view)

    # The owning non-admin does NOT see last_log.
    member, _ = _login(client.app, "srvuser@example.com", created["password"])
    owner_view = member.get(f"/api/users/{user_id}/servers").json()
    assert owner_view
    assert all(s["last_log"] == "" for s in owner_view)


def test_deferred_deletion_no_vmid_row_honors_grace(admin, monkeypatch) -> None:
    """A record with no vmid (never cloned) still waits the grace window, then
    is removed with no Proxmox destroy call."""
    client, csrf, _ = admin
    fake = _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    # A reference-style record with no vmid, inserted directly.
    from app.db import get_connection
    from app import repository
    with get_connection() as conn:
        ref = repository.create_user_server(
            conn, user_id=user_id, name="ref", kind="lxc", status="reference",
        )
    client.delete(
        f"/api/users/{user_id}/servers/{ref['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    _backdate_deletion(user_id, ref["id"], hours=25)
    fake.calls.clear()
    listed = client.get(f"/api/users/{user_id}/servers").json()
    assert listed == []
    # No stop/destroy was attempted for a record that never had a guest.
    assert not any("/status/stop" in c for c in fake.calls)
    assert not any(c.startswith("DELETE ") for c in fake.calls)


def test_list_backfills_missing_resource_specs(admin, monkeypatch) -> None:
    """issue_017: a guest with a vmid but 0 specs is backfilled from the
    provider on list and persisted; a reference server (no vmid) stays 0."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, template_resources={
        "cores": 4, "memory": 8192, "rootfs": "local:120/x,size=40G",
    })
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]

    from app.db import get_connection
    from app import repository
    with get_connection() as conn:
        # A guest with a vmid+node but unrecorded (0) specs.
        gap = repository.create_user_server(
            conn, user_id=user_id, name="gap", kind="lxc", vmid=120,
            node="pve1", status="created",
        )
        # A reference record with no vmid.
        repository.create_user_server(
            conn, user_id=user_id, name="ref", kind="lxc", status="reference",
        )
    assert gap["cpus"] == 0

    listed = {s["name"]: s for s in
              client.get(f"/api/users/{user_id}/servers").json()}
    # Backfilled from the provider config (cores=4, memory=8GB, disk=40GB).
    assert listed["gap"]["cpus"] == 4
    assert listed["gap"]["memory_gb"] == 8
    assert listed["gap"]["disk_gb"] == 40
    # Reference server (no vmid) is left at 0.
    assert listed["ref"]["cpus"] == 0

    # Persisted: a second list returns the stored values (still populated).
    listed2 = {s["name"]: s for s in
               client.get(f"/api/users/{user_id}/servers").json()}
    assert listed2["gap"]["cpus"] == 4


def test_list_survives_backfill_provider_failure(admin, monkeypatch) -> None:
    """A provider error during resource backfill never fails the list."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    from app.db import get_connection
    from app import repository
    with get_connection() as conn:
        repository.create_user_server(
            conn, user_id=user_id, name="gap", kind="lxc", vmid=120,
            node="pve1", status="created",
        )
    import app.proxmox as proxmox

    def _boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(proxmox, "get_guest_resources", _boom)
    resp = client.get(f"/api/users/{user_id}/servers")
    assert resp.status_code == 200
    assert {s["name"] for s in resp.json()} == {"gap"}


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


# ---------------------------------------------------------------------------
# Server-name composition + global uniqueness (issue_015-r5 F1)
# ---------------------------------------------------------------------------


def test_server_name_composed_with_static_prefix(admin, monkeypatch) -> None:
    """The request 'name' is only a suffix; the stored name is
    '<template>-<owner-derived-id>-<suffix>'."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)  # name "Debian Coder"
    created = _create_member(client, csrf, username="morris@example.com")
    user_id = created["user"]["id"]
    assert created["user"]["user_id"] == "morris"

    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "test-x",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    # The template portion is slugified (lowercase, dashes).
    assert resp.json()["name"] == "debian-coder-morris-test-x"


def test_server_name_prefix_applies_to_self_service_creator(
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
    created = _create_member(
        client, csrf, username="morris@example.com", self_service=True
    )
    user_id = created["user"]["id"]
    member, member_csrf = _login(
        client.app, "morris@example.com", created["password"]
    )
    resp = member.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "mybox",
              "install_pubkey": False},
        headers={"X-CSRF-Token": member_csrf},
    )
    assert resp.status_code == 201, resp.text
    # Self-service creators get the same forced prefix (based on their own id).
    assert resp.json()["name"] == "debian-coder-morris-mybox"


def test_server_name_globally_unique_across_users(admin, monkeypatch) -> None:
    """Server names are globally unique: a name already taken by another user
    (here via two templates that compose to the same string) is rejected."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    # Two templates whose names differ but can compose to the same full name:
    #   "Debian Coder" + owner "morris" + suffix "x"      -> "Debian Coder-morris-x"
    #   "Debian"       + owner "morris" + suffix "coder-x" would differ; instead
    # we rely on the repository-level guarantee plus a same-name cross-user
    # attempt constructed directly.
    template = _add_template(client, csrf)
    u1 = _create_member(client, csrf, username="morris@example.com")
    r1 = client.post(
        f"/api/users/{u1['user']['id']}/servers",
        json={"template_id": template["id"], "name": "shared",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r1.status_code == 201
    full = r1.json()["name"]  # "Debian Coder-morris-shared"

    # A second user; insert a server for THEM with the exact same full name
    # directly, then confirm server_name_exists sees it globally.
    from app.db import get_connection
    from app import repository
    u2 = _create_member(client, csrf, username="nadia@example.com")
    with get_connection() as conn:
        assert repository.server_name_exists(conn, full) is True
        assert repository.server_name_exists(conn, full.upper()) is True
        assert repository.server_name_exists(conn, "no-such-name") is False
        # The DB unique index enforces global uniqueness case-insensitively:
        # inserting the same name for a DIFFERENT user raises (backstops the
        # application pre-check against a cross-user race).
        import pytest as _pytest
        with _pytest.raises(ValueError):
            repository.create_user_server(
                conn, user_id=u2["user"]["id"], name=full.upper(),
                kind="lxc", status="created",
            )


def test_server_name_globally_unique_endpoint_conflict(admin, monkeypatch) -> None:
    """The create endpoint rejects a name already used by ANOTHER user."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    u1 = _create_member(client, csrf, username="morris@example.com")
    r1 = client.post(
        f"/api/users/{u1['user']['id']}/servers",
        json={"template_id": template["id"], "name": "shared",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r1.status_code == 201
    # Rename u1's server directly to a name a second user could also compose.
    from app.db import get_connection
    u2 = _create_member(client, csrf, username="nadia@example.com")
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_servers SET name = ? WHERE id = ?",
            ("debian-coder-nadia-clash", r1.json()["id"]),
        )
    # Now u2 trying to create "clash" composes the same name -> global 409.
    dup = client.post(
        f"/api/users/{u2['user']['id']}/servers",
        json={"template_id": template["id"], "name": "clash",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert dup.status_code == 409
    assert "different name suffix" in dup.json()["detail"]


def test_server_name_suffix_too_long_rejected(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)  # "Debian Coder" (12 chars)
    created = _create_member(client, csrf, username="morris@example.com")
    user_id = created["user"]["id"]
    # prefix "Debian Coder-morris-" = 20 chars -> 43 available. 44 must fail.
    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "a" * 44,
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400
    assert "at most 43 character" in resp.json()["detail"]
    # Exactly 43 fits (full name = 63).
    ok = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "a" * 43,
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 201, ok.text
    assert len(ok.json()["name"]) == 63


def test_server_name_empty_suffix_rejected(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "   ",
              "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 400


def test_server_name_prefix_slugifies_odd_template_name(admin, monkeypatch) -> None:
    """A template whose name has spaces/punctuation still composes a valid
    full server name (the template portion is slugified)."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    # Template name with spaces, punctuation, mixed case.
    tpl = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "Coder!! (Prod)", "kind": "lxc",
              "admin_ssh_key_path": "/keys/admin"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    created = _create_member(client, csrf, username="morris@example.com")
    user_id = created["user"]["id"]
    resp = client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": tpl["id"], "name": "x", "install_pubkey": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    # "Coder!! (Prod)" -> "coder-prod"
    assert resp.json()["name"] == "coder-prod-morris-x"


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
        {
            "id": template["id"],
            "name": "Debian Coder",
            "kind": "lxc",
            "is_apps_server": False,
        }
    ]
    assert "vmid" not in options.text
    assert "id_ed25519" not in options.text


def test_is_apps_server_reflects_current_template_flag(
    admin, monkeypatch
) -> None:
    """issue_021: UserServerOut.is_apps_server is derived live from the
    server's template, and goes False if the template is later deleted (it
    can never report a stale True)."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    apps_template = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "Apps Template", "kind": "lxc",
              "is_apps_server": True},
        headers={"X-CSRF-Token": csrf},
    ).json()
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, apps_template["id"])
    assert server["is_apps_server"] is True

    listed = client.get(f"/api/users/{user_id}/servers").json()
    assert listed[0]["is_apps_server"] is True

    # Deleting the template sets template_id to NULL (ON DELETE SET NULL);
    # the server is no longer treated as an apps-server.
    resp = client.delete(
        f"/api/settings/server-templates/{apps_template['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    listed = client.get(f"/api/users/{user_id}/servers").json()
    assert listed[0]["is_apps_server"] is False
    assert listed[0]["template_id"] is None


def test_is_apps_server_false_for_non_apps_template(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)  # is_apps_server defaults off
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    assert server["is_apps_server"] is False


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
    # issue_020: the provisioning log is hidden from non-admin owners.
    assert body["last_log"] == ""

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


# ---------------------------------------------------------------------------
# Server overview + stats (issue_015-r5 F2)
# ---------------------------------------------------------------------------


def test_servers_overview_admin_sees_all_grouped_by_owner(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    u1 = _create_member(client, csrf, username="morris@example.com")
    u2 = _create_member(client, csrf, username="nadia@example.com")
    _create_server_for(client, csrf, u1["user"]["id"], template["id"], name="a")
    _create_server_for(client, csrf, u2["user"]["id"], template["id"], name="b")

    resp = client.get("/api/servers/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_admin"] is True
    owners = {o["username"]: o for o in body["owners"]}
    # Both the admin (no servers) and the two members appear.
    assert "morris@example.com" in owners and "nadia@example.com" in owners
    assert owners["morris@example.com"]["derived_user_id"] == "morris"
    assert len(owners["morris@example.com"]["servers"]) == 1
    assert len(owners["nadia@example.com"]["servers"]) == 1


def test_servers_overview_user_sees_only_their_own(admin, monkeypatch) -> None:
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
    u1 = _create_member(
        client, csrf, username="morris@example.com", self_service=True
    )
    u2 = _create_member(client, csrf, username="nadia@example.com")
    _create_server_for(client, csrf, u1["user"]["id"], template["id"], name="a")
    _create_server_for(client, csrf, u2["user"]["id"], template["id"], name="b")

    member, _ = _login(client.app, "morris@example.com", u1["password"])
    resp = member.get("/api/servers/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_admin"] is False
    # Exactly one owner group (the caller), with only their own server.
    assert len(body["owners"]) == 1
    assert body["owners"][0]["username"] == "morris@example.com"
    assert len(body["owners"][0]["servers"]) == 1


def test_servers_overview_requires_auth(admin) -> None:
    client, _, _ = admin
    anon = TestClient(client.app)
    assert anon.get("/api/servers/overview").status_code == 401


def test_server_stats_returns_normalized_points(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])

    resp = client.get(
        f"/api/users/{user_id}/servers/{server['id']}/stats?timeframe=day"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["timeframe"] == "day"
    assert len(body["points"]) == 2
    # cpu 0.25 -> 25.0 percent; other fields pass through.
    assert body["points"][0]["cpu_pct"] == 25.0
    assert body["points"][1]["cpu_pct"] == 50.0
    assert body["points"][0]["maxmem"] == 4096


def test_server_stats_invalid_timeframe(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    resp = client.get(
        f"/api/users/{user_id}/servers/{server['id']}/stats?timeframe=decade"
    )
    assert resp.status_code == 200
    assert resp.json()["available"] is False
    assert "timeframe" in resp.json()["detail"].lower()


def test_server_stats_no_vmid_unavailable(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    # A reference record with no vmid.
    from app.db import get_connection
    from app import repository
    with get_connection() as conn:
        ref = repository.create_user_server(
            conn, user_id=user_id, name="ref", kind="lxc", status="reference",
        )
    resp = client.get(f"/api/users/{user_id}/servers/{ref['id']}/stats")
    assert resp.status_code == 200
    assert resp.json()["available"] is False
    assert "no running guest" in resp.json()["detail"].lower()


def test_server_stats_authorization(admin, monkeypatch) -> None:
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
    owner = _create_member(
        client, csrf, username="morris@example.com", self_service=True
    )
    other = _create_member(client, csrf, username="nadia@example.com")
    server = _create_server_for(
        client, csrf, owner["user"]["id"], template["id"]
    )
    member, _ = _login(client.app, "morris@example.com", owner["password"])
    # Owner can read their own stats.
    assert member.get(
        f"/api/users/{owner['user']['id']}/servers/{server['id']}/stats"
    ).status_code == 200
    # But not another user's server stats (403 before any lookup).
    assert member.get(
        f"/api/users/{other['user']['id']}/servers/999/stats"
    ).status_code == 403


def test_get_guest_rrddata_rejects_bad_timeframe(monkeypatch) -> None:
    from app.proxmox import ProxmoxResult
    calls = []
    monkeypatch.setattr(
        proxmox, "_http_request",
        lambda *a, **k: calls.append(a) or (200, {"data": []}),
    )
    r = ProxmoxResult()
    out = proxmox.get_guest_rrddata(
        {"proxmox_url": "https://pve:8006", "proxmox_api_key": "k",
         "proxmox_token_name": "t@pam!x", "proxmox_verify_tls": False},
        "pve1", 120, "lxc", timeframe="decade", result=r,
    )
    assert out is None
    assert calls == []  # never calls the API for a bad timeframe
    assert r.status == "failed"


def test_server_stats_provider_unconfigured(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    # Unconfigure the provider after the server exists.
    client.patch(
        "/api/settings/provisioning",
        json={"proxmox_url": "", "proxmox_token_name": "", "proxmox_api_key": ""},
        headers={"X-CSRF-Token": csrf},
    )
    resp = client.get(
        f"/api/users/{user_id}/servers/{server['id']}/stats"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "not configured" in body["detail"].lower()


def test_server_stats_read_failure_unavailable(admin, monkeypatch) -> None:
    client, csrf, _ = admin

    class _RrdFailProxmox(_FakeProxmox):
        def __call__(self, method, url, *, headers, verify, json_body=None):
            if "/rrddata" in url:
                return (500, {"errors": "boom"})
            return super().__call__(method, url, headers=headers,
                                    verify=verify, json_body=json_body)

    _RrdFailProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    created = _create_member(client, csrf)
    user_id = created["user"]["id"]
    server = _create_server_for(client, csrf, user_id, template["id"])
    resp = client.get(
        f"/api/users/{user_id}/servers/{server['id']}/stats"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert "could not read" in body["detail"].lower()


def test_server_stats_admin_reads_other_users(admin, monkeypatch) -> None:
    """An admin may read any user's server stats."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)
    member = _create_member(client, csrf, username="morris@example.com")
    server = _create_server_for(
        client, csrf, member["user"]["id"], template["id"]
    )
    resp = client.get(
        f"/api/users/{member['user']['id']}/servers/{server['id']}/stats"
    )
    assert resp.status_code == 200
    assert resp.json()["available"] is True


def test_overview_never_leaks_admin_key_fields(admin, monkeypatch) -> None:
    """The overview response must not expose the template admin SSH key."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    template = _add_template(client, csrf)  # admin_ssh_key_path=/home/svc/...
    member = _create_member(client, csrf)
    _create_server_for(client, csrf, member["user"]["id"], template["id"])
    resp = client.get("/api/servers/overview")
    assert resp.status_code == 200
    text = resp.text
    assert "admin_ssh_key_path" not in text
    assert "admin_ssh_key_id" not in text
    assert "/home/svc/.ssh/id_ed25519" not in text
