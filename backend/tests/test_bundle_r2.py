"""Bundle template per-server vars + builtin SSH-config (issue_015-r2 D)."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from app import repository


def _zip_members(response) -> dict[str, bytes]:
    """Open a bundle-download zip response and return {name: bytes}."""
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    return {name: archive.read(name) for name in archive.namelist()}


def _config_text(response) -> str:
    """The 'config' member of a bundle-download zip, decoded to text."""
    return _zip_members(response)["config"].decode()


def _create_member(client, csrf, username="bt@example.com"):
    r = client.post("/api/users",
                    json={"username": username, "role": "user", "teams": [],
                          "apps_server": "a.example.com"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text
    return r.json()


def _login(app, username, password):
    m = TestClient(app)
    login = m.post("/api/auth/login",
                   json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return m, login.json()["csrf_token"]


def _seed_servers(user_id, rows):
    from app.db import get_connection
    with get_connection() as conn:
        for r in rows:
            repository.create_user_server(
                conn, user_id=user_id, kind="lxc", status="created", **r
            )


# ---------------------------------------------------------------------------
# Per-server mapping variables
# ---------------------------------------------------------------------------


def test_mapping_sources_are_static_only() -> None:
    # Static sources are always available; server-template variables are
    # dynamic and no longer part of the static BUNDLE_MAPPING_SOURCES tuple.
    assert "user_id" in repository.BUNDLE_MAPPING_SOURCES
    assert "username" in repository.BUNDLE_MAPPING_SOURCES
    assert not any(
        s.startswith("server") for s in repository.BUNDLE_MAPPING_SOURCES
    )


def test_per_template_bundle_variables_render(admin) -> None:
    client, csrf, _ = admin
    # Two server templates provide the named variables.
    web = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9101, "name": "Web Box", "kind": "lxc"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    db = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9102, "name": "DB Box", "kind": "lxc"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    template = client.post(
        "/api/settings/bundle-templates",
        json={"name": "PerTemplate",
              "content": "web=WEBIP user=WEBUSER db=DBIP",
              "mappings": [
                  {"field_name": "WEBIP", "source": "server_web-box_ip"},
                  {"field_name": "WEBUSER", "source": "server_web-box_user"},
                  {"field_name": "DBIP", "source": "server_db-box_ip"},
              ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert template.status_code == 201, template.text
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _seed_servers(uid, [
        {"name": "alpha", "hostname": "alpha", "ip_address": "10.0.0.1",
         "template_id": web["id"], "template_name": "Web Box"},
        {"name": "beta", "hostname": "beta", "ip_address": "10.0.0.2",
         "template_id": db["id"], "template_name": "DB Box"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])
    dl = member.get(f"/api/account/bundles/{template.json()['id']}/download")
    assert dl.status_code == 200
    cfg = _config_text(dl)
    # web-box -> the user's first Web Box server; user falls back to user id.
    assert "web=10.0.0.1" in cfg
    assert "user=bt" in cfg
    assert "db=10.0.0.2" in cfg


def test_unknown_template_mapping_source_is_rejected(admin) -> None:
    client, csrf, _ = admin
    r = client.post(
        "/api/settings/bundle-templates",
        json={"name": "Bad",
              "content": "x=X",
              "mappings": [
                  {"field_name": "X", "source": "server_nope_ip"},
              ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400, r.text


def test_stale_template_mapping_renders_empty(admin) -> None:
    client, csrf, _ = admin
    tpl = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9103, "name": "Temp Box", "kind": "lxc"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    bundle = client.post(
        "/api/settings/bundle-templates",
        json={"name": "Stale",
              "content": "ip=[TIP]",
              "mappings": [
                  {"field_name": "TIP", "source": "server_temp-box_ip"},
              ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert bundle.status_code == 201, bundle.text
    # Delete the template the mapping refers to: it becomes stale.
    assert client.delete(
        f"/api/settings/server-templates/{tpl['id']}",
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200
    created = _create_member(client, csrf)
    member, _ = _login(client.app, "bt@example.com", created["password"])
    dl = member.get(f"/api/account/bundles/{bundle.json()['id']}/download")
    assert dl.status_code == 200
    # The stale variable renders empty rather than erroring.
    assert "ip=[]" in _config_text(dl)


def test_edit_bundle_grandfathers_stale_template_source(admin) -> None:
    """Editing a bundle keeps a since-deleted template's source (no 400)."""
    client, csrf, _ = admin
    tpl = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9104, "name": "Gone Box", "kind": "lxc"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    bundle = client.post(
        "/api/settings/bundle-templates",
        json={"name": "Grand",
              "content": "ip=GIP",
              "mappings": [
                  {"field_name": "GIP", "source": "server_gone-box_ip"},
              ]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    assert client.delete(
        f"/api/settings/server-templates/{tpl['id']}",
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200
    # Editing only the content, keeping the now-stale source, must succeed.
    upd = client.patch(
        f"/api/settings/bundle-templates/{bundle['id']}",
        json={"content": "ip=GIP updated",
              "mappings": [
                  {"field_name": "GIP", "source": "server_gone-box_ip"},
              ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert upd.status_code == 200, upd.text
    # But introducing a NEW unknown template source still fails.
    bad = client.patch(
        f"/api/settings/bundle-templates/{bundle['id']}",
        json={"mappings": [
            {"field_name": "GIP", "source": "server_gone-box_ip"},
            {"field_name": "NEW", "source": "server_never-existed_ip"},
        ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400, bad.text


# ---------------------------------------------------------------------------
# Builtin SSH-config template
# ---------------------------------------------------------------------------


def test_bundle_description_round_trips(admin) -> None:
    client, csrf, _ = admin
    created = client.post(
        "/api/settings/bundle-templates",
        json={"name": "Described", "content": "x",
              "description": "A helpful bundle", "mappings": []},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    assert created.json()["description"] == "A helpful bundle"
    tid = created.json()["id"]
    # Update the description.
    upd = client.patch(
        f"/api/settings/bundle-templates/{tid}",
        json={"description": "Updated text"},
        headers={"X-CSRF-Token": csrf},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["description"] == "Updated text"
    # It also surfaces on the account download list.
    member = client
    listed = member.get("/api/account/bundles").json()
    described = next(o for o in listed if o["name"] == "Described")
    assert described["description"] == "Updated text"


def test_builtin_has_default_description(admin) -> None:
    client, _, _ = admin
    templates = client.get("/api/settings/bundle-templates").json()
    builtin = next(t for t in templates if t["name"] == "SSH Config Default")
    assert builtin["description"]  # seeded with a non-empty default


def test_clone_carries_description(admin) -> None:
    client, csrf, _ = admin
    src = client.post(
        "/api/settings/bundle-templates",
        json={"name": "SrcDesc", "content": "x",
              "description": "carry me", "mappings": []},
        headers={"X-CSRF-Token": csrf},
    ).json()
    clone = client.post(
        f"/api/settings/bundle-templates/{src['id']}/clone",
        json={"name": "SrcDesc copy"},
        headers={"X-CSRF-Token": csrf},
    )
    assert clone.status_code == 201, clone.text
    assert clone.json()["description"] == "carry me"


def test_builtin_template_is_seeded_and_readonly(admin) -> None:
    client, csrf, _ = admin
    templates = client.get("/api/settings/bundle-templates").json()
    builtin = next(t for t in templates if t["name"] == "SSH Config Default")
    assert builtin["is_builtin"] is True
    assert builtin["enabled"] is True

    # Cannot edit or delete a builtin.
    upd = client.patch(
        f"/api/settings/bundle-templates/{builtin['id']}",
        json={"content": "hacked"},
        headers={"X-CSRF-Token": csrf},
    )
    assert upd.status_code == 409
    dele = client.delete(
        f"/api/settings/bundle-templates/{builtin['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert dele.status_code == 409


def test_builtin_render_with_jump_server(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    # Register a key + enable the jump server with a custom port.
    key = client.post(
        "/api/settings/ssh-keys",
        json={"name": "jk", "kind": "path", "path": "/k"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    client.patch(
        "/api/settings/provisioning",
        json={"jump_enabled": True, "jump_host": "10.9.9.9",
              "jump_management_user": "root", "jump_port": 2222,
              "jump_ssh_key_id": key["id"]},
        headers={"X-CSRF-Token": csrf},
    )
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _seed_servers(uid, [
        {"name": "coder box", "hostname": "coder-box", "ip_address": "10.0.0.5"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])
    builtin = next(
        t for t in member.get("/api/account/bundles").json()
        if t["name"] == "SSH Config Default"
    )
    text = _config_text(member.get(f"/api/account/bundles/{builtin['id']}/download"))
    assert "Host *" in text
    assert "ServerAliveInterval 60" in text
    assert "ServerAliveCountMax 3" in text
    assert "TCPKeepAlive yes" in text
    # Jump block with the configured port + identity file. In per-user mode
    # (default) the jump login is the user's own derived id.
    assert "Host jumpserver" in text
    assert "Hostname 10.9.9.9" in text
    assert "User bt" in text
    assert "Port 2222" in text
    assert "IdentityFile ~/.ssh/id_ed25519" in text
    # Per-server block references the jump via ProxyJump.
    assert "Host coder-box" in text
    assert "ProxyJump jumpserver" in text


def _enable_jump(client, csrf, **over):
    key = client.post(
        "/api/settings/ssh-keys",
        json={"name": "jk", "kind": "path", "path": "/k"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    body = {"jump_enabled": True, "jump_host": "10.9.9.9",
            "jump_management_user": "root", "jump_port": 2222,
            "jump_ssh_key_id": key["id"]}
    body.update(over)
    r = client.patch(
        "/api/settings/provisioning", json=body,
        headers={"X-CSRF-Token": csrf},
    )
    return r


def test_builtin_bundle_uses_override_address(admin) -> None:
    """With the override on, the bundle jump block uses the bundle host/port."""
    client, csrf, _ = admin
    assert _enable_jump(
        client, csrf,
        jump_bundle_override=True,
        jump_bundle_host="public.example.com",
        jump_bundle_port=443,
    ).status_code == 200
    created = _create_member(client, csrf)
    _seed_servers(created["user"]["id"], [
        {"name": "cbox", "hostname": "cbox", "ip_address": "10.0.0.5"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])
    builtin = next(
        t for t in member.get("/api/account/bundles").json()
        if t["name"] == "SSH Config Default"
    )
    text = _config_text(member.get(f"/api/account/bundles/{builtin['id']}/download"))
    # Bundle jump block uses the override address, not the management one.
    assert "Hostname public.example.com" in text
    assert "Port 443" in text
    assert "10.9.9.9" not in text
    assert "Port 2222" not in text
    # Onboarding user + ProxyJump alias are unchanged (per-user mode -> the
    # user's own derived id is the jump login).
    assert "User bt" in text
    assert "ProxyJump jumpserver" in text


def test_builtin_bundle_falls_back_when_override_off(admin) -> None:
    """Override off (default) keeps using the management host/port."""
    client, csrf, _ = admin
    # Provide a bundle host but leave the override disabled: it is ignored.
    assert _enable_jump(
        client, csrf,
        jump_bundle_override=False,
        jump_bundle_host="public.example.com",
        jump_bundle_port=443,
    ).status_code == 200
    created = _create_member(client, csrf)
    _seed_servers(created["user"]["id"], [
        {"name": "cbox", "hostname": "cbox", "ip_address": "10.0.0.5"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])
    builtin = next(
        t for t in member.get("/api/account/bundles").json()
        if t["name"] == "SSH Config Default"
    )
    text = _config_text(member.get(f"/api/account/bundles/{builtin['id']}/download"))
    assert "Hostname 10.9.9.9" in text
    assert "Port 2222" in text
    assert "public.example.com" not in text


def test_bundle_override_requires_host(admin) -> None:
    """Enabling the override without a bundle host is rejected."""
    client, csrf, _ = admin
    r = _enable_jump(client, csrf, jump_bundle_override=True)
    assert r.status_code == 400, r.text
    assert "bundle host" in r.json()["detail"].lower()


def test_bundle_override_settings_round_trip(admin) -> None:
    client, csrf, _ = admin
    assert _enable_jump(
        client, csrf, jump_bundle_override=True,
        jump_bundle_host="pub.example.com", jump_bundle_port=2020,
    ).status_code == 200
    got = client.get("/api/settings/provisioning").json()
    assert got["jump_bundle_override"] is True
    assert got["jump_bundle_host"] == "pub.example.com"
    assert got["jump_bundle_port"] == 2020



def test_server_user_uses_template_main_user(admin) -> None:
    """Template-scoped _user vars and builtin Host blocks use the main user."""
    client, csrf, _ = admin
    # A template with a fixed main user.
    tpl = client.post(
        "/api/settings/server-templates",
        json={"vmid": 9001, "name": "MU", "kind": "lxc",
              "main_os_user": "ubuntu"},
        headers={"X-CSRF-Token": csrf},
    ).json()
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _seed_servers(uid, [
        {"name": "srv", "hostname": "srv", "ip_address": "10.0.0.3",
         "template_id": tpl["id"], "template_name": "MU"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])

    # Per-template mapping var resolves to the template main user.
    per = client.post(
        "/api/settings/bundle-templates",
        json={"name": "MUvars", "content": "u=MUUSER",
              "mappings": [{"field_name": "MUUSER", "source": "server_mu_user"}]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    dl = member.get(f"/api/account/bundles/{per['id']}/download")
    assert "u=ubuntu" in _config_text(dl)

    # Builtin config's Host block uses the main user too.
    builtin = next(
        t for t in member.get("/api/account/bundles").json()
        if t["name"] == "SSH Config Default"
    )
    text = _config_text(member.get(f"/api/account/bundles/{builtin['id']}/download"))
    assert "Host srv" in text
    assert "User ubuntu" in text


def test_builtin_render_without_jump_has_no_proxyjump(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _seed_servers(uid, [
        {"name": "solo", "hostname": "solo", "ip_address": "10.0.0.7"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])
    builtin = next(
        t for t in member.get("/api/account/bundles").json()
        if t["name"] == "SSH Config Default"
    )
    text = _config_text(member.get(f"/api/account/bundles/{builtin['id']}/download"))
    assert "Host solo" in text
    assert "ProxyJump" not in text
    assert "Host jumpserver" not in text


# ---------------------------------------------------------------------------
# Clone + enable/disable
# ---------------------------------------------------------------------------


def test_clone_builtin_creates_editable_copy(admin) -> None:
    client, csrf, _ = admin
    builtin = next(
        t for t in client.get("/api/settings/bundle-templates").json()
        if t["name"] == "SSH Config Default"
    )
    clone = client.post(
        f"/api/settings/bundle-templates/{builtin['id']}/clone",
        json={"name": "My SSH config"},
        headers={"X-CSRF-Token": csrf},
    )
    assert clone.status_code == 201, clone.text
    assert clone.json()["is_builtin"] is False
    # The clone is editable and deletable.
    cid = clone.json()["id"]
    upd = client.patch(
        f"/api/settings/bundle-templates/{cid}",
        json={"content": "edited"},
        headers={"X-CSRF-Token": csrf},
    )
    assert upd.status_code == 200
    assert client.delete(
        f"/api/settings/bundle-templates/{cid}",
        headers={"X-CSRF-Token": csrf},
    ).status_code == 200


def test_disable_hides_from_account_downloads(admin) -> None:
    client, csrf, _ = admin
    builtin = next(
        t for t in client.get("/api/settings/bundle-templates").json()
        if t["name"] == "SSH Config Default"
    )
    created = _create_member(client, csrf)
    member, _ = _login(client.app, "bt@example.com", created["password"])
    # Visible while enabled.
    assert any(
        o["name"] == "SSH Config Default"
        for o in member.get("/api/account/bundles").json()
    )
    # Disable it.
    r = client.patch(
        f"/api/settings/bundle-templates/{builtin['id']}/enabled",
        json={"enabled": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    # Hidden from account list and download 404s.
    assert not any(
        o["name"] == "SSH Config Default"
        for o in member.get("/api/account/bundles").json()
    )
    assert member.get(
        f"/api/account/bundles/{builtin['id']}/download"
    ).status_code == 404


def test_bundle_zip_includes_key_and_connect_scripts(admin) -> None:
    """issue_019: the download is a zip with config, keys (private key without a
    file extension) and a connect script per server."""
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _seed_servers(uid, [
        {"name": "alpha", "hostname": "alpha", "ip_address": "10.0.0.1"},
        {"name": "beta", "hostname": "beta", "ip_address": "10.0.0.2"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])
    builtin = next(
        t for t in member.get("/api/account/bundles").json()
        if t["name"] == "SSH Config Default"
    )
    dl = member.get(f"/api/account/bundles/{builtin['id']}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"
    assert dl.headers["cache-control"] == "no-store"
    archive = zipfile.ZipFile(io.BytesIO(dl.content))
    members = {name: archive.read(name) for name in archive.namelist()}

    # The private key entry has NO .txt/.pub extension so it drops into ~/.ssh.
    key_name = "id_ed25519_appmanager"
    assert key_name in members
    assert members[key_name].decode().startswith(
        "-----BEGIN OPENSSH PRIVATE KEY-----"
    )
    assert (key_name + ".pub") in members
    assert members[key_name + ".pub"].decode().startswith("ssh-ed25519 ")

    # File modes: private key 0600, public 0644, scripts 0755, config 0644.
    def _mode(name: str) -> int:
        return (archive.getinfo(name).external_attr >> 16) & 0o777

    assert _mode(key_name) == 0o600
    assert _mode(key_name + ".pub") == 0o644
    assert _mode("config") == 0o644
    assert _mode("connect_server_alpha.sh") == 0o755

    # The config references the same key filename.
    assert f"IdentityFile ~/.ssh/{key_name}" in members["config"].decode()

    # One connect script per server, deferring to the bundled config.
    assert "connect_server_alpha.sh" in members
    assert "connect_server_beta.sh" in members
    script = members["connect_server_alpha.sh"].decode()
    assert script.startswith("#!/bin/sh")
    assert "ssh -F ./config" in script
    assert "alpha" in script
    # The identity is overridden to the key shipped beside the script so it can
    # be run in place from the unzip directory (issue_020).
    assert 'IdentityFile="$(dirname "$0")/id_ed25519_appmanager"' in script
    assert "IdentitiesOnly=yes" in script

    # The private-key-in-bundle download is audited.
    audit_resp = client.get("/api/audit?category=user")
    assert any(
        e["action"] == "ssh_key_download" and "bundle" in e.get("detail", "")
        for e in audit_resp.json()
    )


def test_bundle_zip_mapping_scripts_are_self_contained(admin) -> None:
    """Mapping-template bundles get self-contained connect scripts (their
    'config' text may not be a valid ssh config)."""
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _seed_servers(uid, [
        {"name": "gamma", "hostname": "gamma", "ip_address": "10.0.0.9"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])
    tpl = client.post(
        "/api/settings/bundle-templates",
        json={"name": "Plain", "content": "user=WHO",
              "mappings": [{"field_name": "WHO", "source": "user_id"}]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    dl = member.get(f"/api/account/bundles/{tpl['id']}/download")
    members = _zip_members(dl)
    assert members["config"].decode() == "user=bt"
    script = members["connect_server_gamma.sh"].decode()
    # Self-contained: uses the bundled key and the server IP directly.
    assert "ssh -i" in script
    assert "10.0.0.9" in script
    assert "-F ./config" not in script
