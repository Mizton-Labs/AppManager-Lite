"""User-server creation and lifecycle orchestration (issue_015 phase 3).

Coordinates Proxmox guest cloning, startup, IP discovery, resource
accounting, and optional SSH public-key installation on the new server.
Transcripts never contain secrets: the Proxmox token stays inside
``app.proxmox`` and only public keys are pushed over SSH.

``_run`` is the subprocess seam (mirrors ``reverse_proxy._run``); tests
monkeypatch it and the ``app.proxmox`` HTTP seam.
"""

from __future__ import annotations

import re
import shlex
import subprocess  # noqa: S404 - argv arrays only, shell=False
from typing import Any

from . import proxmox
from .proxmox import ProxmoxResult

_SSH_TIMEOUT = 20
_OS_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class ServerError(ValueError):
    """Locally detected validation problem (maps to a 400)."""


def validate_server_name(name: str) -> str:
    name = name.strip()
    if not _SERVER_NAME_RE.match(name):
        raise ServerError(
            "Server name must start with a letter or digit and may contain "
            "letters, digits, spaces, dots, dashes, and underscores "
            "(max 40 characters)."
        )
    return name


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
) -> bool:
    """Append ``public_key`` to authorized_keys for each OS user on the host.

    Idempotent: the key line is only added when absent. Only the public key
    travels; the admin key stays on this server (path passed to ssh -i).
    """
    if not os_users:
        result.log("No OS users requested for key installation; skipping")
        return True
    ok = True
    quoted_key = shlex.quote(public_key.strip())
    for os_user in os_users:
        if not _OS_USER_RE.match(os_user):  # defense in depth
            result.fail(f"invalid OS username {os_user!r}")
            return False
        quoted_user = shlex.quote(os_user)
        remote = (
            "sh -c "
            + shlex.quote(
                "set -e; "
                f"h=$(getent passwd {quoted_user} | cut -d: -f6); "
                '[ -n "$h" ] || { echo "no such user"; exit 1; }; '
                'mkdir -p "$h/.ssh"; chmod 700 "$h/.ssh"; '
                f"grep -qxF {quoted_key} \"$h/.ssh/authorized_keys\" "
                f"2>/dev/null || printf '%s\\n' {quoted_key} "
                '>> "$h/.ssh/authorized_keys"; '
                'chmod 600 "$h/.ssh/authorized_keys"; '
                f'chown -R {quoted_user}: "$h/.ssh"'
            )
        )
        proc = _run(_ssh_argv(admin_key_path, ip, remote))
        if proc.returncode == 0:
            result.log(f"Installed public key for OS user '{os_user}'")
        else:
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            result.fail(
                f"key installation for OS user '{os_user}' failed "
                f"(rc={proc.returncode}): {detail}"
            )
            ok = False
    return ok


_KEY_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=]+$")


def rotate_public_key(
    *,
    ip: str,
    admin_key_path: str,
    old_public_key: str,
    new_public_key: str,
    result: ProxmoxResult,
) -> str:
    """Replace the old public key with the new one on a server.

    Scans root's and every /home user's ``authorized_keys``, removes lines
    carrying the old key blob, and appends the new key to each file that had
    the old one. Matching is by the base64 key blob so comment changes do
    not matter. Verification (old gone, new present) happens inline, per
    file, in the same remote script.

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
    quoted_old = shlex.quote(old_blob)
    quoted_new = shlex.quote(new_public_key.strip())
    # NOTE: ``grep -v`` exits 1 when it selects no lines - the normal case
    # for a single-key authorized_keys file - so that status is tolerated.
    remote = "sh -c " + shlex.quote(
        "changed=''; fail=''; "
        "for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do "
        '[ -f "$f" ] || continue; '
        f"if grep -qF {quoted_old} \"$f\"; then "
        f"{{ grep -vF {quoted_old} \"$f\" > \"$f.tmp\" || [ $? -eq 1 ]; }} "
        '&& cat "$f.tmp" > "$f"; rm -f "$f.tmp"; '
        f"grep -qxF {quoted_new} \"$f\" || printf '%s\\n' {quoted_new} >> \"$f\"; "
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


def create_server(
    *,
    provider_config: dict[str, Any],
    template: dict[str, Any],
    name: str,
    owner_public_key: str,
    install_pubkey: bool,
    os_users: list[str],
) -> dict[str, Any]:
    """Clone a template into a new user server.

    Returns ``{status, transcript, vmid, node, kind, ip_address, resources}``.
    LXC guests are started and their IP read back; VM guests are cloned only
    (the operator configures them in Proxmox and enters the IP manually).
    Never raises for remote failures - the transcript carries the details.
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

    if install_pubkey:
        admin_key_path = (template.get("admin_ssh_key_path") or "").strip()
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
        ):
            outcome["transcript"] = result.transcript
            return outcome

    result.log("Server created successfully")
    outcome["status"] = "ok"
    outcome["transcript"] = result.transcript
    return outcome
