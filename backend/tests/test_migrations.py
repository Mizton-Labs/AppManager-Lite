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
        assert {"created_by", "url_type", "approval_status", "is_private"} <= acols
        # Applications gained their own apps server/port, pending config fields,
        # and a push-needed flag.
        assert {
            "apps_server",
            "apps_port",
            "pending_alias",
            "pending_is_active",
            "needs_push",
            "apps_rewrite_root",
            "pending_apps_rewrite_root",
            "pass_authenticated_user",
            "pending_pass_authenticated_user",
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
        assert "application_user_shares" in tables
        # The settings table gained an optional SSH user column plus branding.
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(settings)")}
        assert "nginx_user" in scols
        assert {"app_name", "app_logo", "configured", "collaborators"} <= scols
        # The settings row is seeded with a non-empty default alias template.
        template = conn.execute(
            "SELECT alias_template FROM settings WHERE id = 1"
        ).fetchone()["alias_template"]
        assert "location /ALIAS/" in template
        # The authenticated-user header opt-in defaults to off for every
        # existing row migrated from a legacy database.
        app_row = conn.execute(
            "SELECT pass_authenticated_user, pending_pass_authenticated_user "
            "FROM applications LIMIT 1"
        ).fetchone()
        if app_row is not None:
            assert app_row["pass_authenticated_user"] == 0
            assert app_row["pending_pass_authenticated_user"] is None


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


def test_url_type_check_dropped_and_embedded_accepted(legacy_db: Path) -> None:
    """A database carrying the legacy url_type CHECK is rebuilt on migrate so
    'embedded' is accepted; existing rows and child rows are preserved."""
    from app import db

    # Bring the legacy DB up to the current schema first.
    db.init_db()

    # Seed a row + child, then reintroduce the legacy url_type CHECK to
    # reproduce a production database created from the older _SCHEMA.
    with db.get_connection() as conn:
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(applications)")]
        col_list = ", ".join(columns)
        conn.execute("INSERT INTO teams (name) VALUES ('CheckTeam')")
        team_id = conn.execute(
            "SELECT id FROM teams WHERE name = 'CheckTeam'"
        ).fetchone()["id"]
        app_id = conn.execute(
            "INSERT INTO applications (name, url, url_type) "
            "VALUES ('checked', 'grafana', 'alias') RETURNING id"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO application_teams (application_id, team_id) VALUES (?, ?)",
            (app_id, team_id),
        )

    # Rebuild applications WITH a url_type CHECK (legacy shape) on a dedicated
    # autocommit connection with FKs off, mirroring the real migration mechanics.
    create_checked = db._extract_create_table(db._SCHEMA, "applications").replace(
        "url_type        TEXT    NOT NULL DEFAULT 'url',",
        "url_type TEXT NOT NULL DEFAULT 'url' CHECK (url_type IN ('url', 'alias')),",
    )
    setup = db.connect()
    setup.isolation_level = None
    setup.execute("PRAGMA foreign_keys=OFF")
    setup.execute("BEGIN")
    setup.execute("ALTER TABLE applications RENAME TO applications_legacy")
    setup.execute(create_checked)
    setup.execute(
        f"INSERT INTO applications ({col_list}) "
        f"SELECT {col_list} FROM applications_legacy"
    )
    setup.execute("DROP TABLE applications_legacy")
    setup.execute("COMMIT")
    assert "CHECK (url_type" in setup.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
    ).fetchone()["sql"]
    setup.close()

    # Re-migrate: the CHECK must be dropped, twice-run to prove idempotence.
    with db.get_connection() as conn:
        db._migrate_schema(conn)
    with db.get_connection() as conn:
        db._migrate_schema(conn)
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='applications'"
        ).fetchone()["sql"]
        assert "CHECK (url_type" not in table_sql
        # Row + child preserved with a stable id.
        assert conn.execute(
            "SELECT name FROM applications WHERE id = ?", (app_id,)
        ).fetchone()["name"] == "checked"
        assert conn.execute(
            "SELECT COUNT(*) c FROM application_teams WHERE application_id = ?",
            (app_id,),
        ).fetchone()["c"] == 1
        # Child FKs must still reference the live applications table, never the
        # transient applications_old rewritten by a legacy_alter_table rename.
        for child in db._APPLICATION_CHILD_TABLES:
            child_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (child,),
            ).fetchone()["sql"]
            assert "applications_old" not in child_sql
            assert "REFERENCES applications" in child_sql.replace('"', "")
        # 'embedded' is now accepted at the DB layer.
        conn.execute(
            "INSERT INTO applications (name, url, url_type) "
            "VALUES ('emb', 'http://e', 'embedded')"
        )


def test_orphaned_child_fk_referencing_applications_old_is_repaired(
    legacy_db: Path,
) -> None:
    """A database damaged by the earlier buggy rebuild (child FKs pointing at a
    now-dropped applications_old) is self-healed on migrate: child DDL is
    rewritten back to reference applications, rows are preserved, and inserts
    into the child tables succeed again."""
    from app import db

    db.init_db()

    # Seed an application + one child row per affected table.
    with db.get_connection() as conn:
        conn.execute("INSERT INTO teams (name) VALUES ('OrphanTeam')")
        team_id = conn.execute(
            "SELECT id FROM teams WHERE name = 'OrphanTeam'"
        ).fetchone()["id"]
        conn.execute("INSERT INTO users (username, password_hash, role) "
                     "VALUES ('orphuser', 'x', 'user')")
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = 'orphuser'"
        ).fetchone()["id"]
        app_id = conn.execute(
            "INSERT INTO applications (name, url, url_type) "
            "VALUES ('orphaned', 'grafana', 'alias') RETURNING id"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO application_teams (application_id, team_id) VALUES (?, ?)",
            (app_id, team_id),
        )

    # Reproduce the damage: rewrite each child table's DDL so its FK targets the
    # nonexistent applications_old (exactly what the modern-SQLite rename did),
    # preserving rows/ids. Done with legacy_alter_table OFF + FKs off.
    setup = db.connect()
    setup.isolation_level = None
    setup.execute("PRAGMA foreign_keys=OFF")
    setup.execute("PRAGMA legacy_alter_table=OFF")
    setup.execute("BEGIN")
    for child in db._APPLICATION_CHILD_TABLES:
        columns = [r["name"] for r in setup.execute(f"PRAGMA table_info({child})")]
        col_list = ", ".join(columns)
        broken = db._extract_create_table(db._SCHEMA, child).replace(
            "REFERENCES applications(id)", 'REFERENCES "applications_old"(id)'
        )
        setup.execute(f"ALTER TABLE {child} RENAME TO {child}_broken")
        setup.execute(broken)
        setup.execute(
            f"INSERT INTO {child} ({col_list}) SELECT {col_list} FROM {child}_broken"
        )
        setup.execute(f"DROP TABLE {child}_broken")
    setup.execute("COMMIT")
    for child in db._APPLICATION_CHILD_TABLES:
        assert "applications_old" in setup.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (child,),
        ).fetchone()["sql"]
    setup.close()

    # Re-migrate (twice, to prove idempotence): the self-heal repairs every
    # child FK back to applications.
    with db.get_connection() as conn:
        db._migrate_schema(conn)
    with db.get_connection() as conn:
        db._migrate_schema(conn)
        for child in db._APPLICATION_CHILD_TABLES:
            child_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (child,),
            ).fetchone()["sql"]
            assert "applications_old" not in child_sql
        # Preserved child row survived the rebuild.
        assert conn.execute(
            "SELECT COUNT(*) c FROM application_teams WHERE application_id = ?",
            (app_id,),
        ).fetchone()["c"] == 1
        # Inserts into the previously-orphaned child tables now succeed
        # (the create-app 500 regression).
        conn.execute(
            "INSERT INTO application_user_shares (application_id, user_id) "
            "VALUES (?, ?)",
            (app_id, user_id),
        )
        conn.execute(
            "INSERT INTO application_favorites (user_id, application_id) "
            "VALUES (?, ?)",
            (user_id, app_id),
        )


def test_jump_management_split_columns_and_migration(legacy_db: Path) -> None:
    """The jump management/mode columns are added and jump_user is preserved."""
    from app import db

    db.init_db()
    with db.get_connection() as conn:
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(settings)")}
        assert {
            "jump_management_user",
            "jump_account_mode",
            "jump_jumper_user",
        } <= scols
        # Defaults: management user root, per-user account mode.
        row = conn.execute(
            "SELECT jump_management_user, jump_account_mode FROM settings "
            "WHERE id = 1"
        ).fetchone()
        assert row["jump_management_user"] == "root"
        assert row["jump_account_mode"] == "per_user"

        # Simulate a pre-split configured jump user, then re-migrate: it should
        # be copied into the new shared jumper-user column (idempotently).
        conn.execute(
            "UPDATE settings SET jump_user = 'cdt-jumper', jump_jumper_user = '' "
            "WHERE id = 1"
        )
        conn.commit()
        db._migrate_schema(conn)
        got = conn.execute(
            "SELECT jump_jumper_user FROM settings WHERE id = 1"
        ).fetchone()["jump_jumper_user"]
        assert got == "cdt-jumper"
