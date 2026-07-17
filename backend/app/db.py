"""SQLite access and schema bootstrap.

A new connection is opened per request (and per CLI invocation). All queries
elsewhere use parameter binding; no SQL is built from user input.
"""

from __future__ import annotations

import os
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
    ssh_private_key      TEXT    NOT NULL DEFAULT '',
    ssh_public_key       TEXT    NOT NULL DEFAULT '',
    ssh_key_generated_at TEXT,
    theme                TEXT    NOT NULL DEFAULT '',
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
    apps_protocol    TEXT    NOT NULL DEFAULT 'http',
    apps_port        TEXT    NOT NULL DEFAULT '',
    apps_path        TEXT    NOT NULL DEFAULT '',
    alias_auth_required INTEGER NOT NULL DEFAULT 1,
    is_private      INTEGER NOT NULL DEFAULT 0,
    pending_alias    TEXT    NOT NULL DEFAULT '',
    pending_is_active INTEGER,
    pending_alias_auth_required INTEGER,
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

CREATE TABLE IF NOT EXISTS application_user_shares (
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (application_id, user_id)
);

CREATE TABLE IF NOT EXISTS application_favorites (
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, application_id)
);

CREATE TABLE IF NOT EXISTS application_usage_daily (
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    usage_date     TEXT NOT NULL,
    visitor_key    TEXT NOT NULL,
    launch_count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (application_id, usage_date, visitor_key)
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

-- Registry of SSH keys usable across the app (issue_015-r1). A key is either
-- a reference to a key file on the server (kind='path') or a private key
-- stored encrypted at rest in the DB (kind='stored'). Secret material
-- (encrypted_private_key) is never returned by the API.
CREATE TABLE IF NOT EXISTS ssh_keys (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    name                  TEXT    NOT NULL UNIQUE,
    kind                  TEXT    NOT NULL CHECK (kind IN ('path', 'stored')),
    path                  TEXT    NOT NULL DEFAULT '',
    encrypted_private_key TEXT    NOT NULL DEFAULT '',
    public_key            TEXT    NOT NULL DEFAULT '',
    fingerprint           TEXT    NOT NULL DEFAULT '',
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Admin-registered Proxmox templates used to create user servers.
CREATE TABLE IF NOT EXISTS server_templates (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    vmid               INTEGER NOT NULL,
    name               TEXT    NOT NULL UNIQUE,
    kind               TEXT    NOT NULL CHECK (kind IN ('lxc', 'vm')),
    admin_ssh_key_path TEXT    NOT NULL DEFAULT '',
    is_apps_server     INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Per-user provisioned (or referenced) LXC/VM servers.
CREATE TABLE IF NOT EXISTS user_servers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name           TEXT    NOT NULL,
    hostname       TEXT    NOT NULL DEFAULT '',
    template_id    INTEGER REFERENCES server_templates(id) ON DELETE SET NULL,
    template_name  TEXT    NOT NULL DEFAULT '',
    vmid           INTEGER,
    node           TEXT    NOT NULL DEFAULT '',
    kind           TEXT    NOT NULL CHECK (kind IN ('lxc', 'vm')),
    ip_address     TEXT    NOT NULL DEFAULT '',
    cpus           INTEGER NOT NULL DEFAULT 0,
    memory_gb      INTEGER NOT NULL DEFAULT 0,
    disk_gb        INTEGER NOT NULL DEFAULT 0,
    admin_modified INTEGER NOT NULL DEFAULT 0,
    -- Optional per-server admin key path (used for key rotation on servers
    -- that have no template, e.g. imported reference servers). Never exposed
    -- through the API.
    admin_ssh_key_path TEXT NOT NULL DEFAULT '',
    -- created: provisioned by AppManager; reference: imported record of a
    -- pre-existing server; failed: creation attempt kept for its log.
    status         TEXT    NOT NULL DEFAULT 'created'
                       CHECK (status IN ('created', 'reference', 'failed')),
    last_log       TEXT    NOT NULL DEFAULT '',
    -- Deferred deletion (issue_015-r4 F1): ISO timestamp of a pending deletion
    -- request (empty = not pending), and the last destroy-failure detail
    -- (empty = none; non-empty marks an admin-recoverable failed destroy).
    deletion_requested_at TEXT NOT NULL DEFAULT '',
    deletion_error TEXT NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
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
    appmanager_proxy_host TEXT NOT NULL DEFAULT '',
    appmanager_proxy_port TEXT NOT NULL DEFAULT '',
    alias_template  TEXT    NOT NULL DEFAULT '',
    app_name        TEXT    NOT NULL DEFAULT '',
    app_logo        TEXT    NOT NULL DEFAULT '',
    collaborators   TEXT    NOT NULL DEFAULT '[]',
    default_theme   TEXT    NOT NULL DEFAULT 'dark-modern',
    configured      INTEGER NOT NULL DEFAULT 0,
    -- LXC/VM provider (Proxmox). The API key is write-only: stored here,
    -- never returned by any endpoint or written to logs/audit entries.
    provider_type            TEXT    NOT NULL DEFAULT '',
    proxmox_url              TEXT    NOT NULL DEFAULT '',
    proxmox_token_name       TEXT    NOT NULL DEFAULT '',
    proxmox_api_key          TEXT    NOT NULL DEFAULT '',
    proxmox_template_filter  TEXT    NOT NULL DEFAULT '',
    proxmox_templates_only   INTEGER NOT NULL DEFAULT 1,
    proxmox_verify_tls       INTEGER NOT NULL DEFAULT 1,
    proxmox_conn_status      TEXT    NOT NULL DEFAULT '',
    proxmox_conn_log         TEXT    NOT NULL DEFAULT '',
    -- issue_025: admin-selected Proxmox realms (JSON list of realm ids) and the
    -- optional prefix applied to auto-created user pool ids.
    proxmox_realms           TEXT    NOT NULL DEFAULT '[]',
    proxmox_pool_prefix      TEXT    NOT NULL DEFAULT '',
    -- Server-provisioning policy.
    provisioning_self_service     INTEGER NOT NULL DEFAULT 0,
    provisioning_max_servers      INTEGER NOT NULL DEFAULT 3,
    provisioning_allow_resource_edit INTEGER NOT NULL DEFAULT 0,
    provisioning_max_cpus         INTEGER NOT NULL DEFAULT 12,
    provisioning_max_memory_gb    INTEGER NOT NULL DEFAULT 24,
    provisioning_max_disk_gb      INTEGER NOT NULL DEFAULT 200,
    -- issue_025: when on, each created guest is added to its owner's Proxmox
    -- pool (created if missing). Default on.
    provisioning_add_to_pool      INTEGER NOT NULL DEFAULT 1,
    show_app_statistics           INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sso_auth_flows_expires ON sso_auth_flows(expires_at);
CREATE INDEX IF NOT EXISTS idx_user_teams_team ON user_teams(team_id);
CREATE INDEX IF NOT EXISTS idx_application_teams_team
    ON application_teams(team_id);
CREATE INDEX IF NOT EXISTS idx_application_user_shares_user
    ON application_user_shares(user_id);
CREATE INDEX IF NOT EXISTS idx_application_favorites_app
    ON application_favorites(application_id);
CREATE INDEX IF NOT EXISTS idx_application_usage_daily_date
    ON application_usage_daily(usage_date);
CREATE INDEX IF NOT EXISTS idx_audit_category_id ON audit_log(category, id);
CREATE INDEX IF NOT EXISTS idx_bundle_template_mappings_template
    ON bundle_template_mappings(template_id);
CREATE INDEX IF NOT EXISTS idx_user_servers_user ON user_servers(user_id);
"""


def connect() -> sqlite3.Connection:
    settings = get_settings()
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # Wait (rather than immediately raising SQLITE_BUSY) when another writer
    # holds the lock. WAL allows one writer + concurrent readers; the lazy
    # deletion sweep (issue_015-r4 F1) can hold a write transaction across
    # network I/O, widening the contention window, so give writers a few
    # seconds to acquire the lock instead of failing the request outright.
    conn.execute("PRAGMA busy_timeout = 5000")
    _restrict_db_permissions(settings.db_path)
    return conn


def _restrict_db_permissions(db_path: object) -> None:
    """Keep the database (and WAL/SHM companions) owner-only.

    The database stores per-user SSH private keys and session tokens. Best
    effort: permission errors are ignored so read-only or exotic filesystems
    do not break connections.
    """
    for suffix in ("", "-wal", "-shm"):
        try:
            os.chmod(f"{db_path}{suffix}", 0o600)
        except OSError:
            pass


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


def _backfill_user_ssh_keys(conn: sqlite3.Connection) -> None:
    """Generate a keypair for every user that does not have one yet.

    Private keys are stored encrypted at rest (issue_015-r1). Key material
    never leaves the database here; nothing is logged.
    """
    from . import keystore, sshkeys

    rows = conn.execute(
        "SELECT id, username FROM users WHERE ssh_public_key = ''"
    ).fetchall()
    for row in rows:
        private_key, public_key = sshkeys.generate_keypair(row["username"])
        conn.execute(
            """
            UPDATE users
            SET ssh_private_key = ?, ssh_public_key = ?,
                ssh_key_generated_at = datetime('now')
            WHERE id = ?
            """,
            (keystore.encrypt(private_key), public_key, row["id"]),
        )


def _encrypt_existing_user_keys(conn: sqlite3.Connection) -> None:
    """Encrypt any per-user private keys still stored in plaintext.

    Idempotent: rows whose ``ssh_private_key`` is already an ``enc:v1:``
    token (or empty) are skipped. Detects legacy plaintext by the OpenSSH
    PEM header.
    """
    from . import keystore

    rows = conn.execute(
        "SELECT id, ssh_private_key FROM users WHERE ssh_private_key != ''"
    ).fetchall()
    for row in rows:
        value = row["ssh_private_key"]
        if keystore.is_encrypted(value):
            continue
        conn.execute(
            "UPDATE users SET ssh_private_key = ? WHERE id = ?",
            (keystore.encrypt(value), row["id"]),
        )


def _import_key_paths_to_registry(conn: sqlite3.Connection) -> None:
    """Import already-configured SSH key file paths into the registry.

    Creates one ``kind='path'`` ssh_keys row per distinct configured path
    (reverse-proxy settings, server templates, reference user servers) and
    links the corresponding ``*_ssh_key_id`` FK. Idempotent: rows that
    already reference a registry key, and paths already imported, are skipped.
    """
    def _key_id_for_path(path: str) -> int:
        path = (path or "").strip()
        existing = conn.execute(
            "SELECT id FROM ssh_keys WHERE kind = 'path' AND path = ?", (path,)
        ).fetchone()
        if existing:
            return existing["id"]
        # Derive a unique, human-readable name from the file name.
        base = path.rsplit("/", 1)[-1] or "key"
        name = f"{base} (imported)"
        n = 2
        while conn.execute(
            "SELECT 1 FROM ssh_keys WHERE name = ?", (name,)
        ).fetchone():
            name = f"{base} (imported {n})"
            n += 1
        cur = conn.execute(
            "INSERT INTO ssh_keys (name, kind, path) VALUES (?, 'path', ?)",
            (name, path),
        )
        return int(cur.lastrowid)

    # Reverse-proxy settings key.
    row = conn.execute(
        "SELECT ssh_key_path, reverse_proxy_ssh_key_id FROM settings WHERE id = 1"
    ).fetchone()
    if row and row["ssh_key_path"] and not row["reverse_proxy_ssh_key_id"]:
        kid = _key_id_for_path(row["ssh_key_path"])
        conn.execute(
            "UPDATE settings SET reverse_proxy_ssh_key_id = ? WHERE id = 1",
            (kid,),
        )

    # Server templates.
    for r in conn.execute(
        "SELECT id, admin_ssh_key_path FROM server_templates "
        "WHERE admin_ssh_key_path != '' AND admin_ssh_key_id IS NULL"
    ).fetchall():
        kid = _key_id_for_path(r["admin_ssh_key_path"])
        conn.execute(
            "UPDATE server_templates SET admin_ssh_key_id = ? WHERE id = ?",
            (kid, r["id"]),
        )

    # Reference user servers.
    for r in conn.execute(
        "SELECT id, admin_ssh_key_path FROM user_servers "
        "WHERE admin_ssh_key_path != '' AND admin_ssh_key_id IS NULL"
    ).fetchall():
        kid = _key_id_for_path(r["admin_ssh_key_path"])
        conn.execute(
            "UPDATE user_servers SET admin_ssh_key_id = ? WHERE id = ?",
            (kid, r["id"]),
        )


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

    # Each user carries their own Ed25519 SSH keypair (issue_015). New users
    # get one at creation; the backfill below covers accounts that predate the
    # feature (and is a cheap no-op once every user has a key).
    _add_column(conn, "users", "ssh_private_key", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "users", "ssh_public_key", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "users", "ssh_key_generated_at", "TEXT")
    _backfill_user_ssh_keys(conn)
    # issue_020: per-user UI theme. Empty means "no explicit choice" -> the
    # deployment default applies.
    _add_column(conn, "users", "theme", "TEXT NOT NULL DEFAULT ''")

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
    _add_column(conn, "applications", "apps_protocol", "TEXT NOT NULL DEFAULT 'http'")
    _add_column(conn, "applications", "apps_port", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "applications", "apps_path", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        UPDATE settings
        SET alias_template = replace(
            alias_template,
            'proxy_pass http://APPS_SERVER:APPS_PORT/;',
            'proxy_pass APPS_PROTOCOL://APPS_SERVER:APPS_PORTAPPS_PATH;'
        )
        WHERE alias_template LIKE '%proxy_pass http://APPS_SERVER:APPS_PORT/%'
        """
    )
    _add_column(
        conn, "applications", "alias_auth_required", "INTEGER NOT NULL DEFAULT 1"
    )

    # A staged alias change awaiting approval. When a non-self-service owner
    # edits the alias, the new value is held here while the application keeps
    # serving its current alias; on approval it is applied to ``url``.
    _add_column(conn, "applications", "pending_alias", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "applications", "pending_is_active", "INTEGER")
    _add_column(conn, "applications", "pending_alias_auth_required", "INTEGER")
    _add_column(conn, "applications", "needs_push", "INTEGER NOT NULL DEFAULT 0")

    # The reverse-proxy config gained an optional SSH user (ssh user@host).
    _add_column(conn, "settings", "nginx_user", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "appmanager_proxy_host", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "appmanager_proxy_port", "TEXT NOT NULL DEFAULT ''")

    # Configurable branding: an admin-defined application name and logo (a small
    # raster data URI), plus a one-time "configured" flag that drives the
    # first-login setup wizard.
    _add_column(conn, "settings", "app_name", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "app_logo", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "configured", "INTEGER NOT NULL DEFAULT 0")
    # Admin-managed "Collaborators" shown on the About page, stored as a JSON
    # array of names (distinct from the git-derived development team).
    _add_column(conn, "settings", "collaborators", "TEXT NOT NULL DEFAULT '[]'")
    # issue_019: admin-selected default UI theme, applied to users who have not
    # chosen their own theme.
    _add_column(
        conn, "settings", "default_theme", "TEXT NOT NULL DEFAULT 'dark-modern'"
    )

    _add_column(conn, "teams", "sort_order", "INTEGER NOT NULL DEFAULT 0")
    # Teams gained an optional small icon (a bundled catalogue path or a capped
    # raster data URI), shown on the sidebar team button.
    _add_column(conn, "teams", "icon", "TEXT NOT NULL DEFAULT ''")

    # LXC/VM provider configuration and server-provisioning policy (issue_015).
    _add_column(conn, "settings", "provider_type", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "proxmox_url", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "proxmox_token_name", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "proxmox_api_key", "TEXT NOT NULL DEFAULT ''")
    _add_column(
        conn, "settings", "proxmox_template_filter", "TEXT NOT NULL DEFAULT ''"
    )
    _add_column(
        conn, "settings", "proxmox_templates_only", "INTEGER NOT NULL DEFAULT 1"
    )
    _add_column(conn, "settings", "proxmox_verify_tls", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "settings", "proxmox_conn_status", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "proxmox_conn_log", "TEXT NOT NULL DEFAULT ''")
    _add_column(
        conn,
        "settings",
        "provisioning_self_service",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column(
        conn, "settings", "provisioning_max_servers", "INTEGER NOT NULL DEFAULT 3"
    )
    _add_column(
        conn,
        "settings",
        "provisioning_allow_resource_edit",
        "INTEGER NOT NULL DEFAULT 0",
    )
    _add_column(
        conn, "settings", "provisioning_max_cpus", "INTEGER NOT NULL DEFAULT 12"
    )
    _add_column(
        conn,
        "settings",
        "provisioning_max_memory_gb",
        "INTEGER NOT NULL DEFAULT 24",
    )
    _add_column(
        conn, "settings", "provisioning_max_disk_gb", "INTEGER NOT NULL DEFAULT 200"
    )
    # issue_025: Proxmox realms selection, user-pool prefix, and the
    # add-to-pool policy toggle (default on).
    _add_column(conn, "settings", "proxmox_realms", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "settings", "proxmox_pool_prefix", "TEXT NOT NULL DEFAULT ''")
    _add_column(
        conn, "settings", "provisioning_add_to_pool", "INTEGER NOT NULL DEFAULT 1"
    )
    _add_column(
        conn, "settings", "show_app_statistics", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column(conn, "applications", "is_private", "INTEGER NOT NULL DEFAULT 0")

    # Reference servers imported without a template carry their own admin
    # key path for key rotation.
    _add_column(
        conn, "user_servers", "admin_ssh_key_path", "TEXT NOT NULL DEFAULT ''"
    )

    # SSH key registry (issue_015-r1): foreign keys from the settings row,
    # server templates, and user servers to a registered key. Legacy *_path
    # columns are kept as a read fallback.
    _add_column(
        conn, "settings", "reverse_proxy_ssh_key_id", "INTEGER"
    )
    _add_column(conn, "server_templates", "admin_ssh_key_id", "INTEGER")
    # Per-template provisioning options (issue_015-r2). When main_os_user is
    # set, the user's key is installed only for that OS user. enable_sudo adds
    # that user to the sudo group; enable_trusted_access sets up a full SSH
    # mesh across the user's trusted servers. Both flags default on.
    _add_column(
        conn, "server_templates", "main_os_user", "TEXT NOT NULL DEFAULT ''"
    )
    _add_column(
        conn, "server_templates", "enable_sudo", "INTEGER NOT NULL DEFAULT 1"
    )
    _add_column(
        conn,
        "server_templates",
        "enable_trusted_access",
        "INTEGER NOT NULL DEFAULT 1",
    )
    # issue_017: mark a template as a selectable "Apps server". Flagged
    # templates' names are offered in the apps-server dropdowns for user
    # creation and application management.
    _add_column(
        conn, "server_templates", "is_apps_server", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column(conn, "user_servers", "admin_ssh_key_id", "INTEGER")

    # Jump server (issue_015-r1): onboard/offboard OS accounts on a bastion.
    _add_column(conn, "settings", "jump_enabled", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "settings", "jump_host", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "jump_user", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "settings", "jump_port", "INTEGER NOT NULL DEFAULT 22")
    _add_column(conn, "settings", "jump_ssh_key_id", "INTEGER")

    # Jump server SSH-config-bundle address override (issue_015-r3). When the
    # bastion is reachable at a different address in generated user SSH configs
    # than the private address AppManager manages it over (e.g. public vs.
    # private interface), the override supplies the bundle-facing host/port.
    # Default off: bundles use the management jump_host/jump_port.
    _add_column(
        conn, "settings", "jump_bundle_override", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column(conn, "settings", "jump_bundle_host", "TEXT NOT NULL DEFAULT ''")
    _add_column(
        conn, "settings", "jump_bundle_port", "INTEGER NOT NULL DEFAULT 22"
    )

    # Jump server management/jump-user split + account model (issue_015-r3).
    #  - jump_management_user: the SSH login AppManager connects AS to manage the
    #    bastion (create accounts, install keys). Must be privileged; default
    #    root.
    #  - jump_account_mode: 'per_user' (each user gets their own hardened
    #    account, named by their derived user_id) or 'shared' (all users' keys
    #    installed into one shared hardened account). Default per_user.
    #  - jump_jumper_user: the shared account name, used only in 'shared' mode.
    _add_column(
        conn, "settings", "jump_management_user",
        "TEXT NOT NULL DEFAULT 'root'",
    )
    _add_column(
        conn, "settings", "jump_account_mode",
        "TEXT NOT NULL DEFAULT 'per_user'",
    )
    _add_column(
        conn, "settings", "jump_jumper_user", "TEXT NOT NULL DEFAULT ''"
    )
    # Preserve any pre-split configured jump user as the shared jumper account
    # so an existing 'shared'-style setup keeps working after upgrade.
    conn.execute(
        "UPDATE settings SET jump_jumper_user = jump_user "
        "WHERE id = 1 AND jump_jumper_user = '' "
        "AND jump_user IS NOT NULL AND jump_user <> ''"
    )

    # Encrypt any per-user private keys still stored in plaintext, and import
    # already-configured key file paths into the registry (idempotent).
    _encrypt_existing_user_keys(conn)
    _import_key_paths_to_registry(conn)

    # Bundle templates gained builtin/enabled flags (issue_015-r2). Builtin
    # templates render dynamically, can be cloned but not renamed/deleted, and
    # can be disabled to hide them from the account download list.
    _add_column(
        conn, "bundle_templates", "is_builtin", "INTEGER NOT NULL DEFAULT 0"
    )
    _add_column(
        conn, "bundle_templates", "enabled", "INTEGER NOT NULL DEFAULT 1"
    )

    # Bundle templates gained an optional description shown under the account
    # download dropdown (issue_015-r3).
    _add_column(
        conn, "bundle_templates", "description", "TEXT NOT NULL DEFAULT ''"
    )

    # Predefined built-in SSH-config template. Rendered dynamically from the
    # user's servers + jump server at download time (its content is a marker,
    # not used verbatim). Idempotent by the unique template name; ensure the
    # builtin flag is set even for rows seeded before this migration.
    conn.execute(
        "INSERT OR IGNORE INTO bundle_templates (name, content, is_builtin) "
        "VALUES (?, ?, 1)",
        (
            "SSH Config Default",
            "# Generated dynamically from your servers at download time.\n",
        ),
    )
    conn.execute(
        "UPDATE bundle_templates SET is_builtin = 1 WHERE name = 'SSH Config Default'"
    )
    # Give the built-in a default description if it has none yet (keeps any
    # admin-provided override intact on re-runs).
    conn.execute(
        "UPDATE bundle_templates SET description = ? "
        "WHERE name = 'SSH Config Default' AND description = ''",
        (
            "Ready-to-use SSH client config for all your servers, generated "
            "from your account at download time.",
        ),
    )

    # Deferred server deletion (issue_015-r4 F1). A deletion request enters a
    # 24h grace window during which it can be cancelled; a lazy sweep then
    # destroys the guest. deletion_requested_at holds the ISO timestamp of the
    # request (empty = not pending). deletion_error holds the last destroy
    # failure detail (empty = none); a non-empty value marks a server that
    # failed to destroy - hidden from the owner but kept in the admin list for
    # recovery. Additive columns only; the status CHECK is intentionally
    # unchanged (no table rebuild).
    _add_column(
        conn, "user_servers", "deletion_requested_at",
        "TEXT NOT NULL DEFAULT ''",
    )
    _add_column(
        conn, "user_servers", "deletion_error", "TEXT NOT NULL DEFAULT ''"
    )

    # Enforce globally-unique server names case-insensitively (issue_015-r5 F1)
    # as a backstop to the application-level pre-check. Best-effort: if legacy
    # data already contains a case-insensitive duplicate, the index cannot be
    # created; leave it and rely on the application check rather than failing
    # startup. New collisions are prevented once the index exists.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_servers_name_ci "
            "ON user_servers(lower(name))"
        )
    except sqlite3.IntegrityError:
        pass
