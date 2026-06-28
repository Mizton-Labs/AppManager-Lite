"""Reverse-proxy (nginx) configuration support.

This module owns the default alias template and the logic to render and push
application aliases to a remote nginx server over SSH.

The alias template is a plain nginx ``location`` block. When an alias is pushed,
the placeholders ``APPS_SERVER``, ``APPS_PORT`` and ``ALIAS`` are substituted
(plus ``APPNAME``/``TIMESTAMP`` in the comment header). ``APPS_SERVER`` is the
server where the owning user runs their applications.

SSH/scp are invoked via the system binaries with ``shell=False`` argument lists
(no shell, key-based auth, ``BatchMode=yes`` and explicit timeouts). The single
command-running seam (``_run``) is mocked in tests; no real network access is
performed by the unit tests.

The private SSH key is never read or logged here -- only the path to a key file
is used (passed to ``ssh -i``). Substituted values (alias/host/port) are strictly
validated before they ever reach the remote file or a command.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# Validation for values substituted into the template / remote commands.
_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_PORT_RE = re.compile(r"^[0-9]{1,5}$")
_SSH_USER_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Common ssh options: key-based, non-interactive, bounded.
_SSH_TIMEOUT = 20  # seconds per remote command


def app_marker(app_id: int) -> tuple[str, str]:
    """Return the (begin, end) sentinel comment lines that wrap an app's alias
    block in the nginx config, so it can be removed surgically on delete.

    Keyed off the immutable application id, so the marker is stable, unique, and
    never derived from user input.
    """
    token = f"appmanager-lite-app:{int(app_id)}"
    return (f"# >>> {token} >>>", f"# <<< {token} <<<")


PROXY_AUTH_BEGIN = "# >>> appmanager-lite-proxy-auth >>>"
PROXY_AUTH_END = "# <<< appmanager-lite-proxy-auth <<<"

# Default alias template seeded into the settings table. Administrators may edit
# it in General Settings. Tabs/indentation are preserved verbatim so the
# rendered nginx block matches operator expectations.
DEFAULT_ALIAS_TEMPLATE = """\
\t#############################################################################################
    ### APPNAME / ALIAS / TIMESTAMP
    #--------------------------------------------------------------------------------------------
\tlocation = /ALIAS {
\t\treturn 301 /ALIAS/;
\t}
\tlocation /ALIAS/ {
\t\tauth_request /api/auth/proxy-check;
\t\terror_page 401 = @appmanager_login;
\t\tproxy_pass http://APPS_SERVER:APPS_PORT/;
\t\tproxy_read_timeout 7200s;
\t\tproxy_next_upstream error timeout invalid_header http_500 http_502 http_503 http_504;
\t\tproxy_buffering off;
\t\tproxy_redirect off;
\t\tproxy_set_header Host $host;
\t\tproxy_set_header X-Real-IP $remote_addr;
\t\tproxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
\t\tproxy_set_header X-Forwarded-Proto $scheme;
\t\tproxy_set_header X-Script-Name /ALIAS;
\t\tproxy_http_version 1.1;
\t\tproxy_set_header Upgrade $http_upgrade;
\t\tproxy_set_header Connection "upgrade";
\t\tclient_max_body_size 30G;
\t}
    #--------------------------------------------------------------------------------------------
\t#############################################################################################
"""


def render_proxy_auth_block(*, appmanager_host: str, appmanager_port: str) -> str:
    appmanager_host = (appmanager_host or "").strip()
    appmanager_port = (appmanager_port or "").strip()
    if not _HOST_RE.match(appmanager_host):
        raise ReverseProxyError(f"Invalid AppManager backend host: {appmanager_host!r}")
    if not _PORT_RE.match(appmanager_port) or not (1 <= int(appmanager_port) <= 65535):
        raise ReverseProxyError(f"Invalid AppManager backend port: {appmanager_port!r}")
    return f"""{PROXY_AUTH_BEGIN}
location = /api/auth/proxy-check {{
    proxy_pass http://{appmanager_host}:{appmanager_port}/api/auth/proxy-check;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
}}

location @appmanager_login {{
    return 302 /?next=$request_uri;
}}
{PROXY_AUTH_END}
"""


class ReverseProxyError(ValueError):
    """Raised for invalid inputs or missing configuration before any SSH call."""


@dataclass
class PushResult:
    """Outcome of an alias push, with a step-by-step transcript (no secrets)."""

    status: str = "ok"  # ok | failed | reverted | skipped
    steps: list[str] = field(default_factory=list)

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.steps.append(f"[{stamp}] {message}")

    @property
    def transcript(self) -> str:
        return "\n".join(self.steps)


def render_alias_block(
    template: str,
    *,
    apps_server: str,
    apps_port: str,
    alias: str,
    app_name: str,
    timestamp: int | None = None,
) -> str:
    """Render the alias template by substituting the placeholders.

    Validates ``alias``/``apps_server``/``apps_port`` against strict whitelists so
    the result can never inject nginx directives or shell content. Returns the
    rendered block. Raises :class:`ReverseProxyError` on invalid input.
    """
    alias = (alias or "").strip().strip("/")
    apps_server = (apps_server or "").strip()
    apps_port = (apps_port or "").strip()
    if not _ALIAS_RE.match(alias):
        raise ReverseProxyError(f"Invalid alias for nginx push: {alias!r}")
    if not _HOST_RE.match(apps_server):
        raise ReverseProxyError(f"Invalid apps server: {apps_server!r}")
    if not _PORT_RE.match(apps_port) or not (1 <= int(apps_port) <= 65535):
        raise ReverseProxyError(f"Invalid apps port: {apps_port!r}")

    ts = int(time.time()) if timestamp is None else int(timestamp)
    # APPNAME/TIMESTAMP only appear in the comment header; keep them tidy.
    safe_name = re.sub(r"[^A-Za-z0-9 ._-]", "", app_name or "")[:80]
    block = template
    block = block.replace("APPS_SERVER", apps_server)
    block = block.replace("APPS_PORT", apps_port)
    block = block.replace("APPNAME", safe_name or "app")
    block = block.replace("TIMESTAMP", str(ts))
    # Replace ALIAS last so an APPNAME containing "ALIAS" is not affected first;
    # the comment header's ALIAS token is intentionally substituted too.
    block = block.replace("ALIAS", alias)
    return block


def inject_before_last_brace(conf_text: str, block: str) -> str:
    """Insert ``block`` immediately before the final ``}`` in ``conf_text``.

    The alias belongs inside the ``server { ... }`` of the 443 listener; injecting
    before the file's last closing brace places it inside that block while leaving
    all prior content untouched. Raises :class:`ReverseProxyError` if there is no
    closing brace to anchor against.
    """
    idx = conf_text.rfind("}")
    if idx == -1:
        raise ReverseProxyError("nginx config has no closing '}' to inject before.")
    prefix = conf_text[:idx]
    suffix = conf_text[idx:]
    # Ensure the injected block sits on its own lines.
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    body = block if block.endswith("\n") else block + "\n"
    return f"{prefix}{body}{suffix}"


def remove_marked_block(conf_text: str, begin: str, end: str) -> tuple[str, bool]:
    """Remove the region between (and including) the ``begin`` and ``end``
    sentinel lines from ``conf_text``.

    Returns ``(new_text, removed)``. ``removed`` is False (and the text is
    returned unchanged) when the markers are absent. Only the first occurrence is
    removed; markers are matched as whole lines (ignoring surrounding
    whitespace).
    """
    lines = conf_text.splitlines(keepends=True)
    begin_idx = end_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if begin_idx is None and stripped == begin:
            begin_idx = i
        elif begin_idx is not None and stripped == end:
            end_idx = i
            break
    if begin_idx is None or end_idx is None:
        return conf_text, False
    del lines[begin_idx : end_idx + 1]
    return "".join(lines), True


def comment_alias_block(block: str) -> str:
    """Comment every rendered nginx directive line in a marked app block.

    The marker comments remain unchanged so future pushes/deletes can still find
    and replace the block by application id.
    """
    commented: list[str] = []
    for line in block.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            commented.append(line)
            continue
        indent = line[: len(line) - len(stripped)]
        commented.append(f"{indent}# {stripped}")
    return "\n".join(commented) + "\n"


# --- SSH command seam (mocked in tests) ------------------------------------


@dataclass
class _Run:
    rc: int
    out: str
    err: str


def _run(argv: list[str], *, timeout: int = _SSH_TIMEOUT) -> _Run:
    """Run a command with no shell, capturing rc/stdout/stderr. The single seam
    the rest of the module goes through; patched in tests."""
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return _Run(proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    except subprocess.TimeoutExpired:
        return _Run(124, "", f"timeout after {timeout}s")
    except FileNotFoundError as exc:  # ssh/scp missing
        return _Run(127, "", str(exc))


def _ssh_base(host: str, key_path: str) -> list[str]:
    return [
        "ssh",
        "-i",
        key_path,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={min(_SSH_TIMEOUT, 10)}",
        host,
    ]


def _ssh(host: str, key_path: str, remote_cmd: str) -> _Run:
    return _run([*_ssh_base(host, key_path), remote_cmd])


# --- Orchestration ---------------------------------------------------------


def push_alias(
    settings: dict,
    *,
    apps_server: str,
    apps_port: str,
    alias: str,
    app_name: str,
    app_id: int,
    is_active: bool = True,
) -> PushResult:
    """Push one application alias to the remote nginx server.

    Performs the documented sequence: pre-flight checks, timestamped backup,
    render+inject, reload via ``docker exec nginx nginx -s reload``, verify, and
    revert from the backup on any failure. Returns a :class:`PushResult` with a
    secret-free transcript. Never raises for remote failures (they are captured
    in the result); only configuration/validation problems raise.
    """
    result = PushResult()
    host = (settings.get("nginx_host") or "").strip()
    user = (settings.get("nginx_user") or "").strip()
    conf_path = (settings.get("nginx_conf_path") or "").strip()
    key_path = (settings.get("ssh_key_path") or "").strip()
    template = settings.get("alias_template") or ""

    if not (host and conf_path and key_path and template):
        result.status = "skipped"
        result.log(
            "Skipped: reverse-proxy is not fully configured "
            "(host, conf path, SSH key path, and template are required)."
        )
        return result

    # Connect as user@host when an SSH user is configured, else as host (using
    # the SSH config's default user). Both parts are validated; reject anything
    # unexpected before building an ssh target.
    if not _HOST_RE.match(host):
        result.status = "failed"
        result.log(f"[FAIL] Invalid nginx host: {host!r}")
        return result
    if user and not _SSH_USER_RE.match(user):
        result.status = "failed"
        result.log(f"[FAIL] Invalid SSH user: {user!r}")
        return result
    host = f"{user}@{host}" if user else host

    # Render first so an invalid alias/server/port fails before touching SSH.
    try:
        block = render_alias_block(
            template,
            apps_server=apps_server,
            apps_port=apps_port,
            alias=alias,
            app_name=app_name,
        )
    except ReverseProxyError as exc:
        result.status = "failed"
        result.log(f"Render failed: {exc}")
        return result

    # Wrap the rendered block in unique sentinel markers so it can be removed
    # surgically when the application is deleted.
    begin, end = app_marker(app_id)
    block = f"{begin}\n{block.rstrip(chr(10))}\n{end}\n"
    if not is_active:
        block = comment_alias_block(block)

    norm_alias = alias.strip().strip("/")
    timestamp = int(time.time())
    backup_path = f"{conf_path}-{timestamp}-{norm_alias}"
    q_conf = shlex.quote(conf_path)
    q_backup = shlex.quote(backup_path)

    # 1) SSH key has access to the host.
    r = _ssh(host, key_path, "echo ok")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] SSH access to {host}: {r.err or r.rc}")
        return result
    result.log(f"[OK] SSH access to {host}")

    # 2) Conf file exists.
    r = _ssh(host, key_path, f"test -f {q_conf}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Conf file not found: {conf_path}")
        return result
    result.log(f"[OK] Conf file exists: {conf_path}")

    # 3) nginx is running (in the docker container).
    r = _ssh(host, key_path, "docker exec nginx nginx -v")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] nginx not running: {r.err or r.rc}")
        return result
    result.log("[OK] nginx is running")

    # 4) Write access to the conf file.
    r = _ssh(host, key_path, f"test -w {q_conf}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] No write access to {conf_path}")
        return result
    result.log(f"[OK] Write access to {conf_path}")

    # 5) Backup copy with -TIMESTAMP-ALIAS suffix.
    r = _ssh(host, key_path, f"cp {q_conf} {q_backup}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Backup copy failed: {r.err or r.rc}")
        return result
    result.log(f"[OK] Backup created: {backup_path}")

    # 6) Read current conf, replace any existing marked block, then inject the
    # rendered block before the last '}'.
    r = _ssh(host, key_path, f"cat {q_conf}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Could not read conf: {r.err or r.rc}")
        return result
    try:
        current_conf, replaced = remove_marked_block(r.out + "\n", begin, end)
        new_conf = inject_before_last_brace(current_conf, block)
    except ReverseProxyError as exc:
        result.status = "failed"
        result.log(f"[FAIL] Injection failed: {exc}")
        return result
    if replaced:
        result.log(f"[OK] Existing alias block replaced for app id={int(app_id)}")
    else:
        result.log(f"[OK] No existing alias block found for app id={int(app_id)}")

    # 7) Write the new conf atomically (write temp, then move into place).
    tmp_remote = f"{conf_path}.tmp-{timestamp}"
    q_tmp = shlex.quote(tmp_remote)
    write_cmd = [
        *_ssh_base(host, key_path),
        f"cat > {q_tmp} && mv {q_tmp} {q_conf}",
    ]
    w = _run_with_input(write_cmd, new_conf)
    if w.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Writing new conf failed: {w.err or w.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    mode = "commented disabled" if not is_active else "active"
    result.log(f"[OK] Alias block written ({mode})")

    # 8) Reload nginx (assumed to run in a docker container named 'nginx').
    r = _ssh(host, key_path, "docker exec nginx nginx -s reload")
    if r.rc != 0:
        result.status = "reverted"
        result.log(f"[FAIL] nginx reload failed: {r.err or r.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    result.log("[OK] nginx reloaded")

    # 9) Verify nginx is still running and the config is valid.
    r = _ssh(host, key_path, "docker exec nginx nginx -t")
    if r.rc != 0:
        result.status = "reverted"
        result.log(f"[FAIL] nginx -t after reload failed: {r.err or r.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    result.log("[OK] nginx config verified")

    result.status = "ok"
    state = "enabled" if is_active else "disabled"
    result.log(f"[DONE] Alias /{norm_alias}/ -> {apps_server}:{apps_port} ({state})")
    return result


def ensure_proxy_auth_config(settings: dict) -> PushResult:
    """Ensure the shared nginx auth_request locations exist in the remote config."""
    result = PushResult()
    host = (settings.get("nginx_host") or "").strip()
    user = (settings.get("nginx_user") or "").strip()
    conf_path = (settings.get("nginx_conf_path") or "").strip()
    key_path = (settings.get("ssh_key_path") or "").strip()
    appmanager_host = (settings.get("appmanager_proxy_host") or "").strip()
    appmanager_port = (settings.get("appmanager_proxy_port") or "").strip()

    if not (host and conf_path and key_path and appmanager_host and appmanager_port):
        result.status = "skipped"
        result.log(
            "Skipped: nginx host, conf path, SSH key path, AppManager backend host, "
            "and AppManager backend port are required."
        )
        return result
    if not _HOST_RE.match(host):
        result.status = "failed"
        result.log(f"[FAIL] Invalid nginx host: {host!r}")
        return result
    if user and not _SSH_USER_RE.match(user):
        result.status = "failed"
        result.log(f"[FAIL] Invalid SSH user: {user!r}")
        return result
    host = f"{user}@{host}" if user else host
    try:
        block = render_proxy_auth_block(
            appmanager_host=appmanager_host, appmanager_port=appmanager_port
        )
    except ReverseProxyError as exc:
        result.status = "failed"
        result.log(f"Render failed: {exc}")
        return result

    timestamp = int(time.time())
    backup_path = f"{conf_path}-{timestamp}-proxy-auth"
    tmp_remote = f"{conf_path}.tmp-{timestamp}"
    q_conf = shlex.quote(conf_path)
    q_backup = shlex.quote(backup_path)
    q_tmp = shlex.quote(tmp_remote)

    for label, cmd in (
        ("SSH access", "echo ok"),
        ("Conf file exists", f"test -f {q_conf}"),
        ("nginx is running", "docker exec nginx nginx -v"),
        ("Write access", f"test -w {q_conf}"),
    ):
        r = _ssh(host, key_path, cmd)
        if r.rc != 0:
            result.status = "failed"
            result.log(f"[FAIL] {label}: {r.err or r.rc}")
            return result
        result.log(f"[OK] {label}")

    r = _ssh(host, key_path, f"cat {q_conf}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Could not read conf: {r.err or r.rc}")
        return result
    current_conf = r.out + "\n"
    if PROXY_AUTH_BEGIN in current_conf and PROXY_AUTH_END in current_conf:
        result.status = "ok"
        result.log("[OK] Protected alias auth config already present")
        return result

    r = _ssh(host, key_path, f"cp {q_conf} {q_backup}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Backup copy failed: {r.err or r.rc}")
        return result
    result.log(f"[OK] Backup created: {backup_path}")

    try:
        new_conf = inject_before_last_brace(current_conf, block)
    except ReverseProxyError as exc:
        result.status = "failed"
        result.log(f"[FAIL] Injection failed: {exc}")
        return result

    write_cmd = [*_ssh_base(host, key_path), f"cat > {q_tmp} && mv {q_tmp} {q_conf}"]
    w = _run_with_input(write_cmd, new_conf)
    if w.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Writing new conf failed: {w.err or w.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    result.log("[OK] Protected alias auth config written")

    r = _ssh(host, key_path, "docker exec nginx nginx -s reload")
    if r.rc != 0:
        result.status = "reverted"
        result.log(f"[FAIL] nginx reload failed: {r.err or r.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    result.log("[OK] nginx reloaded")

    r = _ssh(host, key_path, "docker exec nginx nginx -t")
    if r.rc != 0:
        result.status = "reverted"
        result.log(f"[FAIL] nginx -t after reload failed: {r.err or r.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    result.status = "ok"
    result.log("[OK] nginx config verified")
    result.log("[DONE] Protected alias auth config ready")
    return result


def _run_with_input(argv: list[str], stdin_text: str) -> _Run:
    """Like ``_run`` but pipes ``stdin_text`` to the command. Patched in tests
    alongside ``_run`` (kept separate so the new conf body is testable)."""
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=_SSH_TIMEOUT,
            check=False,
        )
        return _Run(proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    except subprocess.TimeoutExpired:
        return _Run(124, "", f"timeout after {_SSH_TIMEOUT}s")
    except FileNotFoundError as exc:
        return _Run(127, "", str(exc))


def _revert(
    host: str,
    key_path: str,
    conf_path: str,
    backup_path: str,
    result: PushResult,
) -> None:
    """Restore the backup and capture nginx error output."""
    q_conf = shlex.quote(conf_path)
    q_backup = shlex.quote(backup_path)
    r = _ssh(host, key_path, f"cp {q_backup} {q_conf}")
    if r.rc == 0:
        result.log(f"[REVERT] Restored {conf_path} from backup")
        reload_r = _ssh(host, key_path, "docker exec nginx nginx -s reload")
        if reload_r.rc == 0:
            result.log("[REVERT] nginx reloaded with restored config")
        else:
            result.log(f"[REVERT] reload after restore failed: {reload_r.err}")
    else:
        result.log(f"[REVERT] FAILED to restore backup: {r.err or r.rc}")
    # Capture nginx error log tail for diagnostics (best-effort).
    errs = _ssh(host, key_path, "docker exec nginx nginx -t")
    if errs.err:
        result.log(f"[nginx] {errs.err}")


def remove_alias(settings: dict, *, app_id: int) -> PushResult:
    """Remove an application's alias block (by its unique marker) from the remote
    nginx config and reload.

    Mirrors :func:`push_alias`: pre-flight checks, timestamped backup, read,
    excise the marked region, atomic write, reload, verify, and revert from the
    backup on any failure. Returns a :class:`PushResult` with a secret-free
    transcript. ``skipped`` when the reverse proxy is not configured or the
    marker is not present (e.g. an alias pushed before markers existed). Never
    raises for remote failures.
    """
    result = PushResult()
    host = (settings.get("nginx_host") or "").strip()
    user = (settings.get("nginx_user") or "").strip()
    conf_path = (settings.get("nginx_conf_path") or "").strip()
    key_path = (settings.get("ssh_key_path") or "").strip()

    if not (host and conf_path and key_path):
        result.status = "skipped"
        result.log(
            "Skipped: reverse-proxy is not fully configured "
            "(host, conf path, and SSH key path are required)."
        )
        return result
    if not _HOST_RE.match(host):
        result.status = "failed"
        result.log(f"[FAIL] Invalid nginx host: {host!r}")
        return result
    if user and not _SSH_USER_RE.match(user):
        result.status = "failed"
        result.log(f"[FAIL] Invalid SSH user: {user!r}")
        return result
    host = f"{user}@{host}" if user else host

    begin, end = app_marker(app_id)
    timestamp = int(time.time())
    backup_path = f"{conf_path}-{timestamp}-remove-{int(app_id)}"
    q_conf = shlex.quote(conf_path)
    q_backup = shlex.quote(backup_path)

    # 1) SSH access.
    r = _ssh(host, key_path, "echo ok")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] SSH access to {host}: {r.err or r.rc}")
        return result
    result.log(f"[OK] SSH access to {host}")

    # 2) Conf file exists.
    r = _ssh(host, key_path, f"test -f {q_conf}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Conf file not found: {conf_path}")
        return result
    result.log(f"[OK] Conf file exists: {conf_path}")

    # 3) Read the current conf and find the marked block.
    r = _ssh(host, key_path, f"cat {q_conf}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Could not read conf: {r.err or r.rc}")
        return result
    new_conf, removed = remove_marked_block(r.out + "\n", begin, end)
    if not removed:
        result.status = "skipped"
        result.log(
            f"Skipped: no alias marker for this application ({begin}) was found "
            "in the config."
        )
        return result

    # 4) Write access + backup before changing the file.
    r = _ssh(host, key_path, f"test -w {q_conf}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] No write access to {conf_path}")
        return result
    r = _ssh(host, key_path, f"cp {q_conf} {q_backup}")
    if r.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Backup copy failed: {r.err or r.rc}")
        return result
    result.log(f"[OK] Backup created: {backup_path}")

    # 5) Write the conf with the block removed (atomic temp + move).
    tmp_remote = f"{conf_path}.tmp-{timestamp}"
    q_tmp = shlex.quote(tmp_remote)
    write_cmd = [
        *_ssh_base(host, key_path),
        f"cat > {q_tmp} && mv {q_tmp} {q_conf}",
    ]
    w = _run_with_input(write_cmd, new_conf)
    if w.rc != 0:
        result.status = "failed"
        result.log(f"[FAIL] Writing conf failed: {w.err or w.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    result.log("[OK] Alias block removed and conf written")

    # 6) Reload + verify; revert on failure.
    r = _ssh(host, key_path, "docker exec nginx nginx -s reload")
    if r.rc != 0:
        result.status = "reverted"
        result.log(f"[FAIL] nginx reload failed: {r.err or r.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    result.log("[OK] nginx reloaded")

    r = _ssh(host, key_path, "docker exec nginx nginx -t")
    if r.rc != 0:
        result.status = "reverted"
        result.log(f"[FAIL] nginx -t after reload failed: {r.err or r.rc}")
        _revert(host, key_path, conf_path, backup_path, result)
        return result
    result.log("[OK] nginx config verified")

    result.status = "ok"
    result.log(f"[DONE] Removed alias block for app id={int(app_id)}")
    return result
