"""Tests for the one-off apps-server backfill script.

The script rewrites every alias application whose stored apps_server is empty
or custom to the owner's registered apps-server hostname, skips non-alias apps,
already-registered apps, owners with no registered apps-server, and an explicit
exclusion list. It is idempotent and, on --apply, marks needs_push, audits the
change, and re-pushes the alias.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# The one-off lives in the repo-root scripts/ dir (not under backend/), so load
# it by path. It imports `from app import ...`, which resolves in the backend
# test environment.
_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "oneoff_backfill_apps_server.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "oneoff_backfill_apps_server", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """An initialized, isolated database plus the script module, wired so the
    reverse-proxy push is a no-op (no SSH)."""
    data = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(data))
    monkeypatch.setenv("APP_DB_PATH", str(data / "app.db"))
    monkeypatch.setenv("APP_ENABLE_AUTH", "1")
    monkeypatch.setenv("APP_DEV", "1")
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "logs"))

    from app.config import get_settings
    from app import keystore

    get_settings.cache_clear()
    keystore.reset_cache()

    from app import db, repository
    from app.routers import applications as apps_router

    db.init_db()

    script = _load_script()

    # Stub the actual reverse-proxy push so re-push does no SSH but still runs
    # the surrounding record/audit logic.
    pushes: list[int] = []

    def _fake_push(application_id, actor):
        pushes.append(application_id)
        with db.get_connection() as conn:
            repository.set_application_push_result(
                conn, application_id, status="ok", log="stubbed", needs_push=False
            )

    monkeypatch.setattr(script.apps_router, "_push_alias_on_approval", _fake_push)

    return {
        "db": db,
        "repository": repository,
        "apps_router": apps_router,
        "script": script,
        "pushes": pushes,
    }


def _seed(env, *, owner_apps_server_ref="ref"):
    """Create an owner with one registered apps-server server; return owner id
    and the registered hostname."""
    db = env["db"]
    repo = env["repository"]
    with db.get_connection() as conn:
        owner = repo.create_user(
            conn, username="owner@example.com", password="Passw0rd!x",
            role="user", teams=[], must_change_password=False,
            apps_server=owner_apps_server_ref,
        )
        tpl = repo.create_server_template(
            conn, vmid=9001, name="apps-tpl", kind="lxc", is_apps_server=True,
        )
        repo.create_user_server(
            conn, user_id=owner["id"], name="apps-owner",
            hostname="apps-owner.host", template_id=tpl["id"],
            template_name=tpl["name"], kind="lxc", ip_address="10.0.0.5",
            status="reference",
        )
    return owner["id"], "apps-owner.host"


def _app(env, **kw):
    repo = env["repository"]
    with env["db"].get_connection() as conn:
        return repo.create_application(conn, teams=[], **kw)


def _get(env, app_id):
    with env["db"].get_connection() as conn:
        return env["repository"].get_application(conn, app_id)


def _run(env, apply=False):
    return env["script"].main(["--apply"] if apply else [])


def test_custom_alias_is_changed_and_pushed(env):
    owner_id, host = _seed(env)
    app = _app(env, name="custom", url="/c", url_type="alias",
               created_by=owner_id, apps_server="totally-custom-host",
               apps_port="8080")

    rc = _run(env, apply=True)
    assert rc == 0
    updated = _get(env, app["id"])
    assert updated["apps_server"] == host
    assert app["id"] in env["pushes"]  # re-pushed
    # An audit row records the change.
    with env["db"].get_connection() as conn:
        events = conn.execute(
            "SELECT action, detail FROM audit_log WHERE action='apps_server_backfill'"
        ).fetchall()
    assert len(events) == 1
    assert "totally-custom-host -> apps-owner.host" in events[0]["detail"]


def test_empty_alias_is_set(env):
    owner_id, host = _seed(env)
    app = _app(env, name="empty", url="/e", url_type="alias",
               created_by=owner_id, apps_server="", apps_port="8080")
    _run(env, apply=True)
    assert _get(env, app["id"])["apps_server"] == host


def test_already_registered_is_unchanged(env):
    owner_id, host = _seed(env)
    app = _app(env, name="reg", url="/r", url_type="alias",
               created_by=owner_id, apps_server=host, apps_port="8080")
    _run(env, apply=True)
    assert _get(env, app["id"])["apps_server"] == host
    assert app["id"] not in env["pushes"]


def test_already_registered_by_ip_is_unchanged(env):
    """A stored value matching the registered server's IP (not hostname) is
    still classified 'registered' and left alone."""
    owner_id, host = _seed(env)
    app = _app(env, name="regip", url="/ri", url_type="alias",
               created_by=owner_id, apps_server="10.0.0.5", apps_port="8080")
    _run(env, apply=True)
    assert _get(env, app["id"])["apps_server"] == "10.0.0.5"
    assert app["id"] not in env["pushes"]


def test_idempotent_when_account_ref_matches_server_host(env):
    """When the owner's account apps_server reference literally equals the
    registered host, the resolver returns that host; applying twice is a
    no-op (locks the idempotency invariant for the resolver's literal branch)."""
    owner_id, host = _seed(env, owner_apps_server_ref="apps-owner.host")
    app = _app(env, name="lit", url="/l", url_type="alias",
               created_by=owner_id, apps_server="was-custom", apps_port="8080")
    _run(env, apply=True)
    assert _get(env, app["id"])["apps_server"] == host
    env["pushes"].clear()
    _run(env, apply=True)  # second run must be a no-op
    assert env["pushes"] == []


def test_url_app_is_unchanged(env):
    owner_id, _ = _seed(env)
    app = _app(env, name="url", url="https://x", url_type="url",
               created_by=owner_id, apps_server="")
    _run(env, apply=True)
    assert _get(env, app["id"])["apps_server"] == ""
    assert app["id"] not in env["pushes"]


def test_owner_without_registered_server_is_skipped(env):
    repo = env["repository"]
    with env["db"].get_connection() as conn:
        owner = repo.create_user(
            conn, username="noserver@example.com", password="Passw0rd!x",
            role="user", teams=[], must_change_password=False,
        )
    app = _app(env, name="ns", url="/n", url_type="alias",
               created_by=owner["id"], apps_server="custom-x", apps_port="8080")
    _run(env, apply=True)
    # Unchanged: nothing registered to point at.
    assert _get(env, app["id"])["apps_server"] == "custom-x"
    assert app["id"] not in env["pushes"]


def test_excluded_app_is_never_changed(env, monkeypatch):
    owner_id, host = _seed(env)
    app = _app(env, name="excluded", url="/x", url_type="alias",
               created_by=owner_id, apps_server="keep-me-custom",
               apps_port="8080")
    monkeypatch.setattr(env["script"], "EXCLUDED_APP_IDS", frozenset({app["id"]}))
    _run(env, apply=True)
    assert _get(env, app["id"])["apps_server"] == "keep-me-custom"
    assert app["id"] not in env["pushes"]


def test_dry_run_writes_nothing(env):
    owner_id, host = _seed(env)
    app = _app(env, name="dry", url="/d", url_type="alias",
               created_by=owner_id, apps_server="custom-dry", apps_port="8080")
    rc = _run(env, apply=False)
    assert rc == 0
    assert _get(env, app["id"])["apps_server"] == "custom-dry"  # unchanged
    assert env["pushes"] == []
    with env["db"].get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action='apps_server_backfill'"
        ).fetchone()["c"]
    assert n == 0


def test_idempotent(env):
    owner_id, host = _seed(env)
    app = _app(env, name="idem", url="/i", url_type="alias",
               created_by=owner_id, apps_server="custom-i", apps_port="8080")
    _run(env, apply=True)
    assert _get(env, app["id"])["apps_server"] == host
    env["pushes"].clear()
    # Second run: already registered -> no change, no push, no new audit row.
    _run(env, apply=True)
    assert env["pushes"] == []
    with env["db"].get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action='apps_server_backfill'"
        ).fetchone()["c"]
    assert n == 1
