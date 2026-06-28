"""SQLite access and schema bootstrap.

A new connection is opened per request (and per CLI invocation). All queries
elsewhere use parameter binding; no SQL is built from user input.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .config import get_settings
from .reverse_proxy import DEFAULT_ALIAS_TEMPLATE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT    NOT NULL UNIQUE,
    password_hash        TEXT    NOT NULL,
    role                 TEXT    NOT NULL CHECK (role IN ('admin', 'user')),
    is_active            INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    self_service         INTEGER NOT NULL DEFAULT 0,
    apps_server          TEXT    NOT NULL DEFAULT '',
    apps_server_ip       TEXT    NOT NULL DEFAULT '',
    apps_port            TEXT    NOT NULL DEFAULT '',
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS teams (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    icon       TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS user_teams (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, team_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT    NOT NULL,
    auth_method TEXT   NOT NULL DEFAULT 'local'
                        CHECK (auth_method IN ('local', 'oidc', 'saml')),
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sso_auth_flows (
    state      TEXT    PRIMARY KEY,
    protocol   TEXT    NOT NULL CHECK (protocol IN ('oidc', 'saml')),
    nonce      TEXT    NOT NULL DEFAULT '',
    return_to  TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    url             TEXT    NOT NULL,
    url_type        TEXT    NOT NULL DEFAULT 'url'
                        CHECK (url_type IN ('url', 'alias')),
    icon_url        TEXT    NOT NULL DEFAULT '',
    is_active       INTEGER NOT NULL DEFAULT 1,
    approval_status TEXT    NOT NULL DEFAULT 'approved'
                        CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    created_by      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    apps_server      TEXT    NOT NULL DEFAULT '',
    apps_port        TEXT    NOT NULL DEFAULT '',
    pending_alias    TEXT    NOT NULL DEFAULT '',
    pending_is_active INTEGER,
    needs_push       INTEGER NOT NULL DEFAULT 0,
    last_push_status TEXT,
    last_push_log    TEXT    NOT NULL DEFAULT '',
    last_push_at     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS application_teams (
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    team_id        INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    PRIMARY KEY (application_id, team_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    category       TEXT    NOT NULL
                       CHECK (category IN ('application', 'user', 'system')),
    action         TEXT    NOT NULL,
    actor_id       INTEGER,
    actor_username TEXT,
    target_type    TEXT,
    target_id      INTEGER,
    target_name    TEXT,
    detail         TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS bundle_templates (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bundle_template_mappings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES bundle_templates(id) ON DELETE CASCADE,
    field_name  TEXT    NOT NULL,
    source      TEXT    NOT NULL,
    UNIQUE(template_id, field_name)
);

-- Single-row admin-editable settings (reverse-proxy configuration). The row is
-- pinned to id = 1 and seeded with defaults by init_db.
CREATE TABLE IF NOT EXISTS settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    nginx_host      TEXT    NOT NULL DEFAULT '',
    nginx_user      TEXT    NOT NULL DEFAULT '',
    nginx_conf_path TEXT    NOT NULL DEFAULT '',
    ssh_key_path    TEXT    NOT NULL DEFAULT '',
    alias_template  TEXT    NOT NULL DEFAULT '',
    app_name        TEXT    NOT NULL DEFAULT '',
    app_logo        TEXT    NOT NULL DEFAULT '',
    collaborators   TEXT    NOT NULL DEFAULT '[]',
    configured      INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sso_auth_flows_expires ON sso_auth_flows(expires_at);
CREATE INDEX IF NOT EXISTS idx_user_teams_team ON user_teams(team_id);
CREATE INDEX IF NOT EXISTS idx_application_teams_team
    ON application_teams(team_id);
CREATE INDEX IF NOT EXISTS idx_audit_category_id ON audit_log(category, id);
CREATE INDEX IF NOT EXISTS idx_bundle_template_mappings_template
    ON bundle_template_mappings(template_id);
"""


def connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables (idempotent) and seed the singleton settings row.

    Teams are administrator-managed; none are seeded, so a clean install starts
    with no teams (and no applications).
    """
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
        _migrate_schema(conn)
        # Seed the single settings row (id = 1) with the default alias template
        # if it does not exist yet. Never overwrites operator edits.
        conn.execute(
            "INSERT OR IGNORE INTO settings (id, alias_template) VALUES (1, ?)",
            (DEFAULT_ALIAS_TEMPLATE,),
        )


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    # ``table`` is always a code-defined constant, never user input.
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _add_column(
    conn: sqlite3.Connection, table: str, column: str, ddl: str
) -> bool:
    """Add ``column`` to ``table`` when missing. Returns True if it was added.

    ``table``, ``column``, and ``ddl`` are all fixed, code-defined strings; no
    user input is interpolated into the statement.
    """
    if _column_exists(conn, table, column):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to the current column set.

    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so columns
    introduced after a database was first created are added here. Each step is
    idempotent (guarded by ``PRAGMA table_info``) and uses defaults that satisfy
    every existing row, so re-running on an already-migrated database is a no-op.
    """
    if _add_column(conn, "users", "self_service", "INTEGER NOT NULL DEFAULT 0"):
        # Administrators always bypass approval; reflect that on first migration
        # so existing admins read as self-service in the UI.
        conn.execute("UPDATE users SET self_service = 1 WHERE role = 'admin'")

    # Users gained an apps server/port: where the user runs their applications,
    # used to render reverse-proxy aliases.
    _add_column(conn, "users", "apps_server", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "users", "apps_server_ip", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "users", "apps_port", "TEXT NOT NULL DEFAULT ''")

    # Sessions record how the user authenticated so SSO sessions can bypass
    # local-password-only first-login requirements without clearing the flag.
    _add_column(conn, "sessions", "auth_method", "TEXT NOT NULL DEFAULT 'local'")
    _add_column(conn, "sso_auth_flows", "return_to", "TEXT NOT NULL DEFAULT ''")

    # Applications gained an owner, a URL kind, and an approval state. Existing
    # rows default to an admin-curated, approved, full-URL app.
    _add_column(conn, "applications", "created_by", "INTEGER")
    _add_column(
        conn, "applications", "url_type", "TEXT NOT NULL DEFAULT 'url'"
    )
    _add_column(
        conn,
        "applications",
        "approval_status",
        "TEXT NOT NULL DEFAULT 'approved'",
    )

    # Applications record the result of the last reverse-proxy alias push.
    _add_column(conn, "applications", "last_push_status", "TEXT")
    _add_column(conn, "applications", "last_push_log", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "applications", "last_push_at", "TEXT")

    # Applications can carry their own apps server/port (set by an admin at
    # create time for alias apps); used to render the reverse-proxy alias and
    # available at delete time to remove it.
    _add_column(conn, "applications", "apps_server", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "applications", "apps_port", "TEXT NOT NULL DEFAULT ''")

    # A staged alias change awaiting approval. When a non-self-service owner
    # edits the alias, the new value is held here while the application keeps
    # serving its current alias; on approval it is applied to ``url``.
    _add_column(conn, "applications", "pending_alias", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "applications", "pending_is_active", "INTEGER")
    _add_column(conn, "applications", "needs_push", "INTEGER NOT NULL DEFAULT 0")

    # The reverse-proxy config gained an optional SSH user (ssh user@host).
    _add_column(conn, "settings", "nginx_user", "TEXT NOT NULL DEFAULT ''")

    # Configurable branding: an admin-defined application name and logo (a small
    # raster data URI), plus a one-time "configured" flag that drives the
    # first-login setup wizard.
    _add_column(conn, "settings", "app_name", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "app_logo", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "configured", "INTEGER NOT NULL DEFAULT 0")
    # Admin-managed "Collaborators" shown on the About page, stored as a JSON
    # array of names (distinct from the git-derived development team).
    _add_column(conn, "settings", "collaborators", "TEXT NOT NULL DEFAULT '[]'")

    _add_column(conn, "teams", "sort_order", "INTEGER NOT NULL DEFAULT 0")
    # Teams gained an optional small icon (a bundled catalogue path or a capped
    # raster data URI), shown on the sidebar team button.
    _add_column(conn, "teams", "icon", "TEXT NOT NULL DEFAULT ''")
