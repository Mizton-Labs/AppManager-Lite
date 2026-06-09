"""Idempotent schema migration from a pre-issue_004 database.

A database created before self-service, per-app ownership, alias URLs, the
approval workflow, and team ordering existed must upgrade in place when
``init_db`` runs, without losing data and without requiring a fresh database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# The legacy schema as it shipped before issue_004 (no self_service /
# created_by / url_type / approval_status / teams.sort_order columns).
_OLD_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','user')),
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE user_teams (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, team_id)
);
CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    icon_url TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE application_teams (
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    PRIMARY KEY (application_id, team_id)
);
"""


def _build_legacy_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "INSERT INTO users (username, password_hash, role) "
        "VALUES ('legacy_admin','x','admin')"
    )
    conn.execute(
        "INSERT INTO users (username, password_hash, role) "
        "VALUES ('legacy_user','x','user')"
    )
    conn.execute("INSERT INTO teams (name) VALUES ('Threat Intel')")
    conn.execute(
        "INSERT INTO applications (name, url) VALUES ('Legacy App','https://x/y')"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def legacy_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    db_path = data / "app.db"
    monkeypatch.setenv("APP_DATA_DIR", str(data))
    monkeypatch.setenv("APP_DB_PATH", str(db_path))
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("APP_LOG_TO_FILE", "0")

    from app.config import get_settings

    get_settings.cache_clear()
    _build_legacy_db(db_path)
    return db_path


def test_init_db_migrates_legacy_database(legacy_db: Path) -> None:
    from app import db

    # Running twice proves the migration is idempotent.
    db.init_db()
    db.init_db()

    with db.get_connection() as conn:
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        assert "self_service" in ucols
        # Users gained an apps server/port.
        assert {"apps_server", "apps_port"} <= ucols
        acols = {
            r["name"] for r in conn.execute("PRAGMA table_info(applications)")
        }
        assert {"created_by", "url_type", "approval_status"} <= acols
        # Applications gained their own apps server/port, pending config fields,
        # and a push-needed flag.
        assert {
            "apps_server",
            "apps_port",
            "pending_alias",
            "pending_is_active",
            "needs_push",
        } <= acols
        tcols = {r["name"] for r in conn.execute("PRAGMA table_info(teams)")}
        assert "sort_order" in tcols
        assert "icon" in tcols
        # The audit_log and settings tables are created on a legacy database too.
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "audit_log" in tables
        assert "settings" in tables
        # The settings table gained an optional SSH user column plus branding.
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(settings)")}
        assert "nginx_user" in scols
        assert {"app_name", "app_logo", "configured", "collaborators"} <= scols
        # The settings row is seeded with a non-empty default alias template.
        template = conn.execute(
            "SELECT alias_template FROM settings WHERE id = 1"
        ).fetchone()["alias_template"]
        assert "location /ALIAS/" in template


def test_legacy_admin_backfilled_to_self_service(legacy_db: Path) -> None:
    from app import db

    db.init_db()
    with db.get_connection() as conn:
        admin_ss = conn.execute(
            "SELECT self_service FROM users WHERE username='legacy_admin'"
        ).fetchone()["self_service"]
        user_ss = conn.execute(
            "SELECT self_service FROM users WHERE username='legacy_user'"
        ).fetchone()["self_service"]
    assert admin_ss == 1
    assert user_ss == 0


def test_legacy_application_gets_safe_defaults(legacy_db: Path) -> None:
    from app import db

    db.init_db()
    with db.get_connection() as conn:
        app = conn.execute(
            "SELECT * FROM applications WHERE name='Legacy App'"
        ).fetchone()
    assert app["url_type"] == "url"
    assert app["approval_status"] == "approved"
    assert app["created_by"] is None


def test_legacy_team_preserved_and_not_reseeded(legacy_db: Path) -> None:
    from app import db

    db.init_db()
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT name, sort_order, icon FROM teams ORDER BY sort_order, id"
        ).fetchall()
    names = [r["name"] for r in rows]
    # Teams are administrator-managed and no longer seeded: the legacy team is
    # preserved as-is and no canonical defaults are inserted.
    assert names == ["Threat Intel"]
    assert rows[0]["icon"] == ""
