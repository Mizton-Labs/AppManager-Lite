"""User-server creation and lifecycle orchestration (issue_015 phase 3).

Coordinates Proxmox guest cloning, startup, IP discovery, resource
accounting, and optional SSH public-key installation on the new server.
Transcripts never contain secrets: the Proxmox token stays inside
``app.proxmox`` and only public keys are pushed over SSH.

``_run`` is the subprocess seam (mirrors ``reverse_proxy._run``); tests
monkeypatch it and the ``app.proxmox`` HTTP seam.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess  # noqa: S404 - argv arrays only, shell=False
import sqlite3
from typing import Any

from . import keystore, proxmox, repository, sshkeys
from .config import get_settings
from .proxmox import ProxmoxResult

_SSH_TIMEOUT = 20
_OS_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# Server display names are capped at the DNS hostname length (63) so a fully
# composed name (template-userid-suffix) still fits both the record and the
# derived hostname.
MAX_SERVER_NAME_LEN = 63
_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,62}$")
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")

# Login shells install_public_key is allowed to set/create an account with.
# Never attacker/admin-free-text; always one of these literals.
_ALLOWED_ACCOUNT_SHELLS = ("/bin/bash", "/bin/sh")
# The shell a server's main OS user account should default to: created with
# it when the account doesn't yet exist, and normalized to it otherwise (only
# when the binary is present on the remote host).
DEFAULT_ACCOUNT_SHELL = "/bin/bash"


class ServerError(ValueError):
    """Locally detected validation problem (maps to a 400)."""


def resolve_ssh_key(
    conn: sqlite3.Connection, key_id: int | None, *, fallback_path: str = ""
) -> str:
    """Return a filesystem path to the private key for ``ssh -i``.

    - Registry ``path`` key: returns the stored path.
    - Registry ``stored`` key: decrypts and materializes it under
      ``data/keys/ssh-key-<id>`` at 0600, then returns that path.
    - No/unknown key id: returns ``fallback_path`` (legacy behavior).
    """
    if key_id is not None:
        key = repository.get_ssh_key(conn, key_id)
        if key is not None:
            if key["kind"] == "path":
                return key["path"]
            secret = repository.get_ssh_key_secret(conn, key_id)
            if secret:
                return _materialize_stored_key(key_id, keystore.decrypt(secret))
    return fallback_path


def _materialize_stored_key(key_id: int, private_key: str) -> str:
    settings = get_settings()
    settings.ensure_dirs()
    keys_dir = settings.ssh_keys_dir
    keys_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(keys_dir, 0o700)
    except OSError:
        pass
    dest = keys_dir / f"ssh-key-{key_id}"
    data = private_key if private_key.endswith("\n") else private_key + "\n"
    # Create 0600 atomically (truncate if present) so there is no
    # world-readable window before chmod.
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)
    return str(dest)


def remove_materialized_key(key_id: int) -> None:
    """Delete any on-disk materialized copy of a stored key (best effort)."""
    dest = get_settings().ssh_keys_dir / f"ssh-key-{key_id}"
    try:
        dest.unlink()
    except OSError:
        pass


def validate_server_name(name: str) -> str:
    name = name.strip()
    if not _SERVER_NAME_RE.match(name):
        raise ServerError(
            "Server name must start with a letter or digit and may contain "
            "letters, digits, spaces, dots, dashes, and underscores "
            f"(max {MAX_SERVER_NAME_LEN} characters)."
        )
    return name


def server_name_prefix(template_name: str, derived_uid: str) -> str:
    """The static "<template>-<owner-id>-" prefix every server name carries.

    The template portion is slugified to the server-name charset (lowercase
    alphanumerics and dashes) so an arbitrarily-named template (spaces, unicode,
    punctuation) can never compose an invalid full name. The derived user id is
    already slug-safe. The exact same construction is mirrored in the frontend
    so the live preview matches what is stored.
    """
    tpl_slug = re.sub(r"[^a-z0-9]+", "-", (template_name or "").lower()).strip("-")
    uid = (derived_uid or "").strip()
    return f"{tpl_slug or 'server'}-{uid}-"


def hostname_for(name: str) -> str:
    """A DNS-safe hostname derived from the display name."""
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return slug[:63] or "server"


def validate_ip(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not _IP_RE.match(value) or any(
        int(part) > 255 for part in value.split(".")
    ):
        raise ServerError("IP address must be a valid IPv4 address.")
    return value


def parse_os_users(raw: str) -> list[str]:
    """Comma-separated OS usernames that receive the public key."""
    users = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if not _OS_USER_RE.match(item):
            raise ServerError(
                f"Invalid OS username {item!r}: lowercase letters, digits, "
                "dashes, and underscores only."
            )
        users.append(item)
    return users


def _run(argv: list[str], *, timeout: int = _SSH_TIMEOUT):
    """Subprocess seam; identical contract to ``reverse_proxy._run``."""
    try:
        return subprocess.run(  # noqa: S603 - argv array, shell=False
            argv, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, returncode=124, stdout="", stderr="command timed out"
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            argv, returncode=127, stdout="", stderr="command not found"
        )


def _ssh_argv(key_path: str, ip: str, remote_command: str) -> list[str]:
    return [
        "ssh",
        "-i",
        key_path,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=10",
        f"root@{ip}",
        remote_command,
    ]


def install_public_key(
    *,
    ip: str,
    admin_key_path: str,
    os_users: list[str],
    public_key: str,
    result: ProxmoxResult,
    enable_sudo: bool = False,
    marker: str = "",
    ensure_account_shell: str | None = None,
) -> bool:
    """Append ``public_key`` to authorized_keys for each OS user on the host.

    Idempotent: any existing line carrying the same key blob is removed first,
    then a single canonical line is appended, so re-runs converge to exactly one
    entry (and an old differently-commented copy is replaced rather than
    duplicated). Only the public key travels; the admin key stays on this server
    (path passed to ssh -i). When ``enable_sudo`` is set, each OS user is added
    to the sudo/wheel group. When ``marker`` is set, the installed line's
    comment is rewritten to it (e.g. ``AppManager-managed:<user_id>``) so the
    key is clearly attributable to AppManager on the remote host.

    ``ensure_account_shell``, when set to one of ``_ALLOWED_ACCOUNT_SHELLS``,
    changes the default strict behavior (fail if the OS user doesn't exist):
    the account is created (``useradd -m``) with that login shell if missing,
    or its shell is normalized to it if it differs (only when the shell binary
    is present on the remote host). Leave unset (the default) to preserve the
    original "account must already exist" behavior, e.g. for the trusted mesh
    which only ever targets accounts a prior call already ensured.
    """
    if not os_users:
        result.log("No OS users requested for key installation; skipping")
        return True
    if (
        ensure_account_shell is not None
        and ensure_account_shell not in _ALLOWED_ACCOUNT_SHELLS
    ):
        result.fail(f"unsupported account shell {ensure_account_shell!r}")
        return False
    stamped = sshkeys.stamp_public_key(public_key, marker) if marker else (
        public_key.strip()
    )
    try:
        blob = stamped.split()[1]
    except IndexError:
        result.fail("public key to install has an unexpected format")
        return False
    if not _KEY_BLOB_RE.match(blob):
        result.fail("public key blob has an unexpected format")
        return False
    ok = True
    quoted_key = shlex.quote(stamped)
    quoted_blob = shlex.quote(blob)
    for os_user in os_users:
        if not _OS_USER_RE.match(os_user):  # defense in depth
            result.fail(f"invalid OS username {os_user!r}")
            return False
        quoted_user = shlex.quote(os_user)
        # Add-to-sudo step: try the Debian 'sudo' group then RHEL 'wheel';
        # tolerate absence of either group so key install still succeeds.
        sudo_step = (
            f"usermod -aG sudo {quoted_user} 2>/dev/null || "
            f"usermod -aG wheel {quoted_user} 2>/dev/null || "
            'echo "warning: could not add to sudo/wheel group"; '
            if enable_sudo
            else ""
        )
        # Ensure the account exists (creating it with the requested login
        # shell) or normalize an existing account's shell, only when a shell
        # was requested; otherwise the account must already exist (unchanged
        # strict behavior).
        shell_warning = (
            f"warning: shell {ensure_account_shell} not present on this host; "
            "shell left as-is"
        )
        if ensure_account_shell is not None:
            quoted_shell = shlex.quote(ensure_account_shell)
            quoted_warning = shlex.quote(shell_warning)
            ensure_step = (
                f"h=$(getent passwd {quoted_user} | cut -d: -f6); "
                'if [ -z "$h" ]; then '
                f"  if [ -x {quoted_shell} ]; then "
                f"    useradd -m -s {quoted_shell} {quoted_user}; "
                "  else "
                f"    useradd -m {quoted_user}; "
                f"    echo {quoted_warning}; "
                "  fi; "
                f"  h=$(getent passwd {quoted_user} | cut -d: -f6); "
                "else "
                f"  if [ -x {quoted_shell} ]; then "
                f"    cur=$(getent passwd {quoted_user} | cut -d: -f7); "
                f'    [ "$cur" = {quoted_shell} ] || '
                f"      usermod -s {quoted_shell} {quoted_user}; "
                "  else "
                f"    echo {quoted_warning}; "
                "  fi; "
                "fi; "
                '[ -n "$h" ] || { echo "no such user"; exit 1; }; '
            )
        else:
            ensure_step = (
                f"h=$(getent passwd {quoted_user} | cut -d: -f6); "
                '[ -n "$h" ] || { echo "no such user"; exit 1; }; '
            )
        # Rewrite authorized_keys atomically: build the deduped content (minus
        # any existing line for this blob) plus the canonical stamped line in a
        # temp file, then rename over the original. A mid-script death can never
        # leave the file truncated/empty (which would lock the user out).
        remote = (
            "sh -c "
            + shlex.quote(
                "set -e; "
                + ensure_step
                + 'mkdir -p "$h/.ssh"; chmod 700 "$h/.ssh"; '
                'f="$h/.ssh/authorized_keys"; touch "$f"; t="$f.appmgr.tmp"; '
                f"{{ grep -vF {quoted_blob} \"$f\" || [ $? -eq 1 ]; }} > \"$t\"; "
                f"printf '%s\\n' {quoted_key} >> \"$t\"; "
                'chmod 600 "$t"; mv "$t" "$f"; '
                f'chown -R {quoted_user}: "$h/.ssh"; '
                + sudo_step
                + "true"
            )
        )
        proc = _run(_ssh_argv(admin_key_path, ip, remote))
        if proc.returncode == 0:
            msg = f"Installed public key for OS user '{os_user}'"
            if enable_sudo:
                msg += " (sudo access enabled)"
            if ensure_account_shell is not None and shell_warning in (
                proc.stdout or ""
            ):
                msg += f"; {shell_warning}"
            result.log(msg)
        else:
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            result.fail(
                f"key installation for OS user '{os_user}' failed "
                f"(rc={proc.returncode}): {detail}"
            )
            ok = False
    return ok


_KEY_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=]+$")


def _ensure_local_key_and_read_pub(
    *, ip: str, admin_key_path: str, os_user: str, result: ProxmoxResult
) -> str | None:
    """Generate a keypair for ``os_user`` on the server if absent; return pub.

    The private key is generated *on the server* and never leaves it (nothing
    is stored in the app). Returns the OpenSSH public key line, or None on
    failure.
    """
    quoted_user = shlex.quote(os_user)
    # The keygen runs as the target user via `su`; the inner command is
    # single-quoted so $HOME is expanded by that inner shell (never string-
    # spliced from the outer shell), foreclosing injection via the passwd
    # home-dir field. Every use of $h below is double-quoted.
    remote = "sh -c " + shlex.quote(
        "set -e; "
        f"h=$(getent passwd {quoted_user} | cut -d: -f6); "
        '[ -n "$h" ] || { echo "no such user"; exit 1; }; '
        'mkdir -p "$h/.ssh"; chmod 700 "$h/.ssh"; '
        'if [ ! -f "$h/.ssh/id_ed25519" ]; then '
        f"su -s /bin/sh {quoted_user} "
        "-c 'ssh-keygen -t ed25519 -N \"\" -f \"$HOME/.ssh/id_ed25519\" -q' "
        '|| ssh-keygen -t ed25519 -N "" -f "$h/.ssh/id_ed25519" -q; '
        'fi; '
        f'chown -R {quoted_user}: "$h/.ssh"; '
        'cat "$h/.ssh/id_ed25519.pub"'
    )
    proc = _run(_ssh_argv(admin_key_path, ip, remote))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        result.fail(f"{ip}: keygen for '{os_user}' failed: {detail}")
        return None
    pub = (proc.stdout or "").strip().splitlines()
    pub = [ln for ln in pub if ln.startswith("ssh-")]
    if not pub:
        result.fail(f"{ip}: could not read generated public key for '{os_user}'")
        return None
    return pub[-1]


def reconcile_trusted_mesh(
    *,
    servers: list[dict[str, Any]],
    admin_key_path: str,
    os_user: str,
    result: ProxmoxResult,
) -> bool:
    """Establish a full SSH mesh across the user's trusted servers.

    For each server: ensure the main user has a locally-generated keypair and
    collect its public key. Then install every collected public key into every
    server's main-user authorized_keys. Private keys are generated on and stay
    on the servers; the app only relays public keys. Idempotent.

    ``servers`` is a list of dicts with an ``ip_address`` (reachable ones
    only). Returns True when the mesh was fully applied.
    """
    reachable = [s for s in servers if s.get("ip_address")]
    if len(reachable) < 2:
        result.log(
            "Trusted access: fewer than two reachable servers; nothing to mesh"
        )
        return True
    if not _OS_USER_RE.match(os_user):
        result.fail(f"trusted mesh: invalid OS username {os_user!r}")
        return False

    # 1. Collect each server's public key (generating one if needed).
    pubkeys: dict[str, str] = {}
    for srv in reachable:
        ip = srv["ip_address"]
        pub = _ensure_local_key_and_read_pub(
            ip=ip, admin_key_path=admin_key_path, os_user=os_user, result=result
        )
        if pub is None:
            return False
        pubkeys[ip] = pub

    # 2. Install every collected pubkey into every server's authorized_keys.
    ok = True
    for srv in reachable:
        ip = srv["ip_address"]
        for source_ip, pub in pubkeys.items():
            if source_ip == ip:
                continue  # a server does not need its own key installed
            if not install_public_key(
                ip=ip,
                admin_key_path=admin_key_path,
                os_users=[os_user],
                public_key=pub,
                result=result,
                marker=f"AppManager-trusted:{os_user}",
            ):
                ok = False
    if ok:
        result.log(
            f"Trusted access mesh established across {len(reachable)} servers "
            f"for OS user '{os_user}'"
        )
    return ok


def rotate_public_key(
    *,
    ip: str,
    admin_key_path: str,
    old_public_key: str,
    new_public_key: str,
    result: ProxmoxResult,
    marker: str = "",
) -> str:
    """Replace the old public key with the new one on a server.

    Scans root's and every /home user's ``authorized_keys``, removes lines
    carrying the old key blob, and appends the new key to each file that had
    the old one. Matching is by the base64 key blob so comment changes do
    not matter. Verification (old gone, new present) happens inline, per
    file, in the same remote script. When ``marker`` is set, the appended new
    line's comment is rewritten to it (e.g. ``AppManager-managed:<user_id>``).

    Returns ``"updated"``, ``"noop"`` (old key not present anywhere), or
    ``"failed"``.
    """
    try:
        old_blob = old_public_key.split()[1]
    except IndexError:
        result.fail("stored public key has an unexpected format")
        return "failed"
    if not _KEY_BLOB_RE.match(old_blob):
        result.fail("stored public key blob has an unexpected format")
        return "failed"
    new_line = sshkeys.stamp_public_key(new_public_key, marker) if marker else (
        new_public_key.strip()
    )
    quoted_old = shlex.quote(old_blob)
    quoted_new = shlex.quote(new_line)
    # NOTE: ``grep -v`` exits 1 when it selects no lines - the normal case
    # for a single-key authorized_keys file - so that status is tolerated.
    # Each file is rewritten atomically (build in a temp file, then rename) so a
    # mid-script death can never leave authorized_keys truncated.
    remote = "sh -c " + shlex.quote(
        "changed=''; fail=''; "
        "for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do "
        '[ -f "$f" ] || continue; '
        f"if grep -qF {quoted_old} \"$f\"; then "
        't="$f.appmgr.tmp"; '
        f"{{ grep -vF {quoted_old} \"$f\" || [ $? -eq 1 ]; }} > \"$t\"; "
        f"grep -qxF {quoted_new} \"$t\" || printf '%s\\n' {quoted_new} >> \"$t\"; "
        'chmod 600 "$t"; mv "$t" "$f"; '
        f"if grep -qF {quoted_old} \"$f\"; then fail=\"$fail $f(old-present)\"; fi; "
        f"grep -qF {quoted_new} \"$f\" || fail=\"$fail $f(new-missing)\"; "
        'changed="$changed $f"; '
        "fi; "
        "done; "
        '[ -z "$fail" ] || { echo "verify-failed:$fail"; exit 2; }; '
        'echo "updated:$changed"'
    )
    proc = _run(_ssh_argv(admin_key_path, ip, remote))
    if proc.returncode == 0:
        files = (proc.stdout or "").strip().removeprefix("updated:").strip()
        if files:
            result.log(f"{ip}: key rotated in: {files}")
            return "updated"
        result.log(f"{ip}: old key not present; nothing to rotate")
        return "noop"
    detail = (proc.stderr or proc.stdout or "").strip()[:200]
    result.fail(f"{ip}: key rotation failed (rc={proc.returncode}): {detail}")
    return "failed"


# IPv4 addresses that are never a usable "real" guest address: loopback and
# link-local. `ip ... scope global` already excludes these, but hostname -I
# does not, so the parser filters them defensively. The filter is deliberately
# minimal (it does not reject 0.0.0.0, multicast, broadcast, or CGNAT) because
# it is not the security boundary: a discovered address is adopted only when the
# hypervisor independently attributes it to the same guest (see
# read_ip_from_guest / proxmox.list_lxc_ips), so a compromised guest cannot make
# AppManager record or connect to an address the hypervisor does not see on the
# guest's own interfaces.
def _is_usable_ipv4(addr: str) -> bool:
    if not _IP_RE.match(addr):
        return False
    octets = [int(o) for o in addr.split(".")]
    if any(o > 255 for o in octets):
        return False
    if octets[0] == 127:  # loopback
        return False
    if octets[0] == 169 and octets[1] == 254:  # link-local
        return False
    return True


def _pick_guest_ip(ip_output: str, hostname_output: str) -> str:
    """Pick the guest's real IPv4 from remote command output.

    ``ip_output`` is ``ip -4 -o addr show scope global`` (preferred: already
    global-scope, ordered by interface index so the primary NIC comes first).
    ``hostname_output`` is ``hostname -I`` (fallback: space-separated list,
    may include IPv6 and link-local). The first usable IPv4 wins.
    """
    # `ip -o addr` lines look like:
    #   2: eth0    inet 10.10.50.12/24 brd ... scope global eth0
    for line in ip_output.splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        idx = parts.index("inet")
        # Guard against a malformed line where `inet` is the final token.
        cidr = parts[idx + 1] if idx + 1 < len(parts) else ""
        addr = cidr.split("/", 1)[0]
        if _is_usable_ipv4(addr):
            return addr
    # Fallback: hostname -I is a space-separated list of all addresses.
    for token in hostname_output.split():
        if _is_usable_ipv4(token):
            return token
    return ""


def read_ip_from_guest(
    *,
    ip: str,
    admin_key_path: str,
    corroborating_ips: set[str],
    result: ProxmoxResult,
) -> str:
    """Read the guest's real in-guest IPv4 over SSH (via the admin key).

    Connects as root using ``admin_key_path`` and asks the guest what address
    it actually holds (``ip -4 -o addr show scope global``, falling back to
    ``hostname -I``) rather than trusting the hypervisor's single reported
    address. Returns the discovered IPv4, or ``""`` if the key is missing, the
    read fails, or the report cannot be trusted -- in which case the caller
    keeps the hypervisor-reported IP.

    ``ip`` is the address used to reach the guest for this one read (the
    hypervisor-reported IP). ``corroborating_ips`` is the set of addresses the
    hypervisor independently attributes to this guest.

    Security: a container's OS is effectively controlled by its owner (who can
    be root inside it), so the guest can report any address. A discovered
    address is therefore adopted ONLY when the hypervisor also attributes it to
    this guest (it is in ``corroborating_ips``). This prevents a malicious guest
    from steering AppManager's later root-SSH operations (key rotation, trusted
    mesh) or the user's generated SSH config to an attacker-chosen IP. An
    uncorroborated report is ignored and the hypervisor address is kept.
    """
    if not admin_key_path:
        return ""
    if not _IP_RE.match(ip):
        return ""
    # Best-effort, read-only: try `ip` first, then `hostname -I`, printing a
    # tagged section for each so the two outputs are parsed independently.
    remote = "sh -c " + shlex.quote(
        "echo '---ip---'; "
        "ip -4 -o addr show scope global 2>/dev/null || true; "
        "echo '---hostname---'; "
        "hostname -I 2>/dev/null || true"
    )
    proc = _run(_ssh_argv(admin_key_path, ip, remote))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        result.log(
            f"WARNING: could not read in-guest IP (rc={proc.returncode}): "
            f"{detail}; keeping hypervisor-reported address"
        )
        return ""
    out = proc.stdout or ""
    ip_section, _, rest = out.partition("---hostname---")
    ip_section = ip_section.partition("---ip---")[2]
    discovered = _pick_guest_ip(ip_section, rest)
    if not discovered:
        result.log(
            "WARNING: in-guest IP read returned no usable address; keeping "
            "hypervisor-reported address"
        )
        return ""
    if discovered == ip:
        result.log(f"Confirmed in-guest IP {discovered}")
        return discovered
    # The guest reports a different address than the hypervisor's primary one.
    # Adopt it only if the hypervisor independently attributes it to this guest;
    # otherwise the report is untrusted and the hypervisor address is kept.
    if discovered in corroborating_ips:
        result.log(
            f"In-guest IP {discovered} differs from hypervisor-reported {ip} "
            f"and is corroborated by the hypervisor; recording {discovered}"
        )
        return discovered
    result.log(
        f"In-guest IP {discovered} is not corroborated by the hypervisor "
        f"(known: {sorted(corroborating_ips) or 'none'}); keeping {ip}"
    )
    return ""


def destroy_server(
    *,
    provider_config: dict[str, Any],
    node: str,
    vmid: int | None,
    kind: str,
) -> dict[str, Any]:
    """Stop and destroy a guest for the deferred-deletion path.

    Returns ``{status, transcript}``. ``status`` is ``"ok"`` when the guest is
    gone (destroyed now, or already absent), otherwise ``"failed"`` with the
    failure recorded in the transcript. Records with no ``vmid`` (references or
    never-cloned failures) have nothing to destroy and succeed immediately.
    Never raises - the transcript carries the details for the caller to persist.
    """
    result = ProxmoxResult()
    if vmid is None:
        result.log("No guest was ever provisioned; nothing to destroy")
        return {"status": "ok", "transcript": result.transcript}
    result.log(f"Destroying {kind} guest {vmid} on node {node or '?'}")
    if not node:
        # Without a node we cannot address the guest; try to locate it.
        found = proxmox.find_guest(provider_config, vmid, result=ProxmoxResult())
        if found is not None:
            node = found["node"]
            kind = found["kind"]
        else:
            result.log(
                f"guest {vmid} was not found on the cluster; treating as "
                "already destroyed"
            )
            return {"status": "ok", "transcript": result.transcript}
    # Force-stop first (a running guest cannot be destroyed), then destroy.
    if not proxmox.stop_guest(provider_config, node, vmid, kind, result=result):
        return {"status": "failed", "transcript": result.transcript}
    if not proxmox.destroy_guest(
        provider_config, node, vmid, kind, result=result
    ):
        return {"status": "failed", "transcript": result.transcript}
    result.log(f"Guest {vmid} destroyed")
    return {"status": "ok", "transcript": result.transcript}


def create_server(
    *,
    provider_config: dict[str, Any],
    template: dict[str, Any],
    name: str,
    owner_public_key: str,
    install_pubkey: bool,
    os_users: list[str],
    admin_key_path: str | None = None,
    enable_sudo: bool = False,
    owner_marker: str = "",
    ensure_account_shell: str | None = None,
) -> dict[str, Any]:
    """Clone a template into a new user server.

    Returns ``{status, transcript, vmid, node, kind, ip_address, resources}``.
    LXC guests are started and their IP read back; VM guests are cloned only
    (the operator configures them in Proxmox and enters the IP manually).
    Never raises for remote failures - the transcript carries the details.

    ``ensure_account_shell``, when set, is forwarded to ``install_public_key``
    so the target OS account is created/normalized with that login shell.
    Callers must only pass this when ``os_users`` is confidently "the server's
    main OS user" (e.g. a template-configured ``main_os_user``) — never for a
    caller-supplied/free-form user list, since that would let a non-admin
    self-service request auto-create arbitrary OS accounts.
    """
    result = ProxmoxResult()
    outcome: dict[str, Any] = {
        "status": "failed",
        "transcript": "",
        "vmid": None,
        "node": "",
        "kind": template["kind"],
        "ip_address": "",
        "resources": None,
    }
    hostname = hostname_for(name)
    result.log(f"Creating server '{name}' (hostname {hostname}) "
               f"from template '{template['name']}' (vmid {template['vmid']})")

    new_vmid = proxmox.next_vmid(provider_config, result=result)
    if new_vmid is None:
        outcome["transcript"] = result.transcript
        return outcome

    cloned = proxmox.clone_guest(
        provider_config,
        source_vmid=template["vmid"],
        new_vmid=new_vmid,
        name=hostname,
        result=result,
    )
    if cloned is None:
        outcome["transcript"] = result.transcript
        return outcome
    outcome["vmid"] = new_vmid
    outcome["node"] = cloned["node"]
    outcome["kind"] = cloned["kind"]

    resources = proxmox.get_guest_resources(
        provider_config, cloned["node"], new_vmid, cloned["kind"], result=result
    )
    if resources is not None:
        outcome["resources"] = resources
    else:
        # Resource read failures are not fatal; accounting falls back to 0.
        result.status = "ok"
        result.log("WARNING: could not read guest resources; recorded as 0")

    if cloned["kind"] == "vm":
        if install_pubkey:
            result.log(
                "NOTE: SSH key installation is skipped for VMs; install the "
                "key after configuring the VM and entering its IP."
            )
        result.log(
            "VM created but not started: configure it in Proxmox and enter "
            "its IP address here afterwards."
        )
        outcome["status"] = "ok"
        outcome["transcript"] = result.transcript
        return outcome

    if not proxmox.start_guest(
        provider_config, cloned["node"], new_vmid, "lxc", result=result
    ):
        outcome["transcript"] = result.transcript
        return outcome

    ip = proxmox.get_lxc_ip(
        provider_config, cloned["node"], new_vmid, result=result
    )
    outcome["ip_address"] = ip
    if not ip:
        outcome["transcript"] = result.transcript
        return outcome

    # Resolve the admin key once (registry path from the caller, else the
    # template's legacy path column). It is used both to read the real
    # in-guest IP and to install the owner's key.
    if admin_key_path is None:
        admin_key_path = (template.get("admin_ssh_key_path") or "").strip()
    admin_key_path = (admin_key_path or "").strip()

    # Ask the guest what address it actually holds (DHCP/network IP), reaching
    # it at the hypervisor-reported address. The in-guest answer is only
    # adopted when the hypervisor independently attributes it to this guest, so
    # a compromised guest cannot steer later root-SSH operations to an
    # attacker-chosen address. If the read is unavailable (no admin key) or
    # fails, or the report is uncorroborated, the hypervisor IP is kept.
    corroborating_ips = proxmox.list_lxc_ips(
        provider_config, cloned["node"], new_vmid, result=result
    )
    corroborating_ips.add(ip)  # the hypervisor's primary report corroborates itself
    discovered = read_ip_from_guest(
        ip=ip,
        admin_key_path=admin_key_path,
        corroborating_ips=corroborating_ips,
        result=result,
    )
    if discovered:
        ip = discovered
        outcome["ip_address"] = ip

    if install_pubkey:
        if not admin_key_path:
            result.log(
                "WARNING: key installation requested but the template has "
                "no admin SSH key path; skipping"
            )
        elif not owner_public_key:
            result.log(
                "WARNING: the owner has no SSH public key; skipping key "
                "installation"
            )
        elif not install_public_key(
            ip=ip,
            admin_key_path=admin_key_path,
            os_users=os_users,
            public_key=owner_public_key,
            result=result,
            enable_sudo=enable_sudo,
            marker=owner_marker or "AppManager-managed",
            ensure_account_shell=ensure_account_shell,
        ):
            outcome["transcript"] = result.transcript
            return outcome

    result.log("Server created successfully")
    outcome["status"] = "ok"
    outcome["transcript"] = result.transcript
    return outcome
