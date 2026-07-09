"""Create-user auto-provisioning (issue_015-r3 C).

A new user can be created with a set of server templates; one server is
provisioned per template. Provisioning is best-effort: a missing provider or a
clone failure yields a per-template result but never blocks user creation.
"""

from __future__ import annotations

import subprocess

from app import proxmox, repository, servers
from app.db import get_connection


_VERSION_OK = (200, {"data": {"version": "8.2.4"}})
_TEMPLATE_ENTRY = {
    "vmid": 9001, "name": "tpl-debian", "type": "lxc", "template": 1,
    "node": "pve1",
}


class _FakeProxmox:
    def __init__(self, monkeypatch, *, ip="10.0.7.42", clone_ok=True):
        self.ip = ip
        self.clone_ok = clone_ok
        monkeypatch.setattr(proxmox, "_http_request", self)
        monkeypatch.setattr(proxmox, "_sleep", lambda s: None)

    def __call__(self, method, url, *, headers, verify, json_body=None):
        if "/version" in url:
            return _VERSION_OK
        if "/cluster/resources" in url:
            return (200, {"data": [_TEMPLATE_ENTRY]})
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
            return (200, {"data": {"status": "stopped", "exitstatus": "OK"}})
        if "/interfaces" in url:
            return (200, {"data": [
                {"name": "eth0", "inet": f"{self.ip}/24"},
            ]})
        if "/config" in url and json_body is None:
            return (200, {"data": {"cores": 2, "memory": 4096,
                                   "rootfs": "local:9001/x,size=20G"}})
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
            argv, returncode=self.rc, stdout="", stderr=""
        )


def _setup_provider(client, csrf):
    assert client.patch(
        "/api/settings/provisioning",
        json={"provider_type": "proxmox", "proxmox_url": "https://pve:8006",
              "proxmox_token_name": "svc@pam!app", "proxmox_api_key": "sekret"},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200


def _add_template(client, csrf, name="Debian Coder"):
    r = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": name, "kind": "lxc",
              "admin_ssh_key_path": "/home/svc/.ssh/id_ed25519"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_create_user_provisions_selected_templates(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    tpl = _add_template(client, csrf)

    resp = client.post(
        "/api/users",
        json={"username": "coder@example.com", "role": "user", "teams": [],
              "apps_server": "apps.example.com",
              "provision_templates": [tpl["id"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    results = body["provisioning"]
    assert len(results) == 1
    assert results[0]["template_id"] == tpl["id"]
    assert results[0]["status"] == "created"

    # The server exists and follows the <template-slug>-USERID naming convention.
    user_id = body["user"]["id"]
    derived = body["user"]["user_id"]
    with get_connection() as conn:
        servers_rows = repository.list_user_servers(conn, user_id)
    assert len(servers_rows) == 1
    assert servers_rows[0]["name"] == f"debian-coder-{derived}"
    assert servers_rows[0]["status"] == "created"


def test_create_user_provision_nonfatal_on_clone_failure(admin, monkeypatch) -> None:
    """A clone failure records a failed server but never blocks user creation."""
    client, csrf, _ = admin
    _FakeProxmox(monkeypatch, clone_ok=False)
    _FakeSsh(monkeypatch)
    _setup_provider(client, csrf)
    tpl = _add_template(client, csrf, name="Flaky")

    resp = client.post(
        "/api/users",
        json={"username": "flaky@example.com", "role": "user", "teams": [],
              "apps_server": "apps.example.com",
              "provision_templates": [tpl["id"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    results = body["provisioning"]
    assert len(results) == 1
    assert results[0]["status"] == "failed"

    # The user persists, and the failed server record survives the request
    # (proving per-guest commit, not an all-or-nothing rollback).
    user_id = body["user"]["id"]
    with get_connection() as conn:
        rows = repository.list_user_servers(conn, user_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"


def test_create_user_provision_nonfatal_without_provider(admin) -> None:
    client, csrf, _ = admin
    tpl = _add_template(client, csrf, name="No Provider")

    # No provider configured: user is still created; template is skipped.
    resp = client.post(
        "/api/users",
        json={"username": "np@example.com", "role": "user", "teams": [],
              "apps_server": "apps.example.com",
              "provision_templates": [tpl["id"]]},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["username"] == "np@example.com"
    results = body["provisioning"]
    assert len(results) == 1
    assert results[0]["status"] == "skipped"
    assert "provider" in results[0]["detail"].lower()

    with get_connection() as conn:
        assert repository.list_user_servers(conn, body["user"]["id"]) == []


def test_create_user_without_templates_provisions_nothing(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/users",
        json={"username": "plain@example.com", "role": "user", "teams": [],
              "apps_server": "apps.example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["provisioning"] == []
