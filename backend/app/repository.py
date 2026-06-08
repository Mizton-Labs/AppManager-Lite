"""Data-access helpers for users, teams, and their relationships.

Every query uses bound parameters. Functions take an open connection so they
can compose inside a single transaction.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import security
from .teams import slugify


class TeamConflictError(ValueError):
    """Raised when a team name (or its derived slug) collides with another."""


def _row_to_team(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "sort_order": row["sort_order"],
        "icon": row["icon"],
    }


def _row_to_user(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    teams = list_user_teams(conn, row["id"])
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
        "self_service": bool(row["self_service"]),
        "apps_server": row["apps_server"],
        "apps_port": row["apps_port"],
        "teams": teams,
    }


def list_team_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM teams ORDER BY sort_order, id"
    ).fetchall()
    return [r["name"] for r in rows]


def list_teams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All teams as dicts ``{id, name, sort_order, icon}`` in display order."""
    rows = conn.execute(
        "SELECT id, name, sort_order, icon FROM teams ORDER BY sort_order, id"
    ).fetchall()
    return [_row_to_team(r) for r in rows]


def get_team(conn: sqlite3.Connection, team_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, sort_order, icon FROM teams WHERE id = ?",
        (team_id,),
    ).fetchone()
    return _row_to_team(row) if row is not None else None


def _team_name_conflict(
    conn: sqlite3.Connection, name: str, *, exclude_id: int | None = None
) -> bool:
    """True when ``name`` (case-insensitively) or its slug matches another team.

    The unique index already guards exact names; this additionally rejects
    case-only differences and names that would collapse to an existing team's
    URL slug (e.g. ``Red-Team`` vs ``Red Team``).
    """
    slug = slugify(name)
    rows = conn.execute(
        "SELECT id, name FROM teams WHERE id IS NOT ?",
        (exclude_id,),
    ).fetchall()
    lowered = name.strip().lower()
    for r in rows:
        if r["name"].strip().lower() == lowered or slugify(r["name"]) == slug:
            return True
    return False


def create_team(
    conn: sqlite3.Connection,
    *,
    name: str,
    icon: str = "",
    sort_order: int | None = None,
) -> dict[str, Any]:
    """Create a team, appended to the end of the sidebar order by default."""
    name = name.strip()
    if _team_name_conflict(conn, name):
        raise TeamConflictError(f"A team named '{name}' already exists.")
    if sort_order is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS next FROM teams"
        ).fetchone()
        sort_order = row["next"]
    cur = conn.execute(
        "INSERT INTO teams (name, sort_order, icon) VALUES (?, ?, ?)",
        (name, sort_order, icon),
    )
    created = get_team(conn, cur.lastrowid)
    assert created is not None  # just inserted
    return created


def update_team(
    conn: sqlite3.Connection,
    team_id: int,
    *,
    name: str | None = None,
    icon: str | None = None,
) -> dict[str, Any] | None:
    """Rename a team and/or change its icon, in place (id stable).

    Renaming by id preserves every user/application membership, which is stored
    by ``team_id``.
    """
    existing = get_team(conn, team_id)
    if existing is None:
        return None
    if name is not None:
        name = name.strip()
        if _team_name_conflict(conn, name, exclude_id=team_id):
            raise TeamConflictError(f"A team named '{name}' already exists.")
        conn.execute(
            "UPDATE teams SET name = ? WHERE id = ?", (name, team_id)
        )
    if icon is not None:
        conn.execute(
            "UPDATE teams SET icon = ? WHERE id = ?", (icon, team_id)
        )
    return get_team(conn, team_id)


def delete_team(conn: sqlite3.Connection, team_id: int) -> bool:
    """Delete a team. Membership rows cascade away. Returns True if removed."""
    cur = conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    return cur.rowcount > 0


def reorder_teams(
    conn: sqlite3.Connection, ordered_ids: list[int]
) -> list[dict[str, Any]]:
    """Set ``sort_order`` to the given id sequence (0-based).

    ``ordered_ids`` must be exactly the current set of team ids (no missing or
    unknown ids); otherwise a ``ValueError`` is raised and nothing changes.
    """
    existing = {r["id"] for r in conn.execute("SELECT id FROM teams").fetchall()}
    provided = set(ordered_ids)
    if len(ordered_ids) != len(provided):
        raise ValueError("Duplicate team ids in reorder request.")
    if provided != existing:
        raise ValueError("Reorder must list every existing team exactly once.")
    for order, team_id in enumerate(ordered_ids):
        conn.execute(
            "UPDATE teams SET sort_order = ? WHERE id = ?", (order, team_id)
        )
    return list_teams(conn)


def list_user_teams(conn: sqlite3.Connection, user_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name
        FROM user_teams ut
        JOIN teams t ON t.id = ut.team_id
        WHERE ut.user_id = ?
        ORDER BY t.sort_order, t.id
        """,
        (user_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def set_user_teams(
    conn: sqlite3.Connection, user_id: int, team_names: list[str]
) -> None:
    conn.execute("DELETE FROM user_teams WHERE user_id = ?", (user_id,))
    for name in team_names:
        team = conn.execute(
            "SELECT id FROM teams WHERE name = ?", (name,)
        ).fetchone()
        if team is None:
            raise ValueError(f"Unknown team: {name}")
        conn.execute(
            "INSERT OR IGNORE INTO user_teams (user_id, team_id) VALUES (?, ?)",
            (user_id, team["id"]),
        )


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def count_admins(conn: sqlite3.Connection, *, active_only: bool = False) -> int:
    sql = "SELECT COUNT(*) AS c FROM users WHERE role = 'admin'"
    if active_only:
        sql += " AND is_active = 1"
    return conn.execute(sql).fetchone()["c"]


def get_user_by_id(
    conn: sqlite3.Connection, user_id: int
) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(conn, row) if row else None


def get_user_by_username(
    conn: sqlite3.Connection, username: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()


def list_users(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [_row_to_user(conn, r) for r in rows]


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
    role: str,
    teams: list[str],
    must_change_password: bool,
    self_service: bool = False,
    apps_server: str = "",
    apps_port: str = "",
) -> dict[str, Any]:
    existing = get_user_by_username(conn, username)
    if existing is not None:
        raise ValueError("A user with that username already exists.")
    cur = conn.execute(
        """
        INSERT INTO users
            (username, password_hash, role, must_change_password, self_service,
             apps_server, apps_port)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            username,
            security.hash_password(password),
            role,
            int(must_change_password),
            int(self_service),
            apps_server,
            apps_port,
        ),
    )
    user_id = int(cur.lastrowid)
    set_user_teams(conn, user_id, teams)
    user = get_user_by_id(conn, user_id)
    assert user is not None
    return user


def update_user(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    role: str | None = None,
    teams: list[str] | None = None,
    is_active: bool | None = None,
    self_service: bool | None = None,
    apps_server: str | None = None,
    apps_port: str | None = None,
) -> dict[str, Any] | None:
    if get_user_by_id(conn, user_id) is None:
        return None
    if role is not None:
        conn.execute(
            "UPDATE users SET role = ?, updated_at = datetime('now') WHERE id = ?",
            (role, user_id),
        )
    if is_active is not None:
        conn.execute(
            "UPDATE users SET is_active = ?, updated_at = datetime('now') WHERE id = ?",
            (int(is_active), user_id),
        )
    if self_service is not None:
        conn.execute(
            "UPDATE users SET self_service = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (int(self_service), user_id),
        )
    if apps_server is not None:
        conn.execute(
            "UPDATE users SET apps_server = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (apps_server, user_id),
        )
    if apps_port is not None:
        conn.execute(
            "UPDATE users SET apps_port = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (apps_port, user_id),
        )
    if teams is not None:
        set_user_teams(conn, user_id, teams)
    return get_user_by_id(conn, user_id)


def set_password(
    conn: sqlite3.Connection,
    user_id: int,
    password: str,
    *,
    must_change_password: bool,
) -> None:
    conn.execute(
        """
        UPDATE users
        SET password_hash = ?, must_change_password = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (security.hash_password(password), int(must_change_password), user_id),
    )


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    # Keep applications this user created; just detach ownership so they show as
    # system-owned. (Fresh schemas also enforce this via ON DELETE SET NULL, but
    # databases migrated with ALTER TABLE have no such foreign key.)
    conn.execute(
        "UPDATE applications SET created_by = NULL WHERE created_by = ?",
        (user_id,),
    )
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def _row_to_application(
    conn: sqlite3.Connection, row: sqlite3.Row, *, include_creator: bool = False
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "url": row["url"],
        "url_type": row["url_type"],
        "icon_url": row["icon_url"],
        "is_active": bool(row["is_active"]),
        "approval_status": row["approval_status"],
        "created_by": row["created_by"],
        "sort_order": row["sort_order"],
        "apps_server": row["apps_server"],
        "apps_port": row["apps_port"],
        "pending_alias": row["pending_alias"],
        "teams": list_application_teams(conn, row["id"]),
    }
    if include_creator:
        # Only present when the query LEFT JOINs users as created_by_username.
        data["created_by_username"] = row["created_by_username"]
        # Push status/log are management-only details (mirror created_by gating).
        data["last_push_status"] = row["last_push_status"]
        data["last_push_log"] = row["last_push_log"]
        data["last_push_at"] = row["last_push_at"]
    return data


def list_application_teams(
    conn: sqlite3.Connection, application_id: int
) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name
        FROM application_teams at
        JOIN teams t ON t.id = at.team_id
        WHERE at.application_id = ?
        ORDER BY t.sort_order, t.id
        """,
        (application_id,),
    ).fetchall()
    return [r["name"] for r in rows]


def count_applications(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM applications"
    ).fetchone()["c"]


def list_all_applications(
    conn: sqlite3.Connection, *, active_only: bool = True
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM applications WHERE approval_status = 'approved'"
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY sort_order, name, id"
    rows = conn.execute(sql).fetchall()
    return [_row_to_application(conn, r) for r in rows]


def list_applications_for_team(
    conn: sqlite3.Connection, team_name: str, *, active_only: bool = True
) -> list[dict[str, Any]]:
    sql = """
        SELECT a.*
        FROM applications a
        JOIN application_teams at ON at.application_id = a.id
        JOIN teams t ON t.id = at.team_id
        WHERE t.name = ? AND a.approval_status = 'approved'
    """
    if active_only:
        sql += " AND a.is_active = 1"
    sql += " ORDER BY a.sort_order, a.name, a.id"
    rows = conn.execute(sql, (team_name,)).fetchall()
    return [_row_to_application(conn, r) for r in rows]


def list_applications_for_teams(
    conn: sqlite3.Connection, team_names: list[str], *, active_only: bool = True
) -> list[dict[str, Any]]:
    if not team_names:
        return []
    # Placeholders are derived from the list length, not its contents, so this
    # remains a fully parameter-bound query.
    placeholders = ",".join("?" for _ in team_names)
    sql = f"""
        SELECT DISTINCT a.*
        FROM applications a
        JOIN application_teams at ON at.application_id = a.id
        JOIN teams t ON t.id = at.team_id
        WHERE t.name IN ({placeholders}) AND a.approval_status = 'approved'
    """
    if active_only:
        sql += " AND a.is_active = 1"
    sql += " ORDER BY a.sort_order, a.name, a.id"
    rows = conn.execute(sql, tuple(team_names)).fetchall()
    return [_row_to_application(conn, r) for r in rows]


# Approval-aware ordering for management views: actionable (pending) first,
# then approved, then rejected; ties broken by display order.
_MANAGE_ORDER = (
    "ORDER BY CASE a.approval_status "
    "WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, "
    "a.sort_order, a.name, a.id"
)


def list_applications_for_owner(
    conn: sqlite3.Connection, user_id: int
) -> list[dict[str, Any]]:
    """All applications created by ``user_id``, regardless of approval state."""
    rows = conn.execute(
        f"""
        SELECT a.*, u.username AS created_by_username
        FROM applications a
        LEFT JOIN users u ON u.id = a.created_by
        WHERE a.created_by = ?
        {_MANAGE_ORDER}
        """,
        (user_id,),
    ).fetchall()
    return [_row_to_application(conn, r, include_creator=True) for r in rows]


def list_all_applications_admin(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Every application with its creator, for the admin management view."""
    rows = conn.execute(
        f"""
        SELECT a.*, u.username AS created_by_username
        FROM applications a
        LEFT JOIN users u ON u.id = a.created_by
        {_MANAGE_ORDER}
        """
    ).fetchall()
    return [_row_to_application(conn, r, include_creator=True) for r in rows]


def set_application_teams(
    conn: sqlite3.Connection, application_id: int, team_names: list[str]
) -> None:
    conn.execute(
        "DELETE FROM application_teams WHERE application_id = ?",
        (application_id,),
    )
    for name in team_names:
        team = conn.execute(
            "SELECT id FROM teams WHERE name = ?", (name,)
        ).fetchone()
        if team is None:
            raise ValueError(f"Unknown team: {name}")
        conn.execute(
            """
            INSERT OR IGNORE INTO application_teams (application_id, team_id)
            VALUES (?, ?)
            """,
            (application_id, team["id"]),
        )


def create_application(
    conn: sqlite3.Connection,
    *,
    name: str,
    url: str,
    url_type: str = "url",
    description: str = "",
    icon_url: str = "",
    teams: list[str],
    is_active: bool = True,
    sort_order: int = 0,
    approval_status: str = "approved",
    created_by: int | None = None,
    apps_server: str = "",
    apps_port: str = "",
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO applications
            (name, description, url, url_type, icon_url, is_active,
             approval_status, created_by, sort_order, apps_server, apps_port)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            description,
            url,
            url_type,
            icon_url,
            int(is_active),
            approval_status,
            created_by,
            sort_order,
            apps_server,
            apps_port,
        ),
    )
    application_id = int(cur.lastrowid)
    set_application_teams(conn, application_id, teams)
    row = conn.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    assert row is not None
    return _row_to_application(conn, row)


def get_application(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    include_creator: bool = False,
) -> dict[str, Any] | None:
    if include_creator:
        row = conn.execute(
            """
            SELECT a.*, u.username AS created_by_username
            FROM applications a
            LEFT JOIN users u ON u.id = a.created_by
            WHERE a.id = ?
            """,
            (application_id,),
        ).fetchone()
        return (
            _row_to_application(conn, row, include_creator=True) if row else None
        )
    row = conn.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()
    return _row_to_application(conn, row) if row else None


def update_application(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    name: str | None = None,
    url: str | None = None,
    url_type: str | None = None,
    description: str | None = None,
    icon_url: str | None = None,
    is_active: bool | None = None,
    approval_status: str | None = None,
    sort_order: int | None = None,
    teams: list[str] | None = None,
    apps_server: str | None = None,
    apps_port: str | None = None,
    pending_alias: str | None = None,
) -> dict[str, Any] | None:
    if get_application(conn, application_id) is None:
        return None
    # Build the column updates dynamically from a fixed, code-defined map so
    # column names are never derived from user input.
    columns: dict[str, Any] = {}
    if name is not None:
        columns["name"] = name
    if url is not None:
        columns["url"] = url
    if url_type is not None:
        columns["url_type"] = url_type
    if description is not None:
        columns["description"] = description
    if icon_url is not None:
        columns["icon_url"] = icon_url
    if is_active is not None:
        columns["is_active"] = int(is_active)
    if approval_status is not None:
        columns["approval_status"] = approval_status
    if sort_order is not None:
        columns["sort_order"] = sort_order
    if apps_server is not None:
        columns["apps_server"] = apps_server
    if apps_port is not None:
        columns["apps_port"] = apps_port
    if pending_alias is not None:
        columns["pending_alias"] = pending_alias
    if columns:
        assignments = ", ".join(f"{col} = ?" for col in columns)
        params = list(columns.values()) + [application_id]
        conn.execute(
            f"UPDATE applications SET {assignments}, "
            "updated_at = datetime('now') WHERE id = ?",
            params,
        )
    if teams is not None:
        set_application_teams(conn, application_id, teams)
    return get_application(conn, application_id)


def delete_application(conn: sqlite3.Connection, application_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM applications WHERE id = ?", (application_id,)
    )
    return cur.rowcount > 0


def set_application_push_result(
    conn: sqlite3.Connection,
    application_id: int,
    *,
    status: str,
    log: str,
) -> None:
    """Record the result of the last reverse-proxy alias push for an app."""
    conn.execute(
        "UPDATE applications SET last_push_status = ?, last_push_log = ?, "
        "last_push_at = datetime('now') WHERE id = ?",
        (status, log, application_id),
    )


# --- Audit log -------------------------------------------------------------


def insert_audit_event(
    conn: sqlite3.Connection,
    *,
    category: str,
    action: str,
    actor_id: int | None = None,
    actor_username: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    target_name: str | None = None,
    detail: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log
            (category, action, actor_id, actor_username,
             target_type, target_id, target_name, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category,
            action,
            actor_id,
            actor_username,
            target_type,
            target_id,
            target_name,
            detail,
        ),
    )


def list_audit_events(
    conn: sqlite3.Connection,
    *,
    category: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return the most recent audit events (newest first), optionally filtered."""
    if category is not None:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE category = ? ORDER BY id DESC LIMIT ?",
            (category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# --- Settings (reverse-proxy configuration) --------------------------------


def get_settings_row(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the single settings row (id = 1), or an empty default shape."""
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    if row is None:
        return {
            "nginx_host": "",
            "nginx_user": "",
            "nginx_conf_path": "",
            "ssh_key_path": "",
            "alias_template": "",
            "app_name": "",
            "app_logo": "",
            "configured": 0,
        }
    return dict(row)


def update_settings_row(
    conn: sqlite3.Connection,
    *,
    nginx_host: str | None = None,
    nginx_user: str | None = None,
    nginx_conf_path: str | None = None,
    ssh_key_path: str | None = None,
    alias_template: str | None = None,
    app_name: str | None = None,
    app_logo: str | None = None,
    configured: bool | None = None,
) -> dict[str, Any]:
    # Ensure the single row exists before updating.
    conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    columns: dict[str, Any] = {}
    if nginx_host is not None:
        columns["nginx_host"] = nginx_host
    if nginx_user is not None:
        columns["nginx_user"] = nginx_user
    if nginx_conf_path is not None:
        columns["nginx_conf_path"] = nginx_conf_path
    if ssh_key_path is not None:
        columns["ssh_key_path"] = ssh_key_path
    if alias_template is not None:
        columns["alias_template"] = alias_template
    if app_name is not None:
        columns["app_name"] = app_name
    if app_logo is not None:
        columns["app_logo"] = app_logo
    if configured is not None:
        columns["configured"] = int(configured)
    if columns:
        assignments = ", ".join(f"{col} = ?" for col in columns)
        params = [*columns.values(), 1]
        conn.execute(
            f"UPDATE settings SET {assignments}, updated_at = datetime('now') "
            "WHERE id = ?",
            params,
        )
    return get_settings_row(conn)
