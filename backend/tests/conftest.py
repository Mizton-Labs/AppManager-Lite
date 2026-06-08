"""Shared pytest fixtures.

Each test runs against an isolated SQLite database in a temporary directory.
The application settings cache is cleared per test so environment overrides take
effect, and the app is built fresh via ``create_app``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ADMIN_USERNAME = "admin"


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    enable_auth: bool = True,
    base_prefix: str = "",
) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(data))
    monkeypatch.setenv("APP_DB_PATH", str(data / "app.db"))
    monkeypatch.setenv("APP_FRONTEND_DIST", str(tmp_path / "no-dist"))
    monkeypatch.setenv("APP_ENABLE_AUTH", "1" if enable_auth else "0")
    monkeypatch.setenv("APP_BASE_PREFIX", base_prefix)
    monkeypatch.setenv("APP_DEV", "1")
    # Keep file logging inside the per-test tmp dir so the repo is never touched.
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "logs"))

    from app.config import get_settings

    get_settings.cache_clear()


def _build_client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


def _read_admin_password(tmp_path: Path) -> str:
    text = (tmp_path / "data" / "first-run-admin-credentials.txt").read_text()
    for line in text.splitlines():
        if line.startswith("password:"):
            return line.split("password:", 1)[1].strip()
    raise AssertionError("first-run admin password not found")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure(monkeypatch, tmp_path)
    with _build_client() as test_client:
        # Convenience: stash the generated first-run password on the client.
        test_client.admin_password = _read_admin_password(tmp_path)  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture
def client_no_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure(monkeypatch, tmp_path, enable_auth=False)
    with _build_client() as test_client:
        yield test_client


@pytest.fixture
def admin(client: TestClient):
    """An authenticated admin client past the forced password change.

    Returns ``(client, csrf_token, password)``.
    """
    first_pw = client.admin_password  # type: ignore[attr-defined]
    resp = client.post(
        "/api/auth/login", json={"username": ADMIN_USERNAME, "password": first_pw}
    )
    assert resp.status_code == 200, resp.text
    csrf = resp.json()["csrf_token"]

    new_pw = "AdminStrongPass123"
    resp = client.post(
        "/api/account/password",
        json={
            "current_password": first_pw,
            "new_password": new_pw,
            "confirm_password": new_pw,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text

    csrf = client.get("/api/session").json()["csrf_token"]
    return client, csrf, new_pw


@pytest.fixture
def make_team(admin):
    """Factory to create a team via the admin API and return its JSON.

    Teams are no longer seeded, so tests that need named teams (e.g. for user
    or application membership) create them explicitly. Usage::

        def test_x(admin, make_team):
            make_team("Red Team")
    """
    client, csrf, _ = admin

    def _make(name: str, icon: str = "") -> dict:
        resp = client.post(
            "/api/settings/teams",
            json={"name": name, "icon": icon},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make
