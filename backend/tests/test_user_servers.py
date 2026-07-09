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
                 extra_iface_ips=None):
        self.calls: list[str] = []
        self.template_resources = template_resources or {
            "cores": 2, "memory": 4096, "rootfs": "local:9001/x,size=20G",
        }
        self.clone_ok = clone_ok
        self.start_ok = start_ok
        self.ip = ip
        # Additional IPv4s the hypervisor reports on the guest's interfaces
        # (beyond the primary ``ip``). Used to exercise F2 corroboration: an
        # in-guest report is only adopted if it appears in the hypervisor view.
        self.extra_iface_ips = list(extra_iface_ips or [])
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
            ifaces = [
                {"name": "lo", "inet": "127.0.0.1/8"},
                {"name": "eth0", "inet": f"{self.ip}/24"},
            ]
            for i, extra in enumerate(self.extra_iface_ips, start=1):
                ifaces.append({"name": f"eth{i}", "inet": f"{extra}/24"})
            return (200, {"data": ifaces})
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
    assert [s["name"] for s in listed.json()] == ["coder box"]


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


def test_server_usage_admin_created_servers_excluded(admin, monkeypatch) -> None:
    """Admin-set servers are quota-exempt, so they must not count in usage."""
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
    # Admin creates a server FOR the member (admin_modified -> quota-exempt).
    assert client.post(
        f"/api/users/{user_id}/servers",
        json={"template_id": template["id"], "name": "admin-made"},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 201

    member, _ = _login(client.app, "srvuser@example.com", created["password"])
    body = member.get(f"/api/users/{user_id}/servers/usage").json()
    # The server counts toward the SERVER count but its resources are exempt.
    assert body["servers"]["used"] == 1
    assert body["cpus"]["used"] == 0
    assert body["memory_gb"]["used"] == 0
    assert body["disk_gb"]["used"] == 0


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
