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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

_URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+(?::\d{1,5})?/?$")
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
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


# ---------------------------------------------------------------------------
# Guest operations (clone, start, IP discovery, resources)
# ---------------------------------------------------------------------------

# Poll interval and budgets for asynchronous Proxmox tasks. ``_sleep`` is a
# module-level seam so tests can eliminate waiting.
_sleep = time.sleep
_POLL_SECONDS = 2.0
_CLONE_BUDGET_SECONDS = 300.0
_START_BUDGET_SECONDS = 60.0
_IP_BUDGET_SECONDS = 60.0


def _guest_path(kind: str) -> str:
    return "lxc" if kind == "lxc" else "qemu"


def find_guest(
    config: dict[str, Any], vmid: int, *, result: ProxmoxResult
) -> dict[str, Any] | None:
    """Locate a guest cluster-wide; returns ``{vmid, name, kind, node}``."""
    data = _call(config, "GET", "/cluster/resources?type=vm", result=result)
    if result.status != "ok":
        return None
    for item in data or []:
        if isinstance(item, dict) and item.get("vmid") == vmid:
            return {
                "vmid": vmid,
                "name": str(item.get("name", "")),
                "kind": "lxc" if item.get("type") == "lxc" else "vm",
                "node": str(item.get("node", "")),
                "is_template": bool(item.get("template")),
            }
    result.fail(f"guest {vmid} was not found on the cluster")
    return None


def next_vmid(config: dict[str, Any], *, result: ProxmoxResult) -> int | None:
    data = _call(config, "GET", "/cluster/nextid", result=result)
    if result.status != "ok":
        return None
    try:
        vmid = int(data)
    except (TypeError, ValueError):
        result.fail(f"unexpected next-id response: {data!r}")
        return None
    result.log(f"Next free VMID: {vmid}")
    return vmid


def _wait_task(
    config: dict[str, Any],
    node: str,
    upid: str,
    *,
    result: ProxmoxResult,
    budget: float,
    label: str,
) -> bool:
    """Poll a Proxmox task until it stops; True when it exited OK."""
    if not isinstance(upid, str) or not upid.startswith("UPID:"):
        result.fail(f"{label}: unexpected task id {str(upid)[:60]!r}")
        return False
    path = f"/nodes/{quote(node, safe='')}/tasks/{quote(upid, safe='')}/status"
    waited = 0.0
    while True:
        # Poll with a transient result so the transcript is not flooded with
        # one line per poll; only the final outcome is copied over.
        probe = ProxmoxResult()
        data = _call(config, "GET", path, result=probe)
        if probe.status != "ok":
            result.fail(f"{label}: {probe.steps[-1] if probe.steps else 'poll failed'}")
            return False
        status = (data or {}).get("status")
        if status == "stopped":
            exitstatus = str((data or {}).get("exitstatus", ""))
            if exitstatus == "OK":
                result.log(f"{label}: task finished OK")
                return True
            result.fail(f"{label}: task failed ({exitstatus[:200]})")
            return False
        if waited >= budget:
            result.fail(f"{label}: timed out after {int(budget)}s")
            return False
        _sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS


def clone_guest(
    config: dict[str, Any],
    *,
    source_vmid: int,
    new_vmid: int,
    name: str,
    result: ProxmoxResult,
) -> dict[str, Any] | None:
    """Full-clone a template into ``new_vmid``; returns ``{node, kind}``."""
    source = find_guest(config, source_vmid, result=result)
    if source is None:
        return None
    node, kind = source["node"], source["kind"]
    body: dict[str, Any] = {"newid": new_vmid, "full": 1}
    if kind == "lxc":
        body["hostname"] = name
    else:
        body["name"] = name
    upid = _call(
        config,
        "POST",
        f"/nodes/{node}/{_guest_path(kind)}/{source_vmid}/clone",
        result=result,
        json_body=body,
    )
    if result.status != "ok":
        return None
    if not _wait_task(
        config, node, upid, result=result,
        budget=_CLONE_BUDGET_SECONDS, label="clone",
    ):
        return None
    result.log(f"Cloned {kind} {source_vmid} -> {new_vmid} ({name}) on {node}")
    return {"node": node, "kind": kind}


def start_guest(
    config: dict[str, Any],
    node: str,
    vmid: int,
    kind: str,
    *,
    result: ProxmoxResult,
) -> bool:
    upid = _call(
        config,
        "POST",
        f"/nodes/{node}/{_guest_path(kind)}/{vmid}/status/start",
        result=result,
        json_body={},
    )
    if result.status != "ok":
        return False
    return _wait_task(
        config, node, upid, result=result,
        budget=_START_BUDGET_SECONDS, label="start",
    )


_STOP_BUDGET_SECONDS = 60.0
_DESTROY_BUDGET_SECONDS = 120.0


def _guest_status(
    config: dict[str, Any], node: str, vmid: int, kind: str, *,
    result: ProxmoxResult,
) -> str | None:
    """Current run-state of a guest ('running'/'stopped'/...), or None on error."""
    data = _call(
        config, "GET",
        f"/nodes/{quote(node, safe='')}/{_guest_path(kind)}/{vmid}/status/current",
        result=result,
    )
    if result.status != "ok":
        return None
    return str((data or {}).get("status", "")) or None


def _guest_exists(config: dict[str, Any], vmid: int) -> bool:
    """True when a guest with ``vmid`` is present cluster-wide.

    Uses a transient result so a not-found does not poison the caller's
    transcript (find_guest marks its result failed when absent).
    """
    probe = ProxmoxResult()
    data = _call(config, "GET", "/cluster/resources?type=vm", result=probe)
    if probe.status != "ok":
        # Can't tell; assume it may still exist so the caller does not
        # mistakenly treat an API hiccup as "already gone".
        return True
    return any(
        isinstance(item, dict) and item.get("vmid") == vmid
        for item in data or []
    )


def stop_guest(
    config: dict[str, Any],
    node: str,
    vmid: int,
    kind: str,
    *,
    result: ProxmoxResult,
) -> bool:
    """Force-stop a guest; a already-stopped (or absent) guest is a success.

    Uses ``status/stop`` (immediate power-off) rather than a graceful shutdown
    because this is only ever called on the deletion path, where the guest is
    about to be destroyed.
    """
    if not _guest_exists(config, vmid):
        result.log(f"guest {vmid} is already gone; nothing to stop")
        return True
    state = _guest_status(config, node, vmid, kind, result=result)
    if result.status != "ok":
        return False
    if state == "stopped":
        result.log(f"guest {vmid} already stopped")
        return True
    upid = _call(
        config,
        "POST",
        f"/nodes/{quote(node, safe='')}/{_guest_path(kind)}/{vmid}/status/stop",
        result=result,
        json_body={},
    )
    if result.status != "ok":
        return False
    return _wait_task(
        config, node, upid, result=result,
        budget=_STOP_BUDGET_SECONDS, label="stop",
    )


def destroy_guest(
    config: dict[str, Any],
    node: str,
    vmid: int,
    kind: str,
    *,
    result: ProxmoxResult,
) -> bool:
    """Destroy a guest and its disks; an already-absent guest is a success.

    Idempotent: if the guest is not present on the cluster (already destroyed),
    returns True without an API call. Otherwise issues a DELETE with disk purge
    and waits for the task to finish.
    """
    if not _guest_exists(config, vmid):
        result.log(f"guest {vmid} is already gone; nothing to destroy")
        return True
    # purge=1 removes the guest from all related configs (HA, replication,
    # backup jobs) so no dangling references remain. We deliberately do NOT
    # pass destroy-unreferenced-disks: that would also reap volumes keyed to
    # this VMID that are not in the current config (e.g. a disk an operator
    # detached but intentionally retained), risking silent data loss on an
    # automatic grace-expiry destroy. Only the guest's referenced disks go.
    upid = _call(
        config,
        "DELETE",
        f"/nodes/{quote(node, safe='')}/{_guest_path(kind)}/{vmid}?purge=1",
        result=result,
    )
    if result.status != "ok":
        return False
    return _wait_task(
        config, node, upid, result=result,
        budget=_DESTROY_BUDGET_SECONDS, label="destroy",
    )


def get_lxc_ip(
    config: dict[str, Any],
    node: str,
    vmid: int,
    *,
    result: ProxmoxResult,
    budget: float = _IP_BUDGET_SECONDS,
) -> str:
    """Poll a running container's interfaces for its first IPv4 address."""
    waited = 0.0
    while True:
        probe = ProxmoxResult()  # transient failures are retried quietly
        data = _call(config, "GET", f"/nodes/{node}/lxc/{vmid}/interfaces",
                     result=probe)
        if probe.status == "ok":
            for iface in data or []:
                if not isinstance(iface, dict):
                    continue
                if iface.get("name") in ("lo", "lo0"):
                    continue
                inet = str(iface.get("inet", "") or "")
                ip = inet.split("/", 1)[0]
                # Validate the shape before it is used as an SSH destination
                # or stored: never trust provider-returned strings blindly.
                if (
                    ip
                    and not ip.startswith("127.")
                    and _IPV4_RE.match(ip)
                    and all(int(part) <= 255 for part in ip.split("."))
                ):
                    result.log(f"Container IP: {ip}")
                    return ip
        if waited >= budget:
            result.fail(
                f"could not determine the container IP after {int(budget)}s"
            )
            return ""
        _sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS


def list_lxc_ips(
    config: dict[str, Any],
    node: str,
    vmid: int,
    *,
    result: ProxmoxResult,
) -> set[str]:
    """Return all non-loopback IPv4 addresses the hypervisor sees for a guest.

    A single, non-polling read of the interfaces endpoint (the caller has
    already confirmed the guest is up). Used to corroborate an in-guest IP
    report: only an address the hypervisor also attributes to this guest may
    be trusted, so a compromised guest cannot make AppManager adopt an
    arbitrary address. Returns an empty set on any read failure.
    """
    addrs: set[str] = set()
    probe = ProxmoxResult()
    data = _call(config, "GET", f"/nodes/{node}/lxc/{vmid}/interfaces",
                 result=probe)
    if probe.status != "ok":
        result.log("could not read hypervisor interface list for corroboration")
        return addrs
    for iface in data or []:
        if not isinstance(iface, dict):
            continue
        if iface.get("name") in ("lo", "lo0"):
            continue
        inet = str(iface.get("inet", "") or "")
        ip = inet.split("/", 1)[0]
        if (
            ip
            and not ip.startswith("127.")
            and _IPV4_RE.match(ip)
            and all(int(part) <= 255 for part in ip.split("."))
        ):
            addrs.add(ip)
    return addrs


_DISK_SIZE_RE = re.compile(r"size=(\d+)([MGT])")


def _disk_gb_from_config(value: str) -> int:
    match = _DISK_SIZE_RE.search(value or "")
    if not match:
        return 0
    number, unit = int(match.group(1)), match.group(2)
    if unit == "M":
        return max(1, number // 1024)
    if unit == "T":
        return number * 1024
    return number


def get_guest_resources(
    config: dict[str, Any],
    node: str,
    vmid: int,
    kind: str,
    *,
    result: ProxmoxResult,
) -> dict[str, int] | None:
    """Read cores / memory (GB) / disk (GB) from the guest config."""
    data = _call(
        config,
        "GET",
        f"/nodes/{node}/{_guest_path(kind)}/{vmid}/config",
        result=result,
    )
    if result.status != "ok":
        return None
    cfg = data or {}
    cores = int(cfg.get("cores", 1) or 1)
    memory_mb = int(cfg.get("memory", 512) or 512)
    if kind == "lxc":
        disk_gb = _disk_gb_from_config(str(cfg.get("rootfs", "")))
    else:
        disk_gb = 0
        for key, value in cfg.items():
            if re.match(r"^(scsi|virtio|sata|ide)\d+$", str(key)):
                disk_gb += _disk_gb_from_config(str(value))
    resources = {
        "cpus": cores,
        "memory_gb": max(1, round(memory_mb / 1024)),
        "disk_gb": disk_gb,
    }
    result.log(
        f"Guest {vmid} resources: {resources['cpus']} CPUs, "
        f"{resources['memory_gb']} GB memory, {resources['disk_gb']} GB disk"
    )
    return resources


def set_lxc_resources(
    config: dict[str, Any],
    node: str,
    vmid: int,
    *,
    cpus: int | None = None,
    memory_gb: int | None = None,
    disk_gb_target: int | None = None,
    current_disk_gb: int = 0,
    result: ProxmoxResult,
) -> bool:
    """Apply CPU/memory config changes and grow the rootfs when requested."""
    body: dict[str, Any] = {}
    if cpus is not None:
        body["cores"] = cpus
    if memory_gb is not None:
        body["memory"] = memory_gb * 1024
    if body:
        _call(
            config, "PUT", f"/nodes/{node}/lxc/{vmid}/config",
            result=result, json_body=body,
        )
        if result.status != "ok":
            return False
        result.log(f"Updated config: {sorted(body)}")
    if disk_gb_target is not None:
        if disk_gb_target < current_disk_gb:
            result.fail("disk can only be grown, not shrunk")
            return False
        if disk_gb_target > current_disk_gb:
            grow = disk_gb_target - current_disk_gb
            upid = _call(
                config, "PUT", f"/nodes/{node}/lxc/{vmid}/resize",
                result=result,
                json_body={"disk": "rootfs", "size": f"+{grow}G"},
            )
            if result.status != "ok":
                return False
            # Small resizes may complete synchronously (no task id).
            if isinstance(upid, str) and upid.startswith("UPID:"):
                if not _wait_task(
                    config, node, upid, result=result,
                    budget=_START_BUDGET_SECONDS, label="resize",
                ):
                    return False
            result.log(f"Grew rootfs by {grow}G")
    return True


# Timeframes the Proxmox RRD API accepts for guest usage graphs.
RRD_TIMEFRAMES = ("hour", "day", "week", "month", "year")


def get_guest_rrddata(
    config: dict[str, Any],
    node: str,
    vmid: int,
    kind: str,
    *,
    timeframe: str = "hour",
    result: ProxmoxResult,
) -> list[dict[str, float]] | None:
    """Read historical CPU/memory/disk/network usage samples for a guest.

    Wraps Proxmox ``/nodes/{node}/{lxc|qemu}/{vmid}/rrddata`` (AVERAGE
    consolidation). ``timeframe`` must be one of :data:`RRD_TIMEFRAMES`.
    Returns a list of normalized samples ordered by time; each has:
    ``time`` (epoch seconds), ``cpu`` (0-1 fraction), ``mem``/``maxmem``,
    ``disk``/``maxdisk`` (bytes), ``netin``/``netout`` (bytes/s). Missing
    fields in a sample default to 0.0. Returns ``None`` on error.
    """
    if timeframe not in RRD_TIMEFRAMES:
        result.fail(f"invalid rrd timeframe {timeframe!r}")
        return None
    data = _call(
        config,
        "GET",
        f"/nodes/{quote(node, safe='')}/{_guest_path(kind)}/{vmid}/rrddata"
        f"?timeframe={quote(timeframe, safe='')}&cf=AVERAGE",
        result=result,
    )
    if result.status != "ok":
        return None
    fields = ("cpu", "mem", "maxmem", "disk", "maxdisk", "netin", "netout")
    samples: list[dict[str, float]] = []
    for point in data or []:
        if not isinstance(point, dict):
            continue
        sample: dict[str, float] = {"time": float(point.get("time", 0) or 0)}
        for key in fields:
            try:
                sample[key] = float(point.get(key, 0) or 0)
            except (TypeError, ValueError):
                sample[key] = 0.0
        samples.append(sample)
    result.log(
        f"Guest {vmid} rrddata ({timeframe}): {len(samples)} sample(s)"
    )
    return samples
