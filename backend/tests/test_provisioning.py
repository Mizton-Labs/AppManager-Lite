"""Server-provisioning settings, Proxmox provider client, server templates."""

from __future__ import annotations

import pytest

from app import proxmox


def _patch_proxmox(monkeypatch, responses):
    """Patch the HTTP seam; ``responses`` maps a URL fragment to (status, payload)."""
    calls = []

    def fake_request(method, url, *, headers, verify, json_body=None):
        calls.append({"method": method, "url": url, "headers": headers,
                      "verify": verify})
        for fragment, reply in responses.items():
            if fragment in url:
                return reply
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(proxmox, "_http_request", fake_request)
    return calls


_VERSION_OK = (200, {"data": {"version": "8.2.4"}})
_RESOURCES = (
    200,
    {
        "data": [
            {"vmid": 9001, "name": "tpl-debian-coder", "type": "lxc",
             "template": 1, "node": "pve1"},
            {"vmid": 9002, "name": "tpl-debian-apps", "type": "lxc",
             "template": 1, "node": "pve1"},
            {"vmid": 200, "name": "runtime-apps-box", "type": "lxc",
             "template": 0, "node": "pve1"},
            {"vmid": 300, "name": "tpl-win-vm", "type": "qemu",
             "template": 1, "node": "pve2"},
        ]
    },
)

_PROVIDER_BODY = {
    "provider_type": "proxmox",
    "proxmox_url": "https://pve.example.com:8006",
    "proxmox_token_name": "svc@pam!appmanager",
    "proxmox_api_key": "sekret-token-value",
}


# ---------------------------------------------------------------------------
# Proxmox client unit behavior
# ---------------------------------------------------------------------------


def test_normalize_url_accepts_and_rejects() -> None:
    assert proxmox.normalize_url("https://10.0.0.5:8006/") == "https://10.0.0.5:8006"
    for bad in ("", "ftp://x", "https://host;rm -rf", "https://ho st"):
        with pytest.raises(proxmox.ProxmoxError):
            proxmox.normalize_url(bad)


def test_auth_header_never_in_transcript(monkeypatch) -> None:
    _patch_proxmox(monkeypatch, {"/version": _VERSION_OK})
    config = {
        "proxmox_url": "https://pve:8006",
        "proxmox_token_name": "svc@pam!tok",
        "proxmox_api_key": "super-secret",
        "proxmox_verify_tls": True,
    }
    result = proxmox.test_connection(config)
    assert result.status == "ok"
    assert "super-secret" not in result.transcript
    assert "PVEAPIToken" not in result.transcript


def test_connection_failure_modes(monkeypatch) -> None:
    _patch_proxmox(monkeypatch, {"/version": (401, "auth failure")})
    config = dict(_PROVIDER_BODY, proxmox_verify_tls=True)
    result = proxmox.test_connection(config)
    assert result.status == "failed"
    assert "401" in result.transcript


def test_list_templates_filters(monkeypatch) -> None:
    _patch_proxmox(monkeypatch, {"/cluster/resources": _RESOURCES})
    config = dict(
        _PROVIDER_BODY,
        proxmox_verify_tls=True,
        proxmox_template_filter="debian",
        proxmox_templates_only=True,
    )
    result = proxmox.list_templates(config)
    assert result.status == "ok"
    assert [t["vmid"] for t in result.data] == [9002, 9001]
    assert all(t["kind"] == "lxc" for t in result.data)

    # Include non-templates when the toggle is off.
    config["proxmox_templates_only"] = False
    config["proxmox_template_filter"] = "apps"
    result = proxmox.list_templates(config)
    assert {t["vmid"] for t in result.data} == {200, 9002}


def test_tls_verification_flag_passed_and_warned(monkeypatch) -> None:
    calls = _patch_proxmox(monkeypatch, {"/version": _VERSION_OK})
    config = dict(_PROVIDER_BODY, proxmox_verify_tls=False)
    result = proxmox.test_connection(config)
    assert calls[0]["verify"] is False
    assert "TLS certificate verification is disabled" in result.transcript


def test_plain_http_url_warns(monkeypatch) -> None:
    _patch_proxmox(monkeypatch, {"/version": _VERSION_OK})
    config = dict(_PROVIDER_BODY, proxmox_url="http://pve:8006")
    result = proxmox.test_connection(config)
    assert "transits unencrypted" in result.transcript


def test_local_config_problems_fail_without_raising() -> None:
    # A hand-edited row (whitespace key, bad URL) must fail, not raise/500.
    for config in (
        dict(_PROVIDER_BODY, proxmox_api_key="   "),
        dict(_PROVIDER_BODY, proxmox_url="ftp://bad"),
    ):
        result = proxmox.test_connection(config)
        assert result.status == "failed"


def test_entries_without_vmid_are_skipped(monkeypatch) -> None:
    payload = (200, {"data": [
        {"name": "broken", "type": "lxc", "template": 1},
        {"vmid": 9001, "name": "ok", "type": "lxc", "template": 1,
         "node": "pve1"},
    ]})
    _patch_proxmox(monkeypatch, {"/cluster/resources": payload})
    result = proxmox.list_templates(dict(_PROVIDER_BODY))
    assert [t["vmid"] for t in result.data] == [9001]


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------


def test_get_provisioning_defaults(admin) -> None:
    client, _, _ = admin
    resp = client.get("/api/settings/provisioning")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider_type"] == ""
    assert body["proxmox_api_key_set"] is False
    assert body["proxmox_templates_only"] is True
    assert body["proxmox_verify_tls"] is True
    assert body["provisioning_self_service"] is False
    assert body["provisioning_max_servers"] == 3
    assert body["provisioning_max_cpus"] == 12
    assert body["provisioning_max_memory_gb"] == 24
    assert body["provisioning_max_disk_gb"] == 200


def test_provisioning_requires_admin(admin) -> None:
    client, csrf, _ = admin
    create = client.post(
        "/api/users",
        json={"username": "puser@example.com", "role": "user", "teams": [],
              "apps_server": "apps.example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert create.status_code == 201

    from fastapi.testclient import TestClient

    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login",
            json={"username": "puser@example.com",
                  "password": create.json()["password"]},
        )
        member_csrf = login.json()["csrf_token"]
        assert member.get("/api/settings/provisioning").status_code == 403
        assert member.patch(
            "/api/settings/provisioning",
            json={"provisioning_max_servers": 99},
            headers={"X-CSRF-Token": member_csrf},
        ).status_code == 403
        assert member.get("/api/settings/server-templates").status_code == 403


def test_save_provider_runs_connection_test_and_hides_key(
    admin, monkeypatch
) -> None:
    client, csrf, _ = admin
    _patch_proxmox(monkeypatch, {"/version": _VERSION_OK})

    resp = client.patch(
        "/api/settings/provisioning",
        json=_PROVIDER_BODY,
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proxmox_conn_status"] == "ok"
    assert "8.2.4" in body["proxmox_conn_log"]
    assert body["proxmox_api_key_set"] is True
    # The secret never appears anywhere in the response.
    assert "sekret-token-value" not in resp.text

    # Audit mentions field names but never the key value.
    events = client.get("/api/audit?category=system").json()
    update = [e for e in events if e["action"] == "settings_update"][0]
    assert "proxmox_api_key(updated)" in update["detail"]
    assert "sekret-token-value" not in update["detail"]


def test_get_settings_never_returns_secret(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _patch_proxmox(monkeypatch, {"/version": _VERSION_OK})
    client.patch(
        "/api/settings/provisioning",
        json=_PROVIDER_BODY,
        headers={"X-CSRF-Token": csrf},
    )
    resp = client.get("/api/settings/provisioning")
    assert resp.status_code == 200
    assert "sekret-token-value" not in resp.text
    assert resp.json()["proxmox_api_key_set"] is True


def test_clearing_provider_resets_connection_status(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _patch_proxmox(monkeypatch, {"/version": _VERSION_OK})
    client.patch(
        "/api/settings/provisioning",
        json=_PROVIDER_BODY,
        headers={"X-CSRF-Token": csrf},
    )
    resp = client.patch(
        "/api/settings/provisioning",
        json={"proxmox_url": ""},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proxmox_conn_status"] == ""
    assert body["proxmox_conn_log"] == ""


def test_null_values_do_not_count_as_changes(admin, monkeypatch) -> None:
    client, csrf, _ = admin

    def boom(*a, **kw):  # pragma: no cover
        raise AssertionError("nulls must not trigger a connection test")

    monkeypatch.setattr(proxmox, "_http_request", boom)
    resp = client.patch(
        "/api/settings/provisioning",
        json={"proxmox_api_key": None, "proxmox_url": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    events = client.get("/api/audit?category=system").json()
    updates = [e for e in events if e["action"] == "settings_update"
               and e["target_name"] == "provisioning"]
    assert all("proxmox_api_key(updated)" not in e["detail"] for e in updates)


def test_failed_connection_recorded(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _patch_proxmox(monkeypatch, {"/version": (401, "bad token")})
    resp = client.patch(
        "/api/settings/provisioning",
        json=_PROVIDER_BODY,
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    assert resp.json()["proxmox_conn_status"] == "failed"
    assert "401" in resp.json()["proxmox_conn_log"]


def test_policy_update_does_not_touch_connection(admin, monkeypatch) -> None:
    client, csrf, _ = admin

    def boom(*a, **kw):  # pragma: no cover - fails the test when called
        raise AssertionError("connection test must not run for policy changes")

    monkeypatch.setattr(proxmox, "_http_request", boom)
    resp = client.patch(
        "/api/settings/provisioning",
        json={"provisioning_self_service": True, "provisioning_max_servers": 5,
              "provisioning_max_cpus": 24},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provisioning_self_service"] is True
    assert body["provisioning_max_servers"] == 5
    assert body["provisioning_max_cpus"] == 24


def test_invalid_provider_values_rejected(admin) -> None:
    client, csrf, _ = admin
    for bad in (
        {"proxmox_url": "not-a-url"},
        {"provider_type": "vmware"},
        {"proxmox_token_name": "has space"},
        {"provisioning_max_servers": -1},
        {"provisioning_max_cpus": 0},
    ):
        resp = client.patch(
            "/api/settings/provisioning",
            json=bad,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 422, (bad, resp.text)


def test_provider_templates_failure_path(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _patch_proxmox(
        monkeypatch,
        {"/version": _VERSION_OK, "/cluster/resources": (403, "no privs")},
    )
    client.patch(
        "/api/settings/provisioning",
        json=_PROVIDER_BODY,
        headers={"X-CSRF-Token": csrf},
    )
    resp = client.get("/api/settings/provisioning/provider-templates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["templates"] == []
    assert "403" in body["log"]


def test_provider_templates_preview(admin, monkeypatch) -> None:
    client, csrf, _ = admin

    # Not configured yet -> 400.
    assert client.get("/api/settings/provisioning/provider-templates").status_code == 400

    _patch_proxmox(
        monkeypatch, {"/version": _VERSION_OK, "/cluster/resources": _RESOURCES}
    )
    client.patch(
        "/api/settings/provisioning",
        json={**_PROVIDER_BODY, "proxmox_template_filter": "tpl-"},
        headers={"X-CSRF-Token": csrf},
    )
    resp = client.get("/api/settings/provisioning/provider-templates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    names = [t["name"] for t in body["templates"]]
    assert names == ["tpl-debian-apps", "tpl-debian-coder", "tpl-win-vm"]
    assert "sekret-token-value" not in resp.text


# ---------------------------------------------------------------------------
# Server templates CRUD
# ---------------------------------------------------------------------------


def test_server_template_crud(admin) -> None:
    client, csrf, _ = admin

    created = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "Debian Coder", "kind": "lxc",
              "admin_ssh_key_path": "/home/svc/.ssh/id_ed25519"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    template = created.json()
    assert template["kind"] == "lxc"

    # Duplicate name -> 409.
    dup = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9002, "name": "Debian Coder", "kind": "vm"},
        headers={"X-CSRF-Token": csrf},
    )
    assert dup.status_code == 409

    listed = client.get("/api/settings/server-templates")
    assert [t["name"] for t in listed.json()] == ["Debian Coder"]

    updated = client.patch(
        f"/api/settings/server-templates/{template['id']}",
        json={"name": "Debian Coder v2", "vmid": 9010},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["vmid"] == 9010

    # Rename collision -> 409; unknown id -> 404.
    other = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9020, "name": "Other", "kind": "lxc"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    collide = client.patch(
        f"/api/settings/server-templates/{other['id']}",
        json={"name": "Debian Coder v2"},
        headers={"X-CSRF-Token": csrf},
    )
    assert collide.status_code == 409
    missing_patch = client.patch(
        "/api/settings/server-templates/12345",
        json={"name": "Ghost"},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing_patch.status_code == 404
    client.delete(
        f"/api/settings/server-templates/{other['id']}",
        headers={"X-CSRF-Token": csrf},
    )

    deleted = client.delete(
        f"/api/settings/server-templates/{template['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
    assert client.get("/api/settings/server-templates").json() == []

    missing = client.delete(
        "/api/settings/server-templates/12345",
        headers={"X-CSRF-Token": csrf},
    )
    assert missing.status_code == 404


def test_server_template_validation(admin) -> None:
    client, csrf, _ = admin
    for bad in (
        {"vmid": 0, "name": "x", "kind": "lxc"},
        {"vmid": 1, "name": "", "kind": "lxc"},
        {"vmid": 1, "name": "   ", "kind": "lxc"},
        {"vmid": 1, "name": "x", "kind": "container"},
        {"vmid": 1, "name": "x", "kind": "lxc",
         "admin_ssh_key_path": "/tmp/key; rm -rf /"},
        # Relative / option-injection / scp-remote shapes must be rejected.
        {"vmid": 1, "name": "x", "kind": "lxc",
         "admin_ssh_key_path": "-oProxyCommand=evil"},
        {"vmid": 1, "name": "x", "kind": "lxc",
         "admin_ssh_key_path": "evil:path"},
        {"vmid": 1, "name": "x", "kind": "lxc",
         "admin_ssh_key_path": "./relative"},
    ):
        resp = client.post(
            "/api/settings/server-templates",
            json=bad,
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 422, (bad, resp.text)
