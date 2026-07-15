"""Jump-server user onboarding/offboarding (issue_015-r1 phase C).

When a jump server is configured, AppManager installs each user's SSH public
key on the bastion so they can jump onward to their servers. Two account models
are supported:

* ``per_user`` (default): each AppManager user gets their own hardened account
  on the bastion (named by their derived user_id), holding only their key.
* ``shared``: every user's key is installed into a single shared hardened
  account (``jump_jumper_user``).

In both models the accounts are hardened for jump-only use: the shell is
``nologin`` and each installed key line is prefixed with
``restrict,port-forwarding`` so it can be used only as a ``ProxyJump`` hop
(no TTY, shell, agent, or X11), which is exactly what the generated SSH config
needs.

Note on ``shared`` mode: hardening prevents an interactive shell, but it does
NOT scope *where* a key may TCP-forward. Because every user shares one account,
any user with a key in the shared account can open a forward to any host the
bastion can reach. Prefer ``per_user`` for multi-tenant deployments; use
``shared`` only for a single tenant or a trusted cohort.

AppManager connects to the bastion as ``jump_management_user`` (default root),
which must be privileged enough to create accounts and write authorized_keys.

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

# Per-key authorized_keys options: harden to a jump-only hop. ``restrict``
# disables pty/X11/agent/port-forwarding, then ``port-forwarding`` re-enables
# just forwarding so ProxyJump (-W) still works.
_HARDENED_KEY_OPTIONS = "restrict,port-forwarding"

ACCOUNT_MODES = ("per_user", "shared")


@dataclass
class JumpConfig:
    enabled: bool
    host: str
    key_path: str
    management_user: str = "root"
    account_mode: str = "per_user"
    jumper_user: str = ""
    port: int = 22
    # Legacy single "jump user" value, retained for reference/migration.
    user: str = ""

    @property
    def ready(self) -> bool:
        base = bool(
            self.enabled and self.host and self.management_user and self.key_path
        )
        if self.account_mode == "shared":
            return base and bool(self.jumper_user)
        return base


def load_config(conn: sqlite3.Connection) -> JumpConfig:
    row = repository.get_settings_row(conn)
    key_path = servers.resolve_ssh_key(conn, row.get("jump_ssh_key_id"))
    mode = (row.get("jump_account_mode") or "per_user").strip() or "per_user"
    if mode not in ACCOUNT_MODES:
        mode = "per_user"
    return JumpConfig(
        enabled=bool(row.get("jump_enabled", 0)),
        host=(row.get("jump_host") or "").strip(),
        key_path=key_path,
        management_user=(row.get("jump_management_user") or "root").strip()
        or "root",
        account_mode=mode,
        jumper_user=(row.get("jump_jumper_user") or "").strip(),
        port=int(row.get("jump_port", 22) or 22),
        user=(row.get("jump_user") or "").strip(),
    )


def target_account(config: JumpConfig, user: dict[str, Any]) -> str:
    """The bastion OS account a user's key is installed into for this mode."""
    if config.account_mode == "shared":
        return config.jumper_user
    return os_user_for(user)


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
        f"{config.management_user}@{config.host}",
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
    stamp_id: str = "",
    remove_markers: list[str] | None = None,
) -> bool:
    """Create the hardened jump account (if missing) and install the key.

    Idempotent. Connects as the management user (must be able to create
    accounts). The account is created with a ``nologin`` shell and the key line
    is prefixed with hardened options so it is usable only as a ``ProxyJump``
    hop. Only the public key travels. ``os_user`` is the bastion account the key
    is installed into; ``stamp_id`` (defaulting to ``os_user``) identifies the
    owning AppManager user in the key comment, so a shared account still records
    per-user provenance.
    """
    if not servers._OS_USER_RE.match(os_user):
        result.fail(f"invalid OS username {os_user!r}")
        return False
    if not public_key.strip():
        result.fail("user has no SSH public key")
        return False
    # Stamp the installed line so it is clearly attributable to the owning user
    # on the bastion (offboarding still matches by the key blob, so the comment
    # is irrelevant to removal), and prefix hardened options to make the key
    # jump-only.
    stamped = sshkeys.stamp_public_key(
        public_key, f"AppManager-managed:{stamp_id or os_user}"
    )
    try:
        blob = stamped.split()[1]
    except IndexError:
        result.fail("user public key has an unexpected format")
        return False
    if not servers._KEY_BLOB_RE.match(blob):
        result.fail("user public key blob has an unexpected format")
        return False
    hardened_line = f"{_HARDENED_KEY_OPTIONS} {stamped}"
    qu = shlex.quote(os_user)
    qk = shlex.quote(hardened_line)
    qb = shlex.quote(blob)
    marker = f"AppManager-managed:{stamp_id or os_user}"
    markers = [value for value in (remove_markers or [marker]) if value]
    awk_args = " ".join(
        f"-v m{index}={shlex.quote(value)}"
        for index, value in enumerate(markers, start=1)
    )
    marker_conditions = " && ".join(
        f"$NF != m{index}" for index in range(1, len(markers) + 1)
    ) or "1"
    # Create the account (hardened, nologin shell) if missing, then rewrite
    # authorized_keys atomically: build the deduped content (minus any existing
    # line for this blob) plus the canonical hardened+stamped line in a temp
    # file and rename it over the original, so a mid-script death can never
    # leave the file truncated (locking the user out). ``grep -vF`` exits 1 when
    # it selects nothing - tolerated.
    remote = "sh -c " + shlex.quote(
        "set -e; "
        "nologin=$(command -v nologin || echo /usr/sbin/nologin); "
        f"id -u {qu} >/dev/null 2>&1 || "
        f'useradd -m -s "$nologin" {qu}; '
        f"h=$(getent passwd {qu} | cut -d: -f6); "
        '[ -n "$h" ] || { echo no-home; exit 1; }; '
        'mkdir -p "$h/.ssh"; chmod 700 "$h/.ssh"; '
        'f="$h/.ssh/authorized_keys"; touch "$f"; t="$f.appmgr.tmp"; '
        f"awk -v b={qb} {awk_args} "
        + shlex.quote(f"index($0, b) == 0 && {marker_conditions}")
        + ' "$f" > "$t"; '
        f"printf '%s\\n' {qk} >> \"$t\"; "
        'chmod 600 "$t"; mv "$t" "$f"; '
        f'chown -R {qu}: "$h/.ssh"; '
        f"grep -Fqx {qk} \"$f\" >/dev/null; echo onboarded"
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
        owner_id = os_user_for(user)
        account = target_account(config, user)
        if not servers._OS_USER_RE.match(account or ""):
            return "skipped", f"jump account {account!r} is not a valid username"
        if not servers._OS_USER_RE.match(owner_id or ""):
            return "skipped", f"derived OS username {owner_id!r} is not valid"
        key = repository.get_user_ssh_key(conn, user["id"])
        public_key = (key or {}).get("public_key", "")
        result = ProxmoxResult()
        ok = onboard_user(
            config, os_user=account, public_key=public_key, result=result,
            stamp_id=f"user-{user['id']}",
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
