#!/usr/bin/env python3
"""Re-push every approved alias with immutable app-aware authorization.

Dry-run by default. Run with ``--apply`` after backing up the database and nginx
configuration. Safe deployment order: pull this code, run this script while the
old backend is still serving, then restart AppManager. The old proxy-check
ignores the new query parameters and remains fail-closed for public aliases
during the brief cutover.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import repository, reverse_proxy  # noqa: E402
from app.db import get_connection, init_db  # noqa: E402
from app.routers import applications

ACTOR = {"id": None, "username": "system:app-aware-alias-migration", "role": "admin"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, url, approval_status FROM applications "
            "WHERE url_type='alias' ORDER BY id"
        ).fetchall()
        settings = repository.get_settings_row(conn)
        settings["ssh_key_path"] = repository.reverse_proxy_key_path(conn, settings)
    deployed_ids: set[int] = set()
    target = settings.get("nginx_host", "")
    if settings.get("nginx_user"):
        target = f"{settings['nginx_user']}@{target}"
    if target and settings.get("ssh_key_path") and settings.get("nginx_conf_path"):
        read = reverse_proxy._ssh(
            target, settings["ssh_key_path"],
            f"cat {shlex.quote(settings['nginx_conf_path'])}",
        )
        if read.rc == 0:
            deployed_ids = {
                int(value) for value in re.findall(r"appmanager-lite-app:(\d+)", read.out)
            }
        else:
            print("FAILED: could not read configured nginx file; refusing migration")
            return 2
    elif args.apply:
        print("FAILED: reverse proxy is not fully configured; refusing migration")
        return 2
    rows_by_id = {row["id"]: row for row in rows}
    all_ids = sorted(set(rows_by_id) | deployed_ids)
    print(f"{'APPLY' if args.apply else 'DRY-RUN'}: {len(all_ids)} alias blocks/rows")
    if args.apply:
        shared = reverse_proxy.ensure_proxy_auth_config(settings)
        if shared.status != "ok":
            print("FAILED: could not upgrade shared proxy-auth location")
            print(shared.transcript)
            return 2
        print("  ok shared proxy-auth location")
    failures = 0
    for app_id in all_ids:
        row = rows_by_id.get(app_id)
        should_push = row is not None and row["approval_status"] == "approved"
        if not args.apply:
            action = "push" if should_push else "remove"
            print(f"  would {action} id={app_id} alias={row['url'] if row else '<orphan>'}")
            continue
        if should_push:
            with get_connection() as conn:
                conn.execute("UPDATE applications SET needs_push=1 WHERE id=?", (app_id,))
            applications._push_alias_on_approval(app_id, ACTOR)
            verified = reverse_proxy.read_alias_config(settings, app_id=app_id)
            ok = verified.status == "ok"
        else:
            removed = reverse_proxy.remove_alias(settings, app_id=app_id)
            verified = reverse_proxy.read_alias_config(settings, app_id=app_id)
            ok = removed.status in ("ok", "skipped") and verified.status == "not_found"
        print(f"  {'ok' if ok else 'FAILED'} id={app_id} action={'push' if should_push else 'remove'}")
        failures += 0 if ok else 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
