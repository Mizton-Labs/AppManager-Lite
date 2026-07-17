"""Data-access helpers for users, teams, and their relationships.

Every query uses bound parameters. Functions take an open connection so they
can compose inside a single transaction.
"""

from __future__ import annotations

import json
import re
import secrets
import shlex
import sqlite3
from typing import Any

from . import keystore, security, sshkeys
from .schemas import (
    BUNDLE_MAPPING_SOURCES,
    is_server_var_source,
    server_var_source_slug,
)
from .teams import slugify

__all__ = ["BUNDLE_MAPPING_SOURCES"]


def _encrypt_private_key(value: str) -> str:
    """Encrypt SSH private-key material for at-rest storage."""
    return keystore.encrypt(value)


class TeamConflictError(ValueError):
    """Raised when a team name (or its derived slug) collides with another."""


# Single source of truth for bundle mapping sources lives in schemas (the
# API-validation layer); re-exported here so the renderer and the validator
# can never desync. (Imported at module top.)


def _row_to_team(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "sort_order": row["sort_order"],
        "icon": row["icon"],
    }


_USER_ID_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def derive_user_id(username: str) -> str:
    """Human-facing user identifier derived from the sign-in name.

    The local part of the email (or the whole username when it is not an
    email), lowercased, with dots and underscores replaced by dashes and any
    other character outside ``[a-z0-9-]`` dropped. The result later names
    per-user resources (server hostnames, config entries), so it is
    restricted to a safe character set here rather than at each use site.
    """
    local_part = username.split("@", 1)[0].lower()
    mapped = local_part.replace(".", "-").replace("_", "-")
    return "".join(ch for ch in mapped if ch in _USER_ID_ALLOWED).strip("-")


def user_id_conflict(
    conn: sqlite3.Connection, user_id: str, *, exclude_id: int | None = None
) -> bool:
    """True when another user's derived identifier equals ``user_id``.

    Derived identifiers are not stored, so this scans usernames; user counts
    are small (an admin-managed portal), which keeps this trivial.
    """
    rows = conn.execute(
        "SELECT username FROM users WHERE id IS NOT ?", (exclude_id,)
    ).fetchall()
    return any(derive_user_id(r["username"]) == user_id for r in rows)


def _row_to_user(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    teams = list_user_teams(conn, row["id"])
    return {
        "id": row["id"],
        "username": row["username"],
        "user_id": derive_user_id(row["username"]),
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "must_change_password": bool(row["must_change_password"]),
        "self_service": bool(row["self_service"]),
        "apps_server": row["apps_server"],
        "apps_server_ip": row["apps_server_ip"],
        "apps_port": row["apps_port"],
        "theme": row["theme"],
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


def get_user_by_unique_email_local_part(
    conn: sqlite3.Connection, local_part: str
) -> sqlite3.Row | None:
    """Resolve a unique email username by its local part.

    Stored usernames are now email addresses. This compatibility lookup keeps
    existing short-name login flows working only when the local part is unique.
    """
    if "@" in local_part:
        return None
    rows = conn.execute(
        "SELECT * FROM users WHERE username LIKE ?",
        (f"{local_part}@%",),
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


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
    apps_server_ip: str = "",
    apps_port: str = "",
) -> dict[str, Any]:
    existing = get_user_by_username(conn, username)
    if existing is not None:
        raise ValueError("A user with that username already exists.")
    user_id = derive_user_id(username)
    if not user_id:
        raise ValueError(
            "The email address must yield a usable identifier "
            "(letters, digits, or dashes before the @)."
        )
    if user_id_conflict(conn, user_id):
        raise ValueError(
            f"A user with the derived identifier '{user_id}' already exists; "
            "choose an email address with a different local part."
        )
    private_key, public_key = sshkeys.generate_keypair(username)
    cur = conn.execute(
        """
        INSERT INTO users
            (username, password_hash, role, must_change_password, self_service,
             apps_server, apps_server_ip, apps_port,
             ssh_private_key, ssh_public_key, ssh_key_generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            username,
            security.hash_password(password),
            role,
            int(must_change_password),
            int(self_service),
            apps_server,
            apps_server_ip,
            apps_port,
            _encrypt_private_key(private_key),
            public_key,
        ),
    )
    user_id = int(cur.lastrowid)
    set_user_teams(conn, user_id, teams)
    user = get_user_by_id(conn, user_id)
    assert user is not None
    return user


def create_sso_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    role: str = "user",
) -> dict[str, Any]:
    """Create a locally linked SSO user with an unshared random password."""
    return create_user(
        conn,
        username=username,
        password=secrets.token_urlsafe(48),
        role=role,
        teams=[],
        must_change_password=False,
    )


def set_user_theme(
    conn: sqlite3.Connection, user_id: int, theme: str
) -> dict[str, Any] | None:
    """Persist a user's chosen UI theme (issue_020). Empty clears the choice."""
    if get_user_by_id(conn, user_id) is None:
        return None
    conn.execute(
        "UPDATE users SET theme = ?, updated_at = datetime('now') WHERE id = ?",
        (theme, user_id),
    )
    return get_user_by_id(conn, user_id)


def update_user(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    role: str | None = None,
    teams: list[str] | None = None,
    is_active: bool | None = None,
    self_service: bool | None = None,
    apps_server: str | None = None,
    apps_server_ip: str | None = None,
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
    if apps_server_ip is not None:
        conn.execute(
            "UPDATE users SET apps_server_ip = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (apps_server_ip, user_id),
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


def get_user_ssh_key(
    conn: sqlite3.Connection, user_id: int
) -> dict[str, Any] | None:
    """The user's SSH keypair, or ``None`` when the user (or key) is missing.

    Key material is intentionally excluded from ``_row_to_user`` so it never
    flows through generic user listings, logs, or audit entries; callers must
    use this accessor and expose the private key only to the owning user.
    """
    row = conn.execute(
        "SELECT ssh_private_key, ssh_public_key, ssh_key_generated_at "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None or not row["ssh_public_key"]:
        return None
    # Private keys are stored encrypted at rest; decrypt for the owner-gated
    # caller. Legacy plaintext rows pass through unchanged.
    return {
        "private_key": keystore.decrypt(row["ssh_private_key"]),
        "public_key": row["ssh_public_key"],
        "generated_at": row["ssh_key_generated_at"],
    }


def set_user_ssh_key(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    private_key: str,
    public_key: str,
) -> None:
    conn.execute(
        """
        UPDATE users
        SET ssh_private_key = ?, ssh_public_key = ?,
            ssh_key_generated_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (keystore.encrypt(private_key), public_key, user_id),
    )


def delete_user(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    delete_apps: bool = False,
    transfer_to_user_id: int | None = None,
) -> bool:
    # Analytics visitor keys deliberately retain no FK, so remove the user's
    # activity explicitly before the numeric ID can ever be reused.
    conn.execute("DELETE FROM application_usage_daily WHERE visitor_key = ?", (f"user:{user_id}",))
    if delete_apps:
        conn.execute("DELETE FROM applications WHERE created_by = ?", (user_id,))
    else:
        conn.execute(
            "UPDATE applications SET created_by = ? WHERE created_by = ?",
            (transfer_to_user_id, user_id),
        )
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Bundle templates
# ---------------------------------------------------------------------------


def _bundle_mappings(conn: sqlite3.Connection, template_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT field_name, source
        FROM bundle_template_mappings
        WHERE template_id = ?
        ORDER BY id
        """,
        (template_id,),
    ).fetchall()
    return [{"field_name": r["field_name"], "source": r["source"]} for r in rows]


def _row_to_bundle_template(
    conn: sqlite3.Connection, row: sqlite3.Row
) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "content": row["content"],
        "description": row["description"],
        "mappings": _bundle_mappings(conn, row["id"]),
        "is_builtin": bool(row["is_builtin"]),
        "enabled": bool(row["enabled"]),
    }


def _validate_bundle_mappings(
    conn: sqlite3.Connection,
    mappings: list[dict[str, str]],
    *,
    allow_sources: set[str] | None = None,
) -> None:
    seen: set[str] = set()
    # Slugs of the server templates that currently exist; server-var sources
    # must name one of these at save time (they may still go stale later, which
    # renders as empty rather than failing the download).
    template_slugs = {slugify(t["name"]) for t in list_server_templates(conn)}
    # Sources already stored on this template are grandfathered so editing an
    # unrelated field does not fail when a referenced template was since
    # deleted (stale sources render empty at download).
    grandfathered = allow_sources or set()
    for mapping in mappings:
        field_name = mapping["field_name"].strip()
        source = mapping["source"].strip()
        if not field_name:
            raise ValueError("Bundle mapping field name must not be empty.")
        if field_name in seen:
            raise ValueError(f"Duplicate bundle mapping field: {field_name}")
        if source in BUNDLE_MAPPING_SOURCES or source in grandfathered:
            pass
        elif is_server_var_source(source):
            slug = server_var_source_slug(source)
            if slug not in template_slugs:
                raise ValueError(
                    f"Unknown server template for mapping source: {source}"
                )
        else:
            raise ValueError(f"Unknown bundle mapping source: {source}")
        seen.add(field_name)


def _replace_bundle_mappings(
    conn: sqlite3.Connection,
    template_id: int,
    mappings: list[dict[str, str]],
    *,
    allow_sources: set[str] | None = None,
) -> None:
    _validate_bundle_mappings(conn, mappings, allow_sources=allow_sources)
    conn.execute(
        "DELETE FROM bundle_template_mappings WHERE template_id = ?", (template_id,)
    )
    for mapping in mappings:
        conn.execute(
            """
            INSERT INTO bundle_template_mappings (template_id, field_name, source)
            VALUES (?, ?, ?)
            """,
            (template_id, mapping["field_name"].strip(), mapping["source"].strip()),
        )


def list_bundle_templates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM bundle_templates ORDER BY lower(name), id"
    ).fetchall()
    return [_row_to_bundle_template(conn, r) for r in rows]


def get_bundle_template(
    conn: sqlite3.Connection, template_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM bundle_templates WHERE id = ?", (template_id,)
    ).fetchone()
    return _row_to_bundle_template(conn, row) if row is not None else None


def create_bundle_template(
    conn: sqlite3.Connection,
    *,
    name: str,
    content: str,
    mappings: list[dict[str, str]],
    description: str = "",
) -> dict[str, Any]:
    cur = conn.execute(
        "INSERT INTO bundle_templates (name, content, description) "
        "VALUES (?, ?, ?)",
        (name.strip(), content, description.strip()),
    )
    template_id = int(cur.lastrowid)
    _replace_bundle_mappings(conn, template_id, mappings)
    created = get_bundle_template(conn, template_id)
    assert created is not None
    return created


def update_bundle_template(
    conn: sqlite3.Connection,
    template_id: int,
    *,
    name: str | None = None,
    content: str | None = None,
    description: str | None = None,
    mappings: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    existing = get_bundle_template(conn, template_id)
    if existing is None:
        return None
    columns: dict[str, Any] = {}
    if name is not None:
        columns["name"] = name.strip()
    if content is not None:
        columns["content"] = content
    if description is not None:
        columns["description"] = description.strip()
    if columns:
        assignments = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(
            f"UPDATE bundle_templates SET {assignments}, updated_at = datetime('now') "
            "WHERE id = ?",
            [*columns.values(), template_id],
        )
    if mappings is not None:
        # Grandfather sources already stored on this template so an edit that
        # keeps a since-deleted template's source does not fail validation.
        allow = {m["source"] for m in existing["mappings"]}
        _replace_bundle_mappings(
            conn, template_id, mappings, allow_sources=allow
        )
    return get_bundle_template(conn, template_id)


def delete_bundle_template(conn: sqlite3.Connection, template_id: int) -> bool:
    cur = conn.execute("DELETE FROM bundle_templates WHERE id = ?", (template_id,))
    return cur.rowcount > 0


def _server_var_user(server: dict[str, Any], user_id: str) -> str:
    """OS user for a server variable: the template main user, else user_id.

    The server dict may carry the template's ``main_os_user`` (annotated by the
    download path); fall back to the account's derived user id.
    """
    return (server.get("main_os_user") or "").strip() or user_id


def bundle_mapping_values(
    user: dict[str, Any],
    user_servers: list[dict[str, Any]] | None = None,
    server_templates: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """All mapping-source -> value pairs for a user (static + per-template).

    Server-template-scoped variables (``server_<slug>_{name,ip,user}``) resolve
    to the user's FIRST usable server created from that template. Templates with
    no usable server for the user render as empty strings.
    """
    user_id = user.get("user_id", "") or derive_user_id(
        user.get("username", "") or ""
    )
    values: dict[str, str] = {
        "username": user.get("username", "") or "",
        "user_id": user_id,
        "user_apps_server": user.get("apps_server", "")
        or user.get("apps_server_ip", "")
        or "",
        "user_apps_server_host": user.get("apps_server", "") or "",
        "user_apps_server_ip": user.get("apps_server_ip", "") or "",
        "user_role": user.get("role", "") or "",
    }
    usable = [
        s
        for s in (user_servers or [])
        if s.get("status") != "failed" and s.get("ip_address")
    ]
    # One set of variables per server template slug; resolved to the user's
    # first usable server of that template.
    for template in server_templates or []:
        slug = slugify(template.get("name", ""))
        if not slug:
            continue
        first = next(
            (s for s in usable if slugify(s.get("template_name", "")) == slug),
            None,
        )
        if first is not None:
            raw = first.get("hostname") or first.get("name", "server")
            name = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-") or "server"
            values[f"server_{slug}_name"] = name
            values[f"server_{slug}_ip"] = first.get("ip_address", "")
            values[f"server_{slug}_user"] = _server_var_user(first, user_id)
        else:
            values[f"server_{slug}_name"] = ""
            values[f"server_{slug}_ip"] = ""
            values[f"server_{slug}_user"] = ""
    return values


def render_bundle_template(
    template: dict[str, Any],
    user: dict[str, Any],
    user_servers: list[dict[str, Any]] | None = None,
    server_templates: list[dict[str, Any]] | None = None,
) -> str:
    values = bundle_mapping_values(user, user_servers, server_templates)
    rendered = str(template["content"])
    for mapping in template["mappings"]:
        # Stale/deleted-template sources are absent from ``values`` and render
        # as empty rather than raising.
        rendered = rendered.replace(
            mapping["field_name"], values.get(mapping["source"], "")
        )
    return rendered


def appmanager_key_name(app_name: str) -> str:
    """Filename for a user's downloaded SSH key, tagged with the app name.

    Produces ``id_ed25519_appmanager_<slug>`` where ``<slug>`` is the configured
    application name lowercased with runs of non-alphanumerics collapsed to
    dashes (e.g. "Morris application X" -> "morris-application-x"). When the app
    name is blank the suffix is just ``id_ed25519_appmanager``. The same name is
    referenced by the generated SSH config's ``IdentityFile`` lines so the
    downloaded key and the config agree.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (app_name or "").lower()).strip("-")
    return f"id_ed25519_appmanager_{slug}" if slug else "id_ed25519_appmanager"


def _server_host_alias(server: dict[str, Any]) -> str:
    """The SSH ``Host`` alias for a server (slug of hostname or name)."""
    raw = server.get("hostname") or server.get("name", "server")
    return re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-") or "server"


def render_generic_ssh_config(
    user: dict[str, Any],
    user_servers: list[dict[str, Any]],
    key_name: str = "id_ed25519",
) -> str:
    """A generic SSH config built from the user's servers.

    Used when a bundle template has no field mappings: one ``Host`` block per
    server that has an IP address, keyed by the server's hostname (falling
    back to a slug of its name).
    """
    user_id = user.get("user_id", "") or derive_user_id(
        user.get("username", "") or ""
    )
    blocks = []
    for server in user_servers:
        if server.get("status") == "failed" or not server.get("ip_address"):
            continue
        # Server display names may contain spaces; SSH Host patterns must not.
        host = _server_host_alias(server)
        blocks.append(
            f"Host {host}\n"
            f"    HostName {server['ip_address']}\n"
            + (f"    User {user_id}\n" if user_id else "")
            + f"    IdentityFile ~/.ssh/{key_name}\n"
        )
    if not blocks:
        return (
            "# No servers with an IP address are registered for this "
            "account yet.\n"
        )
    return "\n".join(blocks)


def render_builtin_ssh_config(
    user: dict[str, Any],
    user_servers: list[dict[str, Any]],
    jump: dict[str, Any] | None = None,
    key_name: str = "id_ed25519",
) -> str:
    """Render the built-in SSH config (issue_015-r2).

    Produces a ``Host *`` keepalive stanza, an optional ``Host JUMPSERVER``
    block when a jump server is enabled, and one ``Host`` block per usable
    server (with ``ProxyJump`` when the jump server is enabled).
    """
    user_id = user.get("user_id", "") or derive_user_id(
        user.get("username", "") or ""
    )
    parts = [
        "Host *\n"
        "    ServerAliveInterval 60\n"
        "    ServerAliveCountMax 3\n"
        "    TCPKeepAlive yes\n"
    ]
    jump_enabled = bool(jump and jump.get("enabled") and jump.get("host"))
    if jump_enabled:
        # The bundle address may differ from the management address (e.g. the
        # bastion has separate public/private interfaces). When the override is
        # set and supplies a host, emit that; otherwise fall back to the
        # management host/port. User/IdentityFile are unaffected.
        if jump.get("bundle_override") and jump.get("bundle_host"):
            bundle_host = jump["bundle_host"]
            bundle_port = int(jump.get("bundle_port", 22) or 22)
        else:
            bundle_host = jump["host"]
            bundle_port = int(jump.get("port", 22) or 22)
        parts.append(
            "Host jumpserver\n"
            f"    Hostname {bundle_host}\n"
            f"    User {jump.get('jump_user', '') or user_id}\n"
            f"    Port {bundle_port}\n"
            f"    IdentityFile ~/.ssh/{key_name}\n"
        )
    for server in user_servers:
        if server.get("status") == "failed" or not server.get("ip_address"):
            continue
        host = _server_host_alias(server)
        srv_user = _server_var_user(server, user_id)
        block = (
            f"Host {host}\n"
            f"    Hostname {server['ip_address']}\n"
            + (f"    User {srv_user}\n" if srv_user else "")
            + ("    ProxyJump jumpserver\n" if jump_enabled else "")
            + f"    IdentityFile ~/.ssh/{key_name}\n"
        )
        parts.append(block)
    return "\n".join(parts)


def render_connect_scripts(
    user: dict[str, Any],
    user_servers: list[dict[str, Any]],
    *,
    jump: dict[str, Any] | None = None,
    key_name: str = "id_ed25519",
    self_contained: bool = False,
) -> list[tuple[str, str]]:
    """Per-server ``connect_server_<name>.sh`` scripts for the bundle zip.

    One script per usable server (not failed, has an IP). Returns
    ``(filename, script_text)`` pairs.

    The scripts are **self-contained** and run in place from the unzipped
    directory: each ``cd``s into its own directory and invokes ``ssh`` with an
    explicit ``-i "$(dirname "$0")/<key>"`` (the private key shipped beside the
    script) targeting the server's IP directly. When a jump server is enabled
    the bastion hop is reached with an explicit ``ProxyCommand`` that passes the
    *same* bundled key, so the whole chain works without depending on the
    bundled ``config`` or the user's ``~/.ssh`` (the user may still drop the
    files into ``~/.ssh`` and use the ``config`` manually if they prefer).

    ``self_contained`` is accepted for API compatibility; both modes now emit
    a self-contained script (mapping-template ``config`` text may not even be a
    valid ssh config, which is why deferring to it was never safe).

    All interpolated values are shell-quoted; server display names are slugged
    into the filename so no path separators can appear.
    """
    user_id = user.get("user_id", "") or derive_user_id(
        user.get("username", "") or ""
    )
    jump_enabled = bool(jump and jump.get("enabled") and jump.get("host"))
    jump_user = jump_host = ""
    jump_port = 22
    if jump_enabled and jump is not None:
        if jump.get("bundle_override") and jump.get("bundle_host"):
            jump_host = jump["bundle_host"]
            jump_port = int(jump.get("bundle_port", 22) or 22)
        else:
            jump_host = jump["host"]
            jump_port = int(jump.get("port", 22) or 22)
        jump_user = jump.get("jump_user", "") or user_id
    scripts: list[tuple[str, str]] = []
    seen: set[str] = set()
    for server in user_servers:
        if server.get("status") == "failed" or not server.get("ip_address"):
            continue
        alias = _server_host_alias(server)
        filename = f"connect_server_{alias}.sh"
        # Guard against two servers slugging to the same alias.
        suffix = 2
        while filename in seen:
            filename = f"connect_server_{alias}-{suffix}.sh"
            suffix += 1
        seen.add(filename)

        header = (
            "#!/bin/sh\n"
            f"# Connect to {server.get('name', alias).replace(chr(10), ' ').replace(chr(13), ' ')} "
            "(generated by the SSH bundle).\n"
            # Windows-backed WSL mounts can discard Unix modes during
            # extraction. When started with `sh script`, restore +x for later
            # direct runs where the filesystem supports it (otherwise harmless).
            'chmod +x "$0" 2>/dev/null || true\n'
        )
        srv_user = _server_var_user(server, user_id)
        target = (
            f"{srv_user}@{server['ip_address']}"
            if srv_user
            else str(server["ip_address"])
        )
        # The script cd's into its own directory first, so the private key
        # shipped beside it is reachable as ./<key>. Using a CWD-relative path
        # (rather than $(dirname "$0")) is what makes the jump hop work too: ssh
        # runs a ProxyCommand in the same working directory, so ./<key> resolves
        # there as well -- unlike $0, which inside a ProxyCommand is the proxy
        # shell, not this script. key_name is a strict slug ([a-z0-9_-]).
        setup = (
            'cd "$(dirname "$0")" || exit 1\n'
            f'chmod 600 "./{key_name}" 2>/dev/null || true\n'
        )
        key_path = f"./{key_name}"
        parts = ["ssh", "-i", key_path, "-o", "IdentitiesOnly=yes"]
        if jump_enabled:
            # Reach the bastion with the SAME bundled key via an explicit
            # ProxyCommand (a plain ProxyJump would make ssh look for the
            # bastion key in ~/.ssh). %h/%p stay literal for ssh to substitute
            # at connect time; jump_user@jump_host is shell-quoted so an exotic
            # admin-configured value cannot break out.
            proxy = (
                f"ssh -i {key_path} -o IdentitiesOnly=yes "
                f"-W %h:%p -p {jump_port} "
                + shlex.quote(f"{jump_user}@{jump_host}")
            )
            parts += ["-o", "ProxyCommand=" + shlex.quote(proxy)]
        parts.append(shlex.quote(target))
        body = setup + "exec " + " ".join(parts) + ' "$@"\n'
        scripts.append((filename, header + body))
    return scripts


def set_bundle_template_enabled(
    conn: sqlite3.Connection, template_id: int, enabled: bool
) -> dict[str, Any] | None:
    if get_bundle_template(conn, template_id) is None:
        return None
    conn.execute(
        "UPDATE bundle_templates SET enabled = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (int(enabled), template_id),
    )
    return get_bundle_template(conn, template_id)


def clone_bundle_template(
    conn: sqlite3.Connection, template_id: int, *, new_name: str
) -> dict[str, Any] | None:
    """Clone a template into a new editable (non-builtin) template."""
    source = get_bundle_template(conn, template_id)
    if source is None:
        return None
    return create_bundle_template(
        conn,
        name=new_name,
        content=source["content"],
        description=source.get("description", ""),
        mappings=[
            {"field_name": m["field_name"], "source": m["source"]}
            for m in source["mappings"]
        ],
    )


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def _row_to_application(
    conn: sqlite3.Connection, row: sqlite3.Row, *, include_creator: bool = False
) -> dict[str, Any]:
    publisher_teams = list_user_teams(conn, row["created_by"]) if row["created_by"] else []
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
        "apps_protocol": row["apps_protocol"],
        "apps_port": row["apps_port"],
        "apps_path": row["apps_path"],
        "alias_auth_required": bool(row["alias_auth_required"]),
        "apps_rewrite_root": bool(row["apps_rewrite_root"]),
        "is_private": bool(row["is_private"]),
        "pending_alias": row["pending_alias"],
        "pending_is_active": (
            None if row["pending_is_active"] is None else bool(row["pending_is_active"])
        ),
        "pending_alias_auth_required": (
            None
            if row["pending_alias_auth_required"] is None
            else bool(row["pending_alias_auth_required"])
        ),
        "pending_apps_rewrite_root": (
            None
            if row["pending_apps_rewrite_root"] is None
            else bool(row["pending_apps_rewrite_root"])
        ),
        "needs_push": bool(row["needs_push"]),
        "publisher_team": publisher_teams[0] if publisher_teams else "",
        "teams": list_application_teams(conn, row["id"]),
        "shared_user_ids": list_application_user_ids(conn, row["id"]),
    }
    if "created_by_username" in row.keys():
        data["created_by_username"] = row["created_by_username"]
    if include_creator:
        # Push status/log are management-only details (mirror created_by gating).
        data["last_push_status"] = row["last_push_status"]
        data["last_push_log"] = row["last_push_log"]
        data["last_push_at"] = row["last_push_at"]
        data["shared_users"] = list_application_shared_users(conn, row["id"])
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


def list_application_user_ids(conn: sqlite3.Connection, application_id: int) -> list[int]:
    return [row["user_id"] for row in conn.execute(
        "SELECT user_id FROM application_user_shares WHERE application_id = ? ORDER BY user_id",
        (application_id,),
    )]


def list_application_shared_users(conn: sqlite3.Connection, application_id: int) -> list[dict[str, Any]]:
    return [
        {"id": row["id"], "username": row["username"], "user_id": derive_user_id(row["username"])}
        for row in conn.execute(
            "SELECT u.id, u.username FROM application_user_shares s "
            "JOIN users u ON u.id = s.user_id WHERE s.application_id = ? ORDER BY u.username COLLATE NOCASE",
            (application_id,),
        )
    ]


def set_application_users(conn: sqlite3.Connection, application_id: int, user_ids: list[int]) -> None:
    conn.execute("DELETE FROM application_user_shares WHERE application_id = ?", (application_id,))
    for user_id in dict.fromkeys(user_ids):
        conn.execute(
            "INSERT INTO application_user_shares (application_id, user_id) VALUES (?, ?)",
            (application_id, user_id),
        )


def can_access_application(conn: sqlite3.Connection, app: dict[str, Any], user: dict[str, Any] | None) -> bool:
    if not app["is_active"] or app["approval_status"] != "approved":
        return False
    if user is None:
        return not app["is_private"] and not app["alias_auth_required"]
    if user.get("role") == "admin" or (user.get("id") and app.get("created_by") == user["id"]):
        return True
    if app["is_private"] or not user.get("id"):
        return False
    if user["id"] in app.get("shared_user_ids", []):
        return True
    return bool(set(app.get("teams", [])) & set(user.get("teams", [])))


def list_visible_applications(
    conn: sqlite3.Connection, user: dict[str, Any], *, active_only: bool = True
) -> list[dict[str, Any]]:
    apps = list_all_applications(conn, active_only=active_only)
    return [app for app in apps if can_access_application(conn, app, user)]


def count_applications(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM applications"
    ).fetchone()["c"]


def set_application_favorite(conn: sqlite3.Connection, user_id: int, application_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO application_favorites (user_id, application_id) VALUES (?, ?)",
        (user_id, application_id),
    )


def remove_application_favorite(conn: sqlite3.Connection, user_id: int, application_id: int) -> None:
    conn.execute(
        "DELETE FROM application_favorites WHERE user_id = ? AND application_id = ?",
        (user_id, application_id),
    )


def record_application_launch(conn: sqlite3.Connection, application_id: int, visitor_key: str) -> None:
    conn.execute(
        "INSERT INTO application_usage_daily (application_id, usage_date, visitor_key, launch_count) "
        "VALUES (?, date('now'), ?, 1) "
        "ON CONFLICT(application_id, usage_date, visitor_key) "
        "DO UPDATE SET launch_count = launch_count + 1",
        (application_id, visitor_key),
    )
    # Daily rows retain user-linked uniqueness only long enough for the
    # dashboard's supported 90-day range; this avoids indefinite activity
    # history in the operational database.
    conn.execute(
        "DELETE FROM application_usage_daily WHERE usage_date < date('now', '-90 days')"
    )


def application_card_statistics(
    conn: sqlite3.Connection, application_ids: list[int], user_id: int | None
) -> tuple[set[int], dict[int, int]]:
    if not application_ids:
        return set(), {}
    marks = ",".join("?" for _ in application_ids)
    favorites: set[int] = set()
    if user_id:
        favorites = {
            row["application_id"] for row in conn.execute(
                f"SELECT application_id FROM application_favorites "
                f"WHERE user_id = ? AND application_id IN ({marks})",
                (user_id, *application_ids),
            )
        }
    visits = {
        row["application_id"]: row["launches"] for row in conn.execute(
            f"SELECT application_id, SUM(launch_count) AS launches "
            f"FROM application_usage_daily WHERE usage_date >= date('now', '-6 days') "
            f"AND application_id IN ({marks}) GROUP BY application_id",
            application_ids,
        )
    }
    return favorites, visits


def list_all_applications(
    conn: sqlite3.Connection, *, active_only: bool = True
) -> list[dict[str, Any]]:
    sql = """
        SELECT a.*, u.username AS created_by_username
        FROM applications a
        LEFT JOIN users u ON u.id = a.created_by
        WHERE a.approval_status = 'approved'
    """
    if active_only:
        sql += " AND a.is_active = 1"
    sql += " ORDER BY a.sort_order, a.name, a.id"
    rows = conn.execute(sql).fetchall()
    return [_row_to_application(conn, r) for r in rows]


def list_applications_for_team(
    conn: sqlite3.Connection, team_name: str, *, active_only: bool = True
) -> list[dict[str, Any]]:
    sql = """
        SELECT a.*, u.username AS created_by_username
        FROM applications a
        LEFT JOIN users u ON u.id = a.created_by
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
        SELECT DISTINCT a.*, u.username AS created_by_username
        FROM applications a
        LEFT JOIN users u ON u.id = a.created_by
        JOIN application_teams at ON at.application_id = a.id
        JOIN teams t ON t.id = at.team_id
        WHERE t.name IN ({placeholders}) AND a.approval_status = 'approved'
    """
    if active_only:
        sql += " AND a.is_active = 1"
    sql += " ORDER BY a.sort_order, a.name, a.id"
    rows = conn.execute(sql, tuple(team_names)).fetchall()
    return [_row_to_application(conn, r) for r in rows]


def list_applications_for_publisher_team(
    conn: sqlite3.Connection,
    publisher_team: str,
    *,
    visible_team_names: list[str] | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Approved apps from publishers in ``publisher_team``.

    ``visible_team_names`` applies the current user's app-sharing visibility. When
    omitted, all approved apps from the publisher team are returned (admin path).
    """
    if visible_team_names is not None and not visible_team_names:
        return []
    params: list[Any] = [publisher_team]
    sql = """
        SELECT DISTINCT a.*, u.username AS created_by_username
        FROM applications a
        LEFT JOIN users u ON u.id = a.created_by
        JOIN user_teams publisher_ut ON publisher_ut.user_id = a.created_by
        JOIN teams publisher_t ON publisher_t.id = publisher_ut.team_id
    """
    if visible_team_names is not None:
        placeholders = ",".join("?" for _ in visible_team_names)
        sql += f"""
        JOIN application_teams at ON at.application_id = a.id
        JOIN teams visible_t ON visible_t.id = at.team_id
        WHERE publisher_t.name = ?
          AND visible_t.name IN ({placeholders})
          AND a.approval_status = 'approved'
        """
        params.extend(visible_team_names)
    else:
        sql += """
        WHERE publisher_t.name = ?
          AND a.approval_status = 'approved'
        """
    if active_only:
        sql += " AND a.is_active = 1"
    sql += " ORDER BY a.sort_order, a.name, a.id"
    rows = conn.execute(sql, params).fetchall()
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


def find_owner_alias_by_slug(
    conn: sqlite3.Connection, slug: str, owner_id: int
) -> dict[str, Any] | None:
    """Return the alias application owned by ``owner_id`` whose alias slug
    (stored in ``url``) matches ``slug``, or None.

    Used to enforce that an embedded application frames one of the owner's own
    existing aliases: an embedded app's iframe renders the same-origin alias
    path served by the reverse proxy, so its source must be a real alias the
    owner controls. The alias must be active and approved to be a usable target.
    """
    row = conn.execute(
        """
        SELECT * FROM applications
        WHERE url_type = 'alias'
          AND created_by = ?
          AND url = ?
          AND is_active = 1
          AND approval_status = 'approved'
        LIMIT 1
        """,
        (owner_id, slug),
    ).fetchone()
    return _row_to_application(conn, row) if row is not None else None


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
    apps_protocol: str = "http",
    apps_port: str = "",
    apps_path: str = "",
    alias_auth_required: bool = True,
    apps_rewrite_root: bool = False,
    is_private: bool = False,
    shared_user_ids: list[int] | None = None,
) -> dict[str, Any]:
    cur = conn.execute(
        """
        INSERT INTO applications
            (name, description, url, url_type, icon_url, is_active,
             approval_status, created_by, sort_order, apps_server, apps_protocol,
             apps_port, apps_path,
             alias_auth_required, apps_rewrite_root, is_private)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            apps_protocol,
            apps_port,
            apps_path,
            int(alias_auth_required),
            int(apps_rewrite_root),
            int(is_private),
        ),
    )
    application_id = int(cur.lastrowid)
    set_application_teams(conn, application_id, teams)
    set_application_users(conn, application_id, shared_user_ids or [])
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
    created_by: int | None = None,
    sort_order: int | None = None,
    teams: list[str] | None = None,
    apps_server: str | None = None,
    apps_protocol: str | None = None,
    apps_port: str | None = None,
    apps_path: str | None = None,
    alias_auth_required: bool | None = None,
    apps_rewrite_root: bool | None = None,
    is_private: bool | None = None,
    shared_user_ids: list[int] | None = None,
    pending_alias: str | None = None,
    pending_is_active: bool | None = None,
    clear_pending_is_active: bool = False,
    pending_alias_auth_required: bool | None = None,
    clear_pending_alias_auth_required: bool = False,
    pending_apps_rewrite_root: bool | None = None,
    clear_pending_apps_rewrite_root: bool = False,
    needs_push: bool | None = None,
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
    if created_by is not None:
        columns["created_by"] = created_by
    if sort_order is not None:
        columns["sort_order"] = sort_order
    if apps_server is not None:
        columns["apps_server"] = apps_server
    if apps_protocol is not None:
        columns["apps_protocol"] = apps_protocol
    if apps_port is not None:
        columns["apps_port"] = apps_port
    if apps_path is not None:
        columns["apps_path"] = apps_path
    if alias_auth_required is not None:
        columns["alias_auth_required"] = int(alias_auth_required)
    if apps_rewrite_root is not None:
        columns["apps_rewrite_root"] = int(apps_rewrite_root)
    if is_private is not None:
        columns["is_private"] = int(is_private)
    if pending_alias is not None:
        columns["pending_alias"] = pending_alias
    if pending_is_active is not None:
        columns["pending_is_active"] = int(pending_is_active)
    if clear_pending_is_active:
        columns["pending_is_active"] = None
    if pending_alias_auth_required is not None:
        columns["pending_alias_auth_required"] = int(pending_alias_auth_required)
    if clear_pending_alias_auth_required:
        columns["pending_alias_auth_required"] = None
    if pending_apps_rewrite_root is not None:
        columns["pending_apps_rewrite_root"] = int(pending_apps_rewrite_root)
    if clear_pending_apps_rewrite_root:
        columns["pending_apps_rewrite_root"] = None
    if needs_push is not None:
        columns["needs_push"] = int(needs_push)
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
    if shared_user_ids is not None:
        set_application_users(conn, application_id, shared_user_ids)
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
    needs_push: bool | None = None,
) -> None:
    """Record the result of the last reverse-proxy alias push for an app."""
    needs_push_sql = "" if needs_push is None else ", needs_push = ?"
    params: list[Any] = [status, log]
    if needs_push is not None:
        params.append(int(needs_push))
    params.append(application_id)
    conn.execute(
        "UPDATE applications SET last_push_status = ?, last_push_log = ?, "
        f"last_push_at = datetime('now'){needs_push_sql} WHERE id = ?",
        params,
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


def parse_collaborators(raw: Any) -> list[str]:
    """Decode the stored collaborators JSON to a list of names.

    Tolerates any legacy or invalid value by returning an empty list, so a
    malformed column never breaks the session or settings responses.
    """
    if isinstance(raw, list):
        return [str(name) for name in raw]
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(name) for name in parsed if isinstance(name, str)]


# issue_025: Proxmox realms are stored as the same JSON-string-list shape as
# collaborators; reuse the tolerant parser.
parse_string_list = parse_collaborators


def get_settings_row(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the single settings row (id = 1), or an empty default shape."""
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    if row is None:
        return {
            "nginx_host": "",
            "nginx_user": "",
            "nginx_conf_path": "",
            "ssh_key_path": "",
            "appmanager_proxy_host": "",
            "appmanager_proxy_port": "",
            "alias_template": "",
            "app_name": "",
            "app_logo": "",
            "collaborators": "[]",
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
    reverse_proxy_ssh_key_id: int | None = None,
    appmanager_proxy_host: str | None = None,
    appmanager_proxy_port: str | None = None,
    alias_template: str | None = None,
    app_name: str | None = None,
    app_logo: str | None = None,
    collaborators: str | None = None,
    default_theme: str | None = None,
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
    if appmanager_proxy_host is not None:
        columns["appmanager_proxy_host"] = appmanager_proxy_host
    if appmanager_proxy_port is not None:
        columns["appmanager_proxy_port"] = appmanager_proxy_port
    if alias_template is not None:
        columns["alias_template"] = alias_template
    if reverse_proxy_ssh_key_id is not None:
        columns["reverse_proxy_ssh_key_id"] = reverse_proxy_ssh_key_id
    if app_name is not None:
        columns["app_name"] = app_name
    if app_logo is not None:
        columns["app_logo"] = app_logo
    if collaborators is not None:
        columns["collaborators"] = collaborators
    if default_theme is not None:
        columns["default_theme"] = default_theme
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


# Columns updatable through the provisioning settings endpoint. The API key is
# write-only: it can be set here but is never included in any response model.
_PROVISIONING_COLUMNS = (
    "provider_type",
    "proxmox_url",
    "proxmox_token_name",
    "proxmox_api_key",
    "proxmox_template_filter",
    "proxmox_templates_only",
    "proxmox_verify_tls",
    "proxmox_conn_status",
    "proxmox_conn_log",
    "provisioning_self_service",
    "provisioning_max_servers",
    "provisioning_allow_resource_edit",
    "provisioning_max_cpus",
    "provisioning_max_memory_gb",
    "provisioning_max_disk_gb",
    "provisioning_add_to_pool",
    "proxmox_realms",
    "proxmox_pool_prefix",
    "jump_enabled",
    "jump_host",
    "jump_user",
    "jump_port",
    "jump_ssh_key_id",
    "jump_management_user",
    "jump_account_mode",
    "jump_jumper_user",
    "jump_bundle_override",
    "jump_bundle_host",
    "jump_bundle_port",
)


def update_provisioning_settings(
    conn: sqlite3.Connection, **values: Any
) -> dict[str, Any]:
    """Update provisioning/provider settings columns (None values skipped).

    Only the fixed, code-defined column set above is accepted; anything else
    raises so a typo cannot silently write arbitrary columns.
    """
    conn.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    columns: dict[str, Any] = {}
    for key, value in values.items():
        if key not in _PROVISIONING_COLUMNS:
            raise ValueError(f"Unknown provisioning settings column: {key}")
        if value is None:
            continue
        columns[key] = int(value) if isinstance(value, bool) else value
    if columns:
        assignments = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(
            f"UPDATE settings SET {assignments}, updated_at = datetime('now') "
            "WHERE id = ?",
            [*columns.values(), 1],
        )
    return get_settings_row(conn)


# ---------------------------------------------------------------------------
# SSH key registry (issue_015-r1)
# ---------------------------------------------------------------------------


def _row_to_ssh_key(row: sqlite3.Row) -> dict[str, Any]:
    """Registry entry WITHOUT secret material (safe for API responses)."""
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "path": row["path"],
        "public_key": row["public_key"],
        "fingerprint": row["fingerprint"],
        "has_private_key": bool(row["encrypted_private_key"]),
    }


def list_ssh_keys(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM ssh_keys ORDER BY name, id").fetchall()
    return [_row_to_ssh_key(r) for r in rows]


def get_ssh_key(conn: sqlite3.Connection, key_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM ssh_keys WHERE id = ?", (key_id,)
    ).fetchone()
    return _row_to_ssh_key(row) if row else None


def create_ssh_key(
    conn: sqlite3.Connection,
    *,
    name: str,
    kind: str,
    path: str = "",
    encrypted_private_key: str = "",
    public_key: str = "",
    fingerprint: str = "",
) -> dict[str, Any]:
    try:
        cur = conn.execute(
            """
            INSERT INTO ssh_keys
                (name, kind, path, encrypted_private_key, public_key, fingerprint)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name.strip(), kind, path.strip(), encrypted_private_key,
             public_key, fingerprint),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"An SSH key named '{name.strip()}' already exists."
        ) from exc
    key = get_ssh_key(conn, int(cur.lastrowid))
    assert key is not None
    return key


def update_ssh_key(
    conn: sqlite3.Connection,
    key_id: int,
    *,
    name: str | None = None,
    kind: str | None = None,
    path: str | None = None,
    encrypted_private_key: str | None = None,
    public_key: str | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any] | None:
    """Update selected columns of a registry key. Only provided fields change.

    The router (re)computes ``encrypted_private_key`` / ``public_key`` /
    ``fingerprint`` from a newly pasted key and clears the stored secret when
    switching to a path key, then passes the results here.
    """
    if get_ssh_key(conn, key_id) is None:
        return None
    columns: dict[str, Any] = {}
    if name is not None:
        columns["name"] = name.strip()
    if kind is not None:
        columns["kind"] = kind
    if path is not None:
        columns["path"] = path.strip()
    if encrypted_private_key is not None:
        columns["encrypted_private_key"] = encrypted_private_key
    if public_key is not None:
        columns["public_key"] = public_key
    if fingerprint is not None:
        columns["fingerprint"] = fingerprint
    if columns:
        assignments = ", ".join(f"{col} = ?" for col in columns)
        try:
            conn.execute(
                f"UPDATE ssh_keys SET {assignments}, "
                "updated_at = datetime('now') WHERE id = ?",
                [*columns.values(), key_id],
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"An SSH key named '{columns.get('name')}' already exists."
            ) from exc
    return get_ssh_key(conn, key_id)


def get_ssh_key_secret(conn: sqlite3.Connection, key_id: int) -> str:
    """Return the stored encrypted private key token (or '') for a key."""
    row = conn.execute(
        "SELECT encrypted_private_key FROM ssh_keys WHERE id = ?", (key_id,)
    ).fetchone()
    return row["encrypted_private_key"] if row else ""


def ssh_key_references(conn: sqlite3.Connection, key_id: int) -> list[str]:
    """Human-readable list of places that reference a registry key."""
    refs: list[str] = []
    s = conn.execute(
        "SELECT 1 FROM settings WHERE id = 1 AND reverse_proxy_ssh_key_id = ?",
        (key_id,),
    ).fetchone()
    if s:
        refs.append("reverse-proxy configuration")
    j = conn.execute(
        "SELECT 1 FROM settings WHERE id = 1 AND jump_ssh_key_id = ?",
        (key_id,),
    ).fetchone()
    if j:
        refs.append("jump server")
    for r in conn.execute(
        "SELECT name FROM server_templates WHERE admin_ssh_key_id = ?",
        (key_id,),
    ).fetchall():
        refs.append(f"server template '{r['name']}'")
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM user_servers WHERE admin_ssh_key_id = ?",
        (key_id,),
    ).fetchone()["c"]
    if n:
        refs.append(f"{n} user server(s)")
    return refs


def delete_ssh_key(conn: sqlite3.Connection, key_id: int) -> bool:
    cur = conn.execute("DELETE FROM ssh_keys WHERE id = ?", (key_id,))
    return cur.rowcount > 0


def reverse_proxy_key_path(conn: sqlite3.Connection, row: dict[str, Any]) -> str:
    """Effective SSH key path for reverse-proxy operations.

    Resolves the registry key referenced by ``reverse_proxy_ssh_key_id``
    (materializing a stored key when needed), falling back to the legacy
    ``ssh_key_path`` column.
    """
    from . import servers

    return servers.resolve_ssh_key(
        conn,
        row.get("reverse_proxy_ssh_key_id"),
        fallback_path=(row.get("ssh_key_path") or "").strip(),
    )


# ---------------------------------------------------------------------------
# Server templates (Proxmox templates registered for user-server creation)
# ---------------------------------------------------------------------------


def _row_to_server_template(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "vmid": row["vmid"],
        "name": row["name"],
        "kind": row["kind"],
        "admin_ssh_key_path": row["admin_ssh_key_path"],
        "admin_ssh_key_id": row["admin_ssh_key_id"],
        "main_os_user": row["main_os_user"],
        "enable_sudo": bool(row["enable_sudo"]),
        "enable_trusted_access": bool(row["enable_trusted_access"]),
        "is_apps_server": bool(row["is_apps_server"]),
    }


def list_server_templates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM server_templates ORDER BY name, id"
    ).fetchall()
    return [_row_to_server_template(r) for r in rows]


def get_server_template(
    conn: sqlite3.Connection, template_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM server_templates WHERE id = ?", (template_id,)
    ).fetchone()
    return _row_to_server_template(row) if row else None


def create_server_template(
    conn: sqlite3.Connection,
    *,
    vmid: int,
    name: str,
    kind: str,
    admin_ssh_key_path: str = "",
    admin_ssh_key_id: int | None = None,
    main_os_user: str = "",
    enable_sudo: bool = True,
    enable_trusted_access: bool = True,
    is_apps_server: bool = False,
) -> dict[str, Any]:
    try:
        cur = conn.execute(
            """
            INSERT INTO server_templates
                (vmid, name, kind, admin_ssh_key_path, admin_ssh_key_id,
                 main_os_user, enable_sudo, enable_trusted_access,
                 is_apps_server)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (vmid, name.strip(), kind, admin_ssh_key_path.strip(),
             admin_ssh_key_id, main_os_user.strip(), int(enable_sudo),
             int(enable_trusted_access), int(is_apps_server)),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"A server template named '{name.strip()}' already exists."
        ) from exc
    template = get_server_template(conn, int(cur.lastrowid))
    assert template is not None
    return template


def update_server_template(
    conn: sqlite3.Connection,
    template_id: int,
    *,
    vmid: int | None = None,
    name: str | None = None,
    kind: str | None = None,
    admin_ssh_key_path: str | None = None,
    admin_ssh_key_id: int | None = None,
    clear_admin_ssh_key_id: bool = False,
    main_os_user: str | None = None,
    enable_sudo: bool | None = None,
    enable_trusted_access: bool | None = None,
    is_apps_server: bool | None = None,
) -> dict[str, Any] | None:
    if get_server_template(conn, template_id) is None:
        return None
    columns: dict[str, Any] = {}
    if vmid is not None:
        columns["vmid"] = vmid
    if name is not None:
        columns["name"] = name.strip()
    if kind is not None:
        columns["kind"] = kind
    if admin_ssh_key_path is not None:
        columns["admin_ssh_key_path"] = admin_ssh_key_path.strip()
    if admin_ssh_key_id is not None:
        columns["admin_ssh_key_id"] = admin_ssh_key_id
    elif clear_admin_ssh_key_id:
        columns["admin_ssh_key_id"] = None
    if main_os_user is not None:
        columns["main_os_user"] = main_os_user.strip()
    if enable_sudo is not None:
        columns["enable_sudo"] = int(enable_sudo)
    if enable_trusted_access is not None:
        columns["enable_trusted_access"] = int(enable_trusted_access)
    if is_apps_server is not None:
        columns["is_apps_server"] = int(is_apps_server)
    if columns:
        assignments = ", ".join(f"{col} = ?" for col in columns)
        try:
            conn.execute(
                f"UPDATE server_templates SET {assignments}, "
                "updated_at = datetime('now') WHERE id = ?",
                [*columns.values(), template_id],
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "A server template with that name already exists."
            ) from exc
    return get_server_template(conn, template_id)


def delete_server_template(conn: sqlite3.Connection, template_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM server_templates WHERE id = ?", (template_id,)
    )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# User servers (provisioned or referenced LXC/VM guests)
# ---------------------------------------------------------------------------


def _row_to_user_server(row: sqlite3.Row) -> dict[str, Any]:
    # ``tpl_is_apps_server`` is only present when the query joined
    # server_templates (issue_021); queries that never need it (e.g. the
    # deletion sweep) omit the join and default to False here.
    keys = row.keys()
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["name"],
        "hostname": row["hostname"],
        "template_id": row["template_id"],
        "template_name": row["template_name"],
        "vmid": row["vmid"],
        "node": row["node"],
        "kind": row["kind"],
        "ip_address": row["ip_address"],
        "cpus": row["cpus"],
        "memory_gb": row["memory_gb"],
        "disk_gb": row["disk_gb"],
        "admin_modified": bool(row["admin_modified"]),
        # Internal only: UserServerOut has no such fields, so these never
        # reach API responses.
        "admin_ssh_key_path": row["admin_ssh_key_path"],
        "admin_ssh_key_id": row["admin_ssh_key_id"],
        "status": row["status"],
        "last_log": row["last_log"],
        "deletion_requested_at": row["deletion_requested_at"],
        "deletion_error": row["deletion_error"],
        "created_at": row["created_at"],
        # issue_021: True when the server's template is (still) flagged
        # is_apps_server. A deleted template (template_id set NULL) yields
        # False via the LEFT JOIN, never a stale True.
        "is_apps_server": bool(row["tpl_is_apps_server"])
        if "tpl_is_apps_server" in keys else False,
    }


_USER_SERVER_SELECT = (
    "SELECT us.*, st.is_apps_server AS tpl_is_apps_server "
    "FROM user_servers us "
    "LEFT JOIN server_templates st ON st.id = us.template_id "
)


def list_user_servers(
    conn: sqlite3.Connection, user_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        _USER_SERVER_SELECT + "WHERE us.user_id = ? ORDER BY us.name, us.id",
        (user_id,),
    ).fetchall()
    return [_row_to_user_server(r) for r in rows]


def server_name_exists(conn: sqlite3.Connection, name: str) -> bool:
    """True when any server (any owner) already has this name, case-insensitive.

    Server names are globally unique in AppManager (they embed the owner and
    template), so this backs the pre-create uniqueness check.
    """
    row = conn.execute(
        "SELECT 1 FROM user_servers WHERE lower(name) = lower(?) LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def list_all_servers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every server across all users, each annotated with its owner's username.

    Used by the admin server overview; the caller derives the owner id from the
    username as needed.
    """
    rows = conn.execute(
        "SELECT s.*, u.username AS owner_username, "
        "st.is_apps_server AS tpl_is_apps_server "
        "FROM user_servers s JOIN users u ON u.id = s.user_id "
        "LEFT JOIN server_templates st ON st.id = s.template_id "
        "ORDER BY u.username, s.name, s.id"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        server = _row_to_user_server(r)
        server["owner_username"] = r["owner_username"]
        out.append(server)
    return out


def get_user_server(
    conn: sqlite3.Connection, user_id: int, server_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        _USER_SERVER_SELECT + "WHERE us.id = ? AND us.user_id = ?",
        (server_id, user_id),
    ).fetchone()
    return _row_to_user_server(row) if row else None


def count_user_servers(conn: sqlite3.Connection, user_id: int) -> int:
    """Servers counted against the per-user limit.

    A ``failed`` record still counts when it carries a ``vmid``: the guest
    was actually cloned, so it consumes real capacity. Only failures that
    never produced a guest are excluded.
    """
    return conn.execute(
        "SELECT COUNT(*) AS c FROM user_servers "
        "WHERE user_id = ? AND (status != 'failed' OR vmid IS NOT NULL)",
        (user_id,),
    ).fetchone()["c"]


def sum_user_server_resources(
    conn: sqlite3.Connection, user_id: int
) -> dict[str, int]:
    """Total resources counted against the user's quota.

    Counts every non-failed server the user owns (including admin-created and
    account-creation auto-provisioned servers, which carry ``admin_modified``);
    only records that never produced a guest (failed with no vmid) are excluded.
    This keeps the usage figures consistent with ``count_user_servers`` so the
    quota bars reflect the resources actually committed by existing servers.
    """
    row = conn.execute(
        """
        SELECT COALESCE(SUM(cpus), 0) AS cpus,
               COALESCE(SUM(memory_gb), 0) AS memory_gb,
               COALESCE(SUM(disk_gb), 0) AS disk_gb
        FROM user_servers
        WHERE user_id = ?
          AND (status != 'failed' OR vmid IS NOT NULL)
        """,
        (user_id,),
    ).fetchone()
    return {
        "cpus": row["cpus"],
        "memory_gb": row["memory_gb"],
        "disk_gb": row["disk_gb"],
    }


def create_user_server(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    name: str,
    hostname: str = "",
    template_id: int | None = None,
    template_name: str = "",
    vmid: int | None = None,
    node: str = "",
    kind: str,
    ip_address: str = "",
    cpus: int = 0,
    memory_gb: int = 0,
    disk_gb: int = 0,
    admin_modified: bool = False,
    admin_ssh_key_path: str = "",
    admin_ssh_key_id: int | None = None,
    status: str = "created",
    last_log: str = "",
) -> dict[str, Any]:
    try:
        cur = conn.execute(
            """
            INSERT INTO user_servers
                (user_id, name, hostname, template_id, template_name, vmid,
                 node, kind, ip_address, cpus, memory_gb, disk_gb,
                 admin_modified, admin_ssh_key_path, admin_ssh_key_id,
                 status, last_log)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, name, hostname, template_id, template_name, vmid,
                node, kind, ip_address, cpus, memory_gb, disk_gb,
                int(admin_modified), admin_ssh_key_path, admin_ssh_key_id,
                status, last_log,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(
            f"A server named '{name}' already exists for this user."
        ) from exc
    server = get_user_server(conn, user_id, int(cur.lastrowid))
    assert server is not None
    return server


def update_user_server(
    conn: sqlite3.Connection,
    user_id: int,
    server_id: int,
    *,
    ip_address: str | None = None,
    cpus: int | None = None,
    memory_gb: int | None = None,
    disk_gb: int | None = None,
    admin_modified: bool | None = None,
    status: str | None = None,
    last_log: str | None = None,
    deletion_requested_at: str | None = None,
    deletion_error: str | None = None,
) -> dict[str, Any] | None:
    if get_user_server(conn, user_id, server_id) is None:
        return None
    columns: dict[str, Any] = {}
    if ip_address is not None:
        columns["ip_address"] = ip_address
    if cpus is not None:
        columns["cpus"] = cpus
    if memory_gb is not None:
        columns["memory_gb"] = memory_gb
    if disk_gb is not None:
        columns["disk_gb"] = disk_gb
    if admin_modified is not None:
        columns["admin_modified"] = int(admin_modified)
    if status is not None:
        columns["status"] = status
    if last_log is not None:
        columns["last_log"] = last_log
    if deletion_requested_at is not None:
        columns["deletion_requested_at"] = deletion_requested_at
    if deletion_error is not None:
        columns["deletion_error"] = deletion_error
    if columns:
        assignments = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(
            f"UPDATE user_servers SET {assignments}, "
            "updated_at = datetime('now') WHERE id = ? AND user_id = ?",
            [*columns.values(), server_id, user_id],
        )
    return get_user_server(conn, user_id, server_id)


def delete_user_server(
    conn: sqlite3.Connection, user_id: int, server_id: int
) -> bool:
    cur = conn.execute(
        "DELETE FROM user_servers WHERE id = ? AND user_id = ?",
        (server_id, user_id),
    )
    return cur.rowcount > 0


def list_servers_pending_deletion(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """All servers with an outstanding deletion request, across every user.

    Used by the deferred-deletion sweep. Rows already marked with a
    ``deletion_error`` are still returned so a retry can clear or re-record
    the error; the sweep decides whether the grace window has elapsed.
    """
    rows = conn.execute(
        "SELECT * FROM user_servers "
        "WHERE deletion_requested_at IS NOT NULL AND deletion_requested_at <> '' "
        "ORDER BY deletion_requested_at, id"
    ).fetchall()
    return [_row_to_user_server(r) for r in rows]
