"""Proxmox VE API client for server provisioning.

Uses API-token authentication (``PVEAPIToken=<token-name>=<secret>``). The
token secret is read from the settings row where it is stored write-only; it
is never logged, audited, or echoed back to the frontend, and never appears
in transcripts produced here.

The single HTTP seam ``_http_request`` keeps tests hermetic (they monkeypatch
it, mirroring how ``reverse_proxy._run`` is patched).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

_URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(?::\d{1,5})?/?$")
_TIMEOUT_SECONDS = 10.0


class ProxmoxError(ValueError):
    """Raised for locally detected configuration problems."""


@dataclass
class ProxmoxResult:
    """Outcome of a Proxmox API interaction, with a secret-free transcript."""

    status: str = "ok"  # ok | failed
    steps: list[str] = field(default_factory=list)
    data: Any = None

    def log(self, message: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.steps.append(f"[{stamp}] {message}")

    def fail(self, message: str) -> "ProxmoxResult":
        self.status = "failed"
        self.log(f"ERROR: {message}")
        return self

    @property
    def transcript(self) -> str:
        return "\n".join(self.steps)


def normalize_url(url: str) -> str:
    """Validate and normalize the Proxmox base URL (PROTO://HOST[:PORT])."""
    url = url.strip().rstrip("/")
    if not url:
        raise ProxmoxError("Proxmox URL is required")
    if not _URL_RE.match(url):
        raise ProxmoxError(
            "Proxmox URL must look like https://host:8006 "
            "(letters, digits, dots, dashes, and an optional port)"
        )
    return url


def _auth_header(token_name: str, api_key: str) -> str:
    token_name = token_name.strip()
    api_key = api_key.strip()
    if not token_name or not api_key:
        raise ProxmoxError("Proxmox token name and API key are required")
    if any(ch in token_name for ch in " \t\r\n\"'"):
        raise ProxmoxError("Proxmox token name contains invalid characters")
    return f"PVEAPIToken={token_name}={api_key}"


_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def _http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    verify: bool,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Single HTTP seam. Returns ``(status_code, parsed_json_or_text)``.

    Redirects are never followed (locks in the SSRF posture) and response
    bodies are capped so a hostile endpoint cannot exhaust memory.
    """
    with httpx.Client(
        verify=verify, timeout=_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        with client.stream(
            method, url, headers=headers, json=json_body
        ) as response:
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    return response.status_code, "(response too large)"
                chunks.append(chunk)
            body = b"".join(chunks)
    try:
        return response.status_code, json.loads(body)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return response.status_code, body.decode("utf-8", errors="replace")


def _call(
    config: dict[str, Any],
    method: str,
    path: str,
    *,
    result: ProxmoxResult,
    json_body: dict[str, Any] | None = None,
) -> Any | None:
    """One authenticated API call; logs progress and failures to ``result``.

    Returns the payload's ``data`` member on success, or ``None`` after
    marking the result failed. Never raises, even for local config problems
    (a hand-edited row must not turn into a 500).
    """
    try:
        base = normalize_url(config.get("proxmox_url", ""))
        header = _auth_header(
            config.get("proxmox_token_name", ""),
            config.get("proxmox_api_key", ""),
        )
    except ProxmoxError as exc:
        result.fail(str(exc))
        return None
    verify = bool(config.get("proxmox_verify_tls", True))
    url = f"{base}/api2/json{path}"
    result.log(f"{method} {base}/api2/json{path}")
    if base.startswith("http://"):
        result.log(
            "WARNING: plain http:// URL - the API token transits unencrypted"
        )
    if not verify:
        result.log("WARNING: TLS certificate verification is disabled")
    try:
        status_code, payload = _http_request(
            method,
            url,
            headers={"Authorization": header},
            verify=verify,
            json_body=json_body,
        )
    except httpx.HTTPError as exc:
        result.fail(f"connection failed: {exc.__class__.__name__}: {exc}")
        return None
    if status_code == 401:
        result.fail("authentication failed (401): check token name and API key")
        return None
    if status_code == 403:
        result.fail("permission denied (403): the API token lacks privileges")
        return None
    if status_code >= 400:
        detail = payload if isinstance(payload, str) else json.dumps(payload)[:300]
        result.fail(f"HTTP {status_code}: {detail[:300]}")
        return None
    if not isinstance(payload, dict) or "data" not in payload:
        result.fail("unexpected response shape (not a Proxmox API payload)")
        return None
    result.log(f"HTTP {status_code}: ok")
    return payload["data"]


def test_connection(config: dict[str, Any]) -> ProxmoxResult:
    """Verify the URL/token by reading the API version."""
    result = ProxmoxResult()
    result.log("Testing Proxmox API connectivity")
    data = _call(config, "GET", "/version", result=result)
    if result.status != "ok":
        return result
    version = (data or {}).get("version", "unknown")
    result.log(f"Connected: Proxmox VE version {version}")
    result.data = {"version": version}
    return result


def list_templates(config: dict[str, Any]) -> ProxmoxResult:
    """List cluster VMs/containers matching the configured name filter.

    Honors ``proxmox_templates_only`` (restrict to actual Proxmox templates)
    and ``proxmox_template_filter`` (case-insensitive name substring).
    """
    result = ProxmoxResult()
    data = _call(config, "GET", "/cluster/resources?type=vm", result=result)
    if result.status != "ok":
        return result
    name_filter = (config.get("proxmox_template_filter") or "").strip().lower()
    templates_only = bool(config.get("proxmox_templates_only", True))
    entries = []
    for item in data or []:
        if not isinstance(item, dict) or not isinstance(item.get("vmid"), int):
            continue
        name = str(item.get("name", ""))
        if templates_only and not item.get("template"):
            continue
        if name_filter and name_filter not in name.lower():
            continue
        entries.append(
            {
                "vmid": item.get("vmid"),
                "name": name,
                "kind": "lxc" if item.get("type") == "lxc" else "vm",
                "node": str(item.get("node", "")),
                "is_template": bool(item.get("template")),
            }
        )
    entries.sort(key=lambda e: (e["name"], e["vmid"]))
    result.log(
        f"Found {len(entries)} matching "
        f"{'templates' if templates_only else 'VMs/containers'}"
        + (f" for filter {name_filter!r}" if name_filter else "")
    )
    result.data = entries
    return result
