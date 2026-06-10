"""User configuration bundle templates and account downloads."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_member(client, csrf, username="bundleuser@example.com", **extra):
    body = {"username": username, "role": "user", "teams": []}
    body.update(extra)
    resp = client.post("/api/users", json=body, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_template(client, csrf):
    resp = client.post(
        "/api/settings/bundle-templates",
        json={
            "name": "Shell profile",
            "content": "USER runs on APPS_SERVER as ROLE",
            "mappings": [
                {"field_name": "USER", "source": "username"},
                {"field_name": "APPS_SERVER", "source": "user_apps_server"},
                {"field_name": "ROLE", "source": "user_role"},
            ],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_admin_manages_bundle_templates(admin) -> None:
    client, csrf, _ = admin
    created = _create_template(client, csrf)

    assert created["name"] == "Shell profile"
    assert created["mappings"][0] == {"field_name": "USER", "source": "username"}
    listed = client.get("/api/settings/bundle-templates")
    assert [template["name"] for template in listed.json()] == ["Shell profile"]

    updated = client.patch(
        f"/api/settings/bundle-templates/{created['id']}",
        json={
            "name": "SSH profile",
            "mappings": [{"field_name": "USER", "source": "username"}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "SSH profile"
    assert updated.json()["mappings"] == [
        {"field_name": "USER", "source": "username"}
    ]

    deleted = client.delete(
        f"/api/settings/bundle-templates/{created['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get("/api/settings/bundle-templates").json() == []


def test_bundle_template_rejects_unknown_mapping_source(admin) -> None:
    client, csrf, _ = admin
    resp = client.post(
        "/api/settings/bundle-templates",
        json={
            "name": "Bad",
            "content": "VALUE",
            "mappings": [{"field_name": "VALUE", "source": "password"}],
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 422


def test_non_admin_cannot_manage_bundle_templates(admin) -> None:
    client, csrf, _ = admin
    created = _create_member(client, csrf)
    password = created["password"]

    with TestClient(client.app) as member:
        login = member.post(
            "/api/auth/login",
            json={"username": "bundleuser@example.com", "password": password},
        )
        member_csrf = login.json()["csrf_token"]
        get_resp = member.get("/api/settings/bundle-templates")
        post_resp = member.post(
            "/api/settings/bundle-templates",
            json={"name": "Nope", "content": "x", "mappings": []},
            headers={"X-CSRF-Token": member_csrf},
        )

    assert get_resp.status_code == 403
    assert post_resp.status_code == 403


def test_account_download_renders_bundle_for_current_user(admin) -> None:
    client, csrf, _ = admin
    template = _create_template(client, csrf)
    created = _create_member(
        client,
        csrf,
        username="analyst@example.com",
        apps_server="apps.example.com",
    )

    with TestClient(client.app) as member:
        member.post(
            "/api/auth/login",
            json={"username": "analyst@example.com", "password": created["password"]},
        )
        options = member.get("/api/account/bundles")
        download = member.get(f"/api/account/bundles/{template['id']}/download")

    assert options.status_code == 200, options.text
    assert options.json() == [{"id": template["id"], "name": "Shell profile"}]
    assert download.status_code == 200, download.text
    assert download.text == "analyst@example.com runs on apps.example.com as user"
    assert "attachment" in download.headers["content-disposition"]
