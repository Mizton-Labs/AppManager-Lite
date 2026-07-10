#!/usr/bin/env python3
"""One-off: persist each alias application's registered apps-server host.

Background
----------
An alias application proxies to an upstream "apps server". Historically an
application could carry an empty ``apps_server`` (resolved to the owner's
registered apps-server only at push time) or a free-text "Custom" host that
does not correspond to any of the owner's provisioned apps-server servers.

This one-off rewrites every **alias** application whose stored ``apps_server``
is **empty OR custom** to the owner's **registered** apps-server hostname (the
server cloned from an ``is_apps_server`` template), using the same resolution
the runtime push uses (``resolve_user_apps_server_host``). Applications that
already point at the owner's registered server, non-alias (``url``)
applications, and owners with no registered apps-server are left untouched.

Safety
------
- Dry-run by default: prints the full plan and writes nothing. Pass ``--apply``
  to perform the change.
- Idempotent: a second run finds the apps already registered and does nothing.
- An explicit exclusion list (``EXCLUDED_APP_IDS``) is never modified.
- Only ever assigns the **app owner's own** registered apps-server host, so an
  application can never be pointed at another user's server.
- Each change marks ``needs_push`` and re-pushes the alias so the live reverse
  proxy is updated, and records an ``apps_server_backfill`` audit entry.

Usage
-----
    python -m scripts.oneoff_backfill_apps_server            # dry-run
    python -m scripts.oneoff_backfill_apps_server --apply    # apply + re-push

Run it inside the backend environment (so ``app`` is importable and the
configured ``data/app.db`` is used). Back up the database first.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import Any

from app import audit, repository
from app.db import get_connection
from app.routers import applications as apps_router

# Applications that must never be touched by this migration, regardless of
# classification (e.g. an intentionally custom upstream).
EXCLUDED_APP_IDS: frozenset[int] = frozenset({12})  # CDT CosmicGate Virtual Lab

# A synthetic actor for audit/push attribution; None actor is stored as a null
# actor by audit.record, clearly marking a system/migration action.
_ACTOR: dict[str, Any] = {"id": None, "username": "system:apps-server-backfill",
                          "role": "admin"}


def _registered_hosts(conn: sqlite3.Connection, owner_id: int) -> list[str]:
    """Owner's registered apps-server hosts (hostname or ip), for classifying
    whether a stored value is already one of them. Values are stripped so the
    comparison matches the stripped stored value (keeps the migration
    idempotent even if a host were stored with stray whitespace)."""
    hosts: list[str] = []
    for s in repository.list_user_servers(conn, owner_id):
        if (
            s.get("is_apps_server")
            and s.get("status") != "failed"
            and (s.get("hostname") or s.get("ip_address"))
        ):
            if (s.get("hostname") or "").strip():
                hosts.append(s["hostname"].strip())
            if (s.get("ip_address") or "").strip():
                hosts.append(s["ip_address"].strip())
    return hosts


def _all_applications(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every application (regardless of active/approval state) with the fields
    this migration reasons about. Read-only; writes go through the repository."""
    rows = conn.execute(
        "SELECT id, name, url_type, created_by, apps_server "
        "FROM applications ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def build_plan(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return a per-application plan describing the migration decision."""
    plan: list[dict[str, Any]] = []
    for app in _all_applications(conn):
        app_id = app["id"]
        owner_id = app.get("created_by")
        current = (app.get("apps_server") or "").strip()
        row: dict[str, Any] = {
            "id": app_id,
            "name": app["name"],
            "url_type": app["url_type"],
            "owner_id": owner_id,
            "current": current,
            "target": "",
            "classification": "",
            "action": "",
        }

        if app["url_type"] != "alias":
            row["classification"] = "url"
            row["action"] = "skip (not an alias)"
            plan.append(row)
            continue

        owner = repository.get_user_by_id(conn, owner_id) if owner_id else None
        target = (apps_router.resolve_user_apps_server_host(conn, owner) or "").strip()
        registered = _registered_hosts(conn, owner_id) if owner_id else []
        row["target"] = target

        if not registered or not target:
            row["classification"] = "no registered apps-server"
            row["action"] = "skip (owner has no registered apps-server)"
        elif current and current in registered:
            row["classification"] = "registered"
            row["action"] = "skip (already registered)"
        elif app_id in EXCLUDED_APP_IDS:
            row["classification"] = "empty" if not current else "custom"
            row["action"] = "skip (excluded)"
        elif not current:
            row["classification"] = "empty"
            row["action"] = "set"
        else:
            row["classification"] = "custom"
            row["action"] = "change"
        plan.append(row)
    return plan


def _print_plan(plan: list[dict[str, Any]]) -> None:
    header = (
        f"{'id':>3}  {'type':5}  {'owner':>5}  {'class':24}  "
        f"{'current':32}  {'->':2}  {'target / action'}"
    )
    print(header)
    print("-" * len(header))
    for r in plan:
        changing = r["action"] in ("set", "change")
        arrow = "->" if changing else "  "
        detail = r["target"] if changing else r["action"]
        print(
            f"{r['id']:>3}  {r['url_type']:5}  {str(r['owner_id']):>5}  "
            f"{r['classification']:24}  {(r['current'] or '(empty)'):32}  "
            f"{arrow:2}  {detail}"
        )


def apply_plan(conn: sqlite3.Connection, plan: list[dict[str, Any]]) -> list[int]:
    """Apply the DB changes (apps_server + needs_push) and record an audit row
    per changed application. Returns the ids that were changed (to re-push)."""
    changed: list[int] = []
    for r in plan:
        if r["action"] not in ("set", "change"):
            continue
        app_id = r["id"]
        old = r["current"]
        new = r["target"]
        repository.update_application(
            conn, app_id, apps_server=new, needs_push=True
        )
        audit.record(
            conn,
            category=audit.CATEGORY_APPLICATION,
            action="apps_server_backfill",
            actor=_ACTOR,
            target_type="application",
            target_id=app_id,
            target_name=r["name"],
            detail=f"apps_server: {old or '(empty)'} -> {new} (one-off backfill)",
        )
        changed.append(app_id)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the change (default is a dry-run that writes nothing).",
    )
    args = parser.parse_args(argv)

    with get_connection() as conn:
        plan = build_plan(conn)
        _print_plan(plan)
        to_change = [r for r in plan if r["action"] in ("set", "change")]
        print()
        print(f"{len(to_change)} application(s) would be updated.")
        if not args.apply:
            print("Dry-run: no changes written. Re-run with --apply to apply.")
            return 0
        if not to_change:
            print("Nothing to apply.")
            return 0
        changed = apply_plan(conn, plan)
        # The DB updates commit when this get_connection() context exits.

    # Re-push each changed alias on its own connection (reads the freshly
    # stored apps_server) so the live reverse proxy is updated.
    print()
    print("Re-pushing updated aliases...")
    for app_id in changed:
        try:
            apps_router._push_alias_on_approval(app_id, _ACTOR)
            with get_connection() as conn:
                app = repository.get_application(conn, app_id)
            status = (app or {}).get("last_push_status")
            print(f"  app {app_id}: push status = {status}")
        except Exception as exc:  # keep pushing the rest; row is committed
            print(
                f"  app {app_id}: push step errored ({exc.__class__.__name__}); "
                "needs_push remains set -- re-push it manually."
            )
    print(f"Done. {len(changed)} application(s) updated and re-pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
