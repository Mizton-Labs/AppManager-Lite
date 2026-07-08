"""Bundle template per-server vars + builtin SSH-config (issue_015-r2 D)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import repository


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


def test_mapping_sources_include_indexed_servers() -> None:
    assert "server1_ip" in repository.BUNDLE_MAPPING_SOURCES
    assert "server8_user" in repository.BUNDLE_MAPPING_SOURCES
    assert "server9_ip" not in repository.BUNDLE_MAPPING_SOURCES


def test_per_server_bundle_variables_render(admin) -> None:
    client, csrf, _ = admin
    template = client.post(
        "/api/settings/bundle-templates",
        json={"name": "PerServer",
              "content": "s1=S1IP user=S1USER s2=S2IP",
              "mappings": [
                  {"field_name": "S1IP", "source": "server1_ip"},
                  {"field_name": "S1USER", "source": "server1_user"},
                  {"field_name": "S2IP", "source": "server2_ip"},
              ]},
        headers={"X-CSRF-Token": csrf},
    )
    assert template.status_code == 201, template.text
    created = _create_member(client, csrf)
    uid = created["user"]["id"]
    _seed_servers(uid, [
        {"name": "alpha", "hostname": "alpha", "ip_address": "10.0.0.1"},
        {"name": "beta", "hostname": "beta", "ip_address": "10.0.0.2"},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])
    dl = member.get(f"/api/account/bundles/{template.json()['id']}/download")
    assert dl.status_code == 200
    # server1 -> alpha (first by name), user falls back to derived user id.
    assert "s1=10.0.0.1" in dl.text
    assert "user=bt" in dl.text
    assert "s2=10.0.0.2" in dl.text


# ---------------------------------------------------------------------------
# Builtin SSH-config template
# ---------------------------------------------------------------------------


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
              "jump_user": "bastion", "jump_port": 2222,
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
    text = member.get(f"/api/account/bundles/{builtin['id']}/download").text
    assert "Host *" in text
    assert "ServerAliveInterval 60" in text
    assert "ServerAliveCountMax 3" in text
    assert "TCPKeepAlive yes" in text
    # Jump block with the configured port + identity file.
    assert "Host jumpserver" in text
    assert "Hostname 10.9.9.9" in text
    assert "User bastion" in text
    assert "Port 2222" in text
    assert "IdentityFile ~/.ssh/id_ed25519" in text
    # Per-server block references the jump via ProxyJump.
    assert "Host coder-box" in text
    assert "ProxyJump jumpserver" in text


def test_server_user_uses_template_main_user(admin) -> None:
    """serverN_user and builtin Host blocks use the template main user."""
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
         "template_id": tpl["id"]},
    ])
    member, _ = _login(client.app, "bt@example.com", created["password"])

    # Per-server mapping var resolves to the template main user.
    per = client.post(
        "/api/settings/bundle-templates",
        json={"name": "MUvars", "content": "u=S1USER",
              "mappings": [{"field_name": "S1USER", "source": "server1_user"}]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    dl = member.get(f"/api/account/bundles/{per['id']}/download")
    assert "u=ubuntu" in dl.text

    # Builtin config's Host block uses the main user too.
    builtin = next(
        t for t in member.get("/api/account/bundles").json()
        if t["name"] == "SSH Config Default"
    )
    text = member.get(f"/api/account/bundles/{builtin['id']}/download").text
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
    text = member.get(f"/api/account/bundles/{builtin['id']}/download").text
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
