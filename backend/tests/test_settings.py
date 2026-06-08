"""Reverse-proxy settings and per-user apps server/port."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import reverse_proxy
from app.reverse_proxy import _Run


@pytest.fixture(autouse=True)
def _seed_teams(admin, make_team):
    for _name in ("Red Team",):
        make_team(_name)


def _create_member(client, csrf, username, **extra):
    body = {"username": username, "role": "user", "teams": ["Red Team"]}
    body.update(extra)
    resp = client.post(
        "/api/users", json=body, headers={"X-CSRF-Token": csrf}
    )
    return resp


def test_reverse_proxy_settings_default_template(admin) -> None:
    client, _csrf, _ = admin
    resp = client.get("/api/settings/reverse-proxy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Seeded with the default alias template; secrets/keys are never stored.
    assert "location /ALIAS/" in body["alias_template"]
    assert body["nginx_host"] == ""
    assert body["ssh_key_path"] == ""


def test_update_reverse_proxy_settings(admin) -> None:
    client, csrf, _ = admin
    resp = client.patch(
        "/api/settings/reverse-proxy",
        json={
            "nginx_host": "proxy.example.com",
            "nginx_user": "deploy",
            "nginx_conf_path": "/etc/nginx/conf.d/apps.conf",
            "ssh_key_path": "/data/keys/proxy_ed25519",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nginx_host"] == "proxy.example.com"
    assert body["nginx_user"] == "deploy"
    assert body["nginx_conf_path"] == "/etc/nginx/conf.d/apps.conf"
    assert body["ssh_key_path"] == "/data/keys/proxy_ed25519"
    # Persisted.
    again = client.get("/api/settings/reverse-proxy").json()
    assert again["nginx_host"] == "proxy.example.com"


def test_settings_reject_unsafe_paths(admin) -> None:
    client, csrf, _ = admin
    resp = client.patch(
        "/api/settings/reverse-proxy",
        json={"ssh_key_path": "/data/key; rm -rf /"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


def test_settings_reject_bad_host(admin) -> None:
    client, csrf, _ = admin
    resp = client.patch(
        "/api/settings/reverse-proxy",
        json={"nginx_host": "bad host!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


def test_settings_reject_bad_ssh_user(admin) -> None:
    client, csrf, _ = admin
    resp = client.patch(
        "/api/settings/reverse-proxy",
        json={"nginx_user": "root; rm -rf /"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


def test_settings_require_admin(admin) -> None:
    client, csrf, _ = admin
    resp = _create_member(client, csrf, "settingsmember")
    password = resp.json()["password"]
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login",
            json={"username": "settingsmember", "password": password},
        )
        get_resp = member.get("/api/settings/reverse-proxy")
        member_csrf = member.get("/api/session").json().get("csrf_token")
        patch_resp = member.patch(
            "/api/settings/reverse-proxy",
            json={"nginx_host": "x.example.com"},
            headers={"X-CSRF-Token": member_csrf or ""},
        )
    assert get_resp.status_code == 403
    assert patch_resp.status_code == 403


def test_create_user_with_apps_server(admin) -> None:
    client, csrf, _ = admin
    resp = _create_member(
        client, csrf, "appsuser", apps_server="apps.example.com"
    )
    assert resp.status_code == 201, resp.text
    user = resp.json()["user"]
    assert user["apps_server"] == "apps.example.com"
    # The per-user port was removed; each application carries its own port.
    assert "apps_port" not in user
    # Visible in the admin user listing.
    listed = {u["username"]: u for u in client.get("/api/users").json()}
    assert listed["appsuser"]["apps_server"] == "apps.example.com"


def test_create_user_rejects_bad_apps_server(admin) -> None:
    client, csrf, _ = admin
    resp = _create_member(client, csrf, "badserver", apps_server="bad host!")
    assert resp.status_code == 422


def test_update_user_apps_server(admin) -> None:
    client, csrf, _ = admin
    user_id = _create_member(client, csrf, "updapps").json()["user"]["id"]
    resp = client.patch(
        f"/api/users/{user_id}",
        json={"apps_server": "moved.example.com"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["apps_server"] == "moved.example.com"
    assert "apps_port" not in resp.json()


# --- approval -> reverse-proxy push (SSH mocked) ---------------------------


def _configure_proxy(client, csrf):
    resp = client.patch(
        "/api/settings/reverse-proxy",
        json={
            "nginx_host": "proxy.example.com",
            "nginx_conf_path": "/etc/nginx/conf.d/apps.conf",
            "ssh_key_path": "/data/keys/k",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text


def _mock_ssh_ok(monkeypatch):
    def run(argv, *, timeout=20):
        cmd = argv[-1]
        if "cat " in cmd:
            return _Run(0, "http {\n  server {\n  }\n}", "")
        return _Run(0, "", "")

    def run_with_input(argv, stdin_text):
        return _Run(0, "", "")

    monkeypatch.setattr(reverse_proxy, "_run", run)
    monkeypatch.setattr(reverse_proxy, "_run_with_input", run_with_input)


def test_approving_alias_app_pushes_to_proxy(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)

    # A member submits an alias app with its own port (stays pending). The
    # server host is resolved from the reverse-proxy settings.
    member_pw = _create_member(
        client,
        csrf,
        "proxyuser",
        apps_server="apps.example.com",
    ).json()["password"]
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login",
            json={"username": "proxyuser", "password": member_pw},
        )
        mcsrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "Grafana",
                "url": "grafana",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        ).json()["id"]

    # Admin approves -> the push runs (mocked) and records an ok status + log.
    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_push_status"] == "ok"
    assert "nginx reloaded" in body["last_push_log"]

    # The push is also in the audit log.
    events = client.get("/api/audit", params={"category": "application"}).json()
    assert any(e["action"] == "nginx_push" for e in events)


def test_approving_non_alias_app_skips_push(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)

    member_pw = _create_member(
        client, csrf, "urluser", apps_server="apps.example.com"
    ).json()["password"]
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "urluser", "password": member_pw}
        )
        mcsrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "Full URL App",
                "url": "https://example.com/app",
                "url_type": "url",
                "teams": ["Red Team"],
            },
            headers={"X-CSRF-Token": mcsrf},
        ).json()["id"]

    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_push_status"] == "skipped"


def test_push_reverts_on_reload_failure_via_api(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)

    def run(argv, *, timeout=20):
        cmd = argv[-1]
        if "cat " in cmd:
            return _Run(0, "http {\n  server {\n  }\n}", "")
        if "nginx -s reload" in cmd:
            return _Run(1, "", "reload failed")
        return _Run(0, "", "")

    monkeypatch.setattr(reverse_proxy, "_run", run)
    monkeypatch.setattr(
        reverse_proxy, "_run_with_input", lambda argv, stdin_text: _Run(0, "", "")
    )

    member_pw = _create_member(
        client, csrf, "revertuser", apps_server="apps.example.com"
    ).json()["password"]
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login",
            json={"username": "revertuser", "password": member_pw},
        )
        mcsrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "Reverting App",
                "url": "revertme",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        ).json()["id"]

    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_push_status"] == "reverted"
    events = client.get("/api/audit", params={"category": "application"}).json()
    assert any(e["action"] == "nginx_revert" for e in events)


# --- push retry ------------------------------------------------------------


def _create_pending_alias_app(client, csrf, username):
    """A member submits an alias app with its own port (stays pending)."""
    member_pw = _create_member(
        client, csrf, username, apps_server="apps.example.com"
    ).json()["password"]
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": username, "password": member_pw}
        )
        mcsrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "Retryable",
                "url": "retryme",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        ).json()["id"]
    return app_id, member_pw


def test_retry_push_recovers_a_failed_push(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)

    # First approval pushes with a failing reload -> reverted.
    def failing(argv, *, timeout=20):
        cmd = argv[-1]
        if "cat " in cmd:
            return _Run(0, "http {\n  server {\n  }\n}", "")
        if "nginx -s reload" in cmd:
            return _Run(1, "", "reload failed")
        return _Run(0, "", "")

    monkeypatch.setattr(reverse_proxy, "_run", failing)
    monkeypatch.setattr(
        reverse_proxy, "_run_with_input", lambda argv, s: _Run(0, "", "")
    )

    app_id, _ = _create_pending_alias_app(client, csrf, "retryuser")
    approved = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert approved.json()["last_push_status"] == "reverted"

    # Now the remote is healthy; retrying succeeds.
    _mock_ssh_ok(monkeypatch)
    retry = client.post(
        f"/api/applications/{app_id}/push-retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["last_push_status"] == "ok"


def test_retry_push_requires_admin(admin) -> None:
    client, csrf, _ = admin
    # Admin creates an approved app to target.
    app_id = client.post(
        "/api/applications",
        json={"name": "Approved", "url": "https://example.com/a", "teams": ["Red Team"]},
        headers={"X-CSRF-Token": csrf},
    ).json()["id"]
    member_pw = _create_member(client, csrf, "retrynonadmin").json()["password"]
    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login",
            json={"username": "retrynonadmin", "password": member_pw},
        )
        mcsrf = member.get("/api/session").json().get("csrf_token") or ""
        resp = member.post(
            f"/api/applications/{app_id}/push-retry",
            headers={"X-CSRF-Token": mcsrf},
        )
    assert resp.status_code == 403


def test_retry_push_rejects_unapproved(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)
    app_id, _ = _create_pending_alias_app(client, csrf, "retrypending")
    # The app is still pending -> retry is a conflict.
    resp = client.post(
        f"/api/applications/{app_id}/push-retry",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409


# --- alias removal on delete (issue_008) -----------------------------------


def test_admin_created_alias_app_pushes_with_app_apps_server(admin, monkeypatch) -> None:
    # An admin creates an alias app and supplies apps_server/apps_port on the
    # request; the push uses those (admins have no per-user apps server).
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)
    resp = client.post(
        "/api/applications",
        json={
            "name": "Admin Alias",
            "url": "adminalias",
            "url_type": "alias",
            "teams": ["Red Team"],
            "apps_server": "apps.example.com",
            "apps_port": "8080",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["apps_server"] == "apps.example.com"
    assert body["last_push_status"] == "ok"


def test_non_admin_apps_server_on_create_is_ignored(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)
    member_pw = _create_member(client, csrf, "ignoreapps").json()["password"]
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "ignoreapps", "password": member_pw}
        )
        mcsrf = login.json()["csrf_token"]
        resp = member.post(
            "/api/applications",
            json={
                "name": "Member Alias",
                "url": "memberalias",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_server": "evil.example.com",
                "apps_port": "9999",
            },
            headers={"X-CSRF-Token": mcsrf},
        )
        app_id = resp.json()["id"]
        # The member sees their own app via /mine; apps_server was not stored.
        mine = {a["id"]: a for a in member.get("/api/applications/mine").json()}
    assert mine[app_id]["apps_server"] == ""


def _mock_ssh_conf(monkeypatch, conf_text):
    """Mock SSH where `cat` returns the given conf; capture written conf."""
    captured = {}

    def run(argv, *, timeout=20):
        cmd = argv[-1]
        if "cat " in cmd:
            return _Run(0, conf_text, "")
        return _Run(0, "", "")

    def run_with_input(argv, stdin_text):
        captured["written"] = stdin_text
        return _Run(0, "", "")

    monkeypatch.setattr(reverse_proxy, "_run", run)
    monkeypatch.setattr(reverse_proxy, "_run_with_input", run_with_input)
    return captured


def test_deleting_alias_app_removes_marked_block(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)

    # Admin creates + auto-approves an alias app (pushed with a marker).
    app_id = client.post(
        "/api/applications",
        json={
            "name": "ToRemove",
            "url": "toremove",
            "url_type": "alias",
            "teams": ["Red Team"],
            "apps_server": "apps.example.com",
            "apps_port": "8080",
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["id"]

    # The remote conf now contains this app's marked block.
    begin, end = reverse_proxy.app_marker(app_id)
    conf = (
        "http {\n  server {\n"
        f"    {begin}\n    location /toremove/ {{ proxy_pass http://x; }}\n    {end}\n"
        "  }\n}\n"
    )
    captured = _mock_ssh_conf(monkeypatch, conf)

    resp = client.request(
        "DELETE",
        f"/api/applications/{app_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    # The written conf had the marked block excised.
    assert "written" in captured
    assert begin not in captured["written"]
    assert "location /toremove/" not in captured["written"]
    # The removal is audited.
    events = client.get("/api/audit", params={"category": "application"}).json()
    assert any(e["action"] == "nginx_remove" for e in events)


def test_deleting_non_alias_app_does_not_remove(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)
    app_id = client.post(
        "/api/applications",
        json={"name": "URLApp", "url": "https://example.com/x", "teams": ["Red Team"]},
        headers={"X-CSRF-Token": csrf},
    ).json()["id"]
    resp = client.request(
        "DELETE",
        f"/api/applications/{app_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    events = client.get("/api/audit", params={"category": "application"}).json()
    assert not any(e["action"] == "nginx_remove" for e in events)


# --- alias-change approval staging (issue_009) -----------------------------


def _approved_member_alias_app(client, csrf, monkeypatch, username, alias):
    """Create a member-owned alias app and approve it (returns id + password)."""
    member_pw = _create_member(
        client, csrf, username, apps_server="apps.example.com"
    ).json()["password"]
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": username, "password": member_pw}
        )
        mcsrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "Staged",
                "url": alias,
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        ).json()["id"]
    client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    return app_id, member_pw


def test_owner_alias_change_is_staged_not_applied(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)
    app_id, member_pw = _approved_member_alias_app(
        client, csrf, monkeypatch, "stager", "graf"
    )

    # The owner edits the alias: the live URL is unchanged, the new value is
    # staged, and the application stays approved and active.
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "stager", "password": member_pw}
        )
        mcsrf = login.json()["csrf_token"]
        resp = member.patch(
            f"/api/applications/{app_id}",
            json={"url": "graf-new", "url_type": "alias"},
            headers={"X-CSRF-Token": mcsrf},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == "graf"
    assert body["pending_alias"] == "graf-new"
    assert body["approval_status"] == "approved"
    assert body["is_active"] is True

    # The app remains live on its current alias for members.
    listing = client.get(
        "/api/applications", params={"team": "Red Team"}
    ).json()
    assert any(a["id"] == app_id and a["url"] == "graf" for a in listing)

    # The staged change is audited.
    events = client.get("/api/audit", params={"category": "application"}).json()
    assert any(e["action"] == "alias_change_requested" for e in events)


def test_admin_approval_applies_staged_alias_and_pushes(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)
    app_id, member_pw = _approved_member_alias_app(
        client, csrf, monkeypatch, "stager2", "graf"
    )
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "stager2", "password": member_pw}
        )
        mcsrf = login.json()["csrf_token"]
        member.patch(
            f"/api/applications/{app_id}",
            json={"url": "graf-new", "url_type": "alias"},
            headers={"X-CSRF-Token": mcsrf},
        )

    # The admin approves the staged change: it is applied to the live URL, the
    # staging field is cleared, and the push runs (mocked).
    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"approval_status": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == "graf-new"
    assert body["pending_alias"] == ""
    assert body["last_push_status"] == "ok"
    events = client.get("/api/audit", params={"category": "application"}).json()
    assert any(e["action"] == "alias_change_approved" for e in events)


def test_admin_alias_change_applies_immediately(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)
    # An admin-owned alias app: an admin edit applies the alias immediately
    # (no staging), since admins apply changes directly.
    app_id = client.post(
        "/api/applications",
        json={
            "name": "AdminAlias",
            "url": "adm",
            "url_type": "alias",
            "teams": ["Red Team"],
            "apps_server": "apps.example.com",
            "apps_port": "8080",
        },
        headers={"X-CSRF-Token": csrf},
    ).json()["id"]
    resp = client.patch(
        f"/api/applications/{app_id}",
        json={"url": "adm-new", "url_type": "alias"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == "adm-new"
    assert body["pending_alias"] == ""


def test_self_service_owner_alias_change_applies_immediately(admin, monkeypatch) -> None:
    client, csrf, _ = admin
    _configure_proxy(client, csrf)
    _mock_ssh_ok(monkeypatch)
    # A self-service member's edits go live directly, so an alias change is not
    # staged.
    member_pw = _create_member(
        client, csrf, "selfsvc", apps_server="apps.example.com", self_service=True
    ).json()["password"]
    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login", json={"username": "selfsvc", "password": member_pw}
        )
        mcsrf = login.json()["csrf_token"]
        app_id = member.post(
            "/api/applications",
            json={
                "name": "SelfAlias",
                "url": "self",
                "url_type": "alias",
                "teams": ["Red Team"],
                "apps_port": "8080",
            },
            headers={"X-CSRF-Token": mcsrf},
        ).json()["id"]
        resp = member.patch(
            f"/api/applications/{app_id}",
            json={"url": "self-new", "url_type": "alias"},
            headers={"X-CSRF-Token": mcsrf},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == "self-new"
    assert body["pending_alias"] == ""
