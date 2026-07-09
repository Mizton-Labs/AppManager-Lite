"""Jump-server user onboarding/offboarding (issue_015-r1 phase C).

When a jump server is configured, each AppManager user gets an OS account on
the bastion (named by their derived user_id) with their SSH public key
installed. User deletion removes the key from that account. All operations
connect as the configured jump user with a registry-selected SSH key.

``_run`` is the subprocess seam (shared with ``servers``); tests patch it.
"""

from __future__ import annotations

import shlex
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import repository, servers, sshkeys
from .proxmox import ProxmoxResult

_SSH_TIMEOUT = 25


@dataclass
class JumpConfig:
    enabled: bool
    host: str
    user: str
    key_path: str
    port: int = 22

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.host and self.user and self.key_path)


def load_config(conn: sqlite3.Connection) -> JumpConfig:
    row = repository.get_settings_row(conn)
    key_path = servers.resolve_ssh_key(conn, row.get("jump_ssh_key_id"))
    return JumpConfig(
        enabled=bool(row.get("jump_enabled", 0)),
        host=(row.get("jump_host") or "").strip(),
        user=(row.get("jump_user") or "").strip(),
        key_path=key_path,
        port=int(row.get("jump_port", 22) or 22),
    )


def _ssh_argv(config: JumpConfig, remote_command: str) -> list[str]:
    return [
        "ssh",
        "-i",
        config.key_path,
        "-p",
        str(config.port),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        f"{config.user}@{config.host}",
        remote_command,
    ]


def _run_remote(config: JumpConfig, remote: str):
    return servers._run(_ssh_argv(config, remote), timeout=_SSH_TIMEOUT)


def onboard_user(
    config: JumpConfig,
    *,
    os_user: str,
    public_key: str,
    result: ProxmoxResult,
) -> bool:
    """Create the OS account (if missing) and install the public key.

    Idempotent. The remote runs under the jump user, which must be able to
    create accounts (root or sudo). Only the public key travels.
    """
    if not servers._OS_USER_RE.match(os_user):
        result.fail(f"invalid OS username {os_user!r}")
        return False
    if not public_key.strip():
        result.fail("user has no SSH public key")
        return False
    # Stamp the installed line so it is clearly attributable to AppManager on
    # the bastion (offboarding still matches by the key blob, so the comment is
    # irrelevant to removal).
    stamped = sshkeys.stamp_public_key(
        public_key, f"AppManager-managed:{os_user}"
    )
    try:
        blob = stamped.split()[1]
    except IndexError:
        result.fail("user public key has an unexpected format")
        return False
    if not servers._KEY_BLOB_RE.match(blob):
        result.fail("user public key blob has an unexpected format")
        return False
    qu = shlex.quote(os_user)
    qk = shlex.quote(stamped)
    qb = shlex.quote(blob)
    # Create the account if missing, then rewrite authorized_keys atomically:
    # build the deduped content (minus any existing line for this blob) plus the
    # canonical stamped line in a temp file and rename it over the original, so
    # a mid-script death can never leave the file truncated (locking the user
    # out). ``grep -vF`` exits 1 when it selects nothing - tolerated.
    remote = "sh -c " + shlex.quote(
        "set -e; "
        f"id -u {qu} >/dev/null 2>&1 || useradd -m -s /bin/bash {qu}; "
        f"h=$(getent passwd {qu} | cut -d: -f6); "
        '[ -n "$h" ] || { echo no-home; exit 1; }; '
        'mkdir -p "$h/.ssh"; chmod 700 "$h/.ssh"; '
        'f="$h/.ssh/authorized_keys"; touch "$f"; t="$f.appmgr.tmp"; '
        f"{{ grep -vF {qb} \"$f\" || [ $? -eq 1 ]; }} > \"$t\"; "
        f"printf '%s\\n' {qk} >> \"$t\"; "
        'chmod 600 "$t"; mv "$t" "$f"; '
        f'chown -R {qu}: "$h/.ssh"; echo onboarded'
    )
    proc = _run_remote(config, remote)
    if proc.returncode == 0:
        result.log(f"{config.host}: onboarded OS user '{os_user}'")
        return True
    detail = (proc.stderr or proc.stdout or "").strip()[:200]
    result.fail(
        f"{config.host}: onboarding '{os_user}' failed "
        f"(rc={proc.returncode}): {detail}"
    )
    return False


def offboard_user(
    config: JumpConfig,
    *,
    os_user: str,
    public_key: str,
    result: ProxmoxResult,
) -> bool:
    """Remove the user's public key from their jump-host account.

    Removes only the key line (matched by base64 blob); the OS account and
    home directory are left intact. Idempotent.
    """
    if not servers._OS_USER_RE.match(os_user):
        result.fail(f"invalid OS username {os_user!r}")
        return False
    try:
        blob = public_key.split()[1]
    except IndexError:
        result.fail("user public key has an unexpected format")
        return False
    if not servers._KEY_BLOB_RE.match(blob):
        result.fail("user public key blob has an unexpected format")
        return False
    qu = shlex.quote(os_user)
    qb = shlex.quote(blob)
    remote = "sh -c " + shlex.quote(
        f"h=$(getent passwd {qu} | cut -d: -f6); "
        '[ -n "$h" ] || { echo no-user; exit 0; }; '
        'f="$h/.ssh/authorized_keys"; [ -f "$f" ] || { echo no-keys; exit 0; }; '
        f"if grep -qF {qb} \"$f\"; then "
        f"{{ grep -vF {qb} \"$f\" > \"$f.tmp\" || [ $? -eq 1 ]; }} && "
        'cat "$f.tmp" > "$f"; rm -f "$f.tmp"; fi; '
        f"grep -qF {qb} \"$f\" && exit 2; echo removed"
    )
    proc = _run_remote(config, remote)
    if proc.returncode == 0:
        result.log(f"{config.host}: removed key for OS user '{os_user}'")
        return True
    detail = (proc.stderr or proc.stdout or "").strip()[:200]
    result.fail(
        f"{config.host}: offboarding '{os_user}' failed "
        f"(rc={proc.returncode}): {detail}"
    )
    return False


def os_user_for(user: dict[str, Any]) -> str:
    """The OS username for a user (their derived user_id)."""
    return user.get("user_id") or repository.derive_user_id(
        user.get("username", "") or ""
    )


# ---------------------------------------------------------------------------
# Best-effort lifecycle wrappers (non-blocking; audited by the caller)
# ---------------------------------------------------------------------------


def sync_user(
    conn: sqlite3.Connection, user: dict[str, Any]
) -> tuple[str, str]:
    """Onboard a single user to the jump server if enabled.

    Returns ``(status, detail)`` where status is
    ``disabled|onboarded|failed|skipped``. Never raises - a failure here must
    not block user creation, deletion, or login.
    """
    try:
        config = load_config(conn)
        if not config.enabled:
            return "disabled", "jump server not enabled"
        if not config.ready:
            return "failed", "jump server is enabled but not fully configured"
        os_user = os_user_for(user)
        if not servers._OS_USER_RE.match(os_user or ""):
            return "skipped", f"derived OS username {os_user!r} is not valid"
        key = repository.get_user_ssh_key(conn, user["id"])
        public_key = (key or {}).get("public_key", "")
        result = ProxmoxResult()
        ok = onboard_user(
            config, os_user=os_user, public_key=public_key, result=result
        )
        last = result.steps[-1] if result.steps else ""
        return ("onboarded" if ok else "failed"), last
    except Exception as exc:  # noqa: BLE001 - best-effort, must not propagate
        return "failed", f"jump onboarding error: {exc.__class__.__name__}"


def remove_user(
    conn: sqlite3.Connection, *, os_user: str, public_key: str
) -> tuple[str, str]:
    """Remove a user's key from the jump server if enabled. Never raises."""
    try:
        config = load_config(conn)
        if not config.enabled:
            return "disabled", "jump server not enabled"
        if not config.ready:
            return "failed", "jump server is enabled but not fully configured"
        result = ProxmoxResult()
        ok = offboard_user(
            config, os_user=os_user, public_key=public_key, result=result
        )
        last = result.steps[-1] if result.steps else ""
        return ("removed" if ok else "failed"), last
    except Exception as exc:  # noqa: BLE001 - best-effort, must not propagate
        return "failed", f"jump offboarding error: {exc.__class__.__name__}"
