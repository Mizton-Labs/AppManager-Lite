"""Reverse-proxy alias rendering, injection, and push orchestration.

The SSH command seam (``_run`` / ``_run_with_input``) is monkeypatched so no
real network access happens; these tests cover the pure logic and the push
sequence including revert-on-failure.
"""

from __future__ import annotations

import pytest

from app import reverse_proxy
from app.reverse_proxy import (
    DEFAULT_ALIAS_TEMPLATE,
    PROXY_AUTH_BEGIN,
    PROXY_AUTH_END,
    PushResult,
    ReverseProxyError,
    _Run,
    app_marker,
    ensure_proxy_auth_config,
    inject_before_last_brace,
    push_alias,
    remove_alias,
    remove_marked_block,
    render_alias_block,
)

_SETTINGS = {
    "nginx_host": "proxy.example.com",
    "nginx_conf_path": "/etc/nginx/conf.d/apps.conf",
    "ssh_key_path": "/data/keys/k",
    "appmanager_proxy_host": "appmanager",
    "appmanager_proxy_port": "8000",
    "alias_template": DEFAULT_ALIAS_TEMPLATE,
}


# --- render --------------------------------------------------------------


def test_render_substitutes_placeholders() -> None:
    block = render_alias_block(
        DEFAULT_ALIAS_TEMPLATE,
        apps_server="apps.example.com",
        apps_port="8080",
        alias="grafana",
        app_name="Grafana",
        timestamp=1700000000,
    )
    assert "proxy_pass http://apps.example.com:8080/;" in block
    assert "location /grafana/" in block
    assert "location = /grafana" in block
    assert "auth_request /api/auth/proxy-check;" in block
    assert "error_page 401 = @appmanager_login;" in block
    assert "Grafana" in block
    assert "1700000000" in block
    for placeholder in ("APPS_PROTOCOL", "APPS_SERVER", "APPS_PORT", "APPS_PATH", "ALIAS"):
        assert placeholder not in block


def test_render_substitutes_alias_upstream_path() -> None:
    block = render_alias_block(
        DEFAULT_ALIAS_TEMPLATE,
        apps_server="apps.example.com",
        apps_protocol="https",
        apps_port="8443",
        apps_path="dashboard",
        alias="grafana",
        app_name="Grafana",
        timestamp=1700000000,
    )

    assert "proxy_pass https://apps.example.com:8443/dashboard;" in block


def test_render_omits_auth_lines_when_alias_auth_disabled() -> None:
    block = render_alias_block(
        DEFAULT_ALIAS_TEMPLATE,
        apps_server="apps.example.com",
        apps_port="8080",
        alias="grafana",
        app_name="Grafana",
        alias_auth_required=False,
    )

    assert "location /grafana/" in block
    assert "proxy_pass http://apps.example.com:8080/;" in block
    assert "auth_request /api/auth/proxy-check;" not in block
    assert "error_page 401 = @appmanager_login;" not in block


@pytest.mark.parametrize(
    "alias,server,port",
    [
        ("../etc", "apps.example.com", "8080"),
        ("ok", "bad host", "8080"),
        ("ok", "apps.example.com", "99999"),
        ("ok", "apps.example.com", "0"),
        ("a;b", "apps.example.com", "80"),
    ],
)
def test_render_rejects_unsafe_values(alias, server, port) -> None:
    with pytest.raises(ReverseProxyError):
        render_alias_block(
            DEFAULT_ALIAS_TEMPLATE,
            apps_server=server,
            apps_port=port,
            alias=alias,
            app_name="x",
        )


# --- inject --------------------------------------------------------------


def test_inject_before_last_brace_preserves_content() -> None:
    conf = (
        "http {\n"
        "  server {\n"
        "    listen 443 ssl;\n"
        "    location / { proxy_pass http://x; }\n"
        "  }\n"
        "}\n"
    )
    out = inject_before_last_brace(conf, "BLOCK")
    assert "BLOCK" in out
    # The block is inserted before the final closing brace.
    assert out.index("BLOCK") < out.rindex("}")
    # Original directives are untouched.
    assert "listen 443 ssl;" in out
    assert "location / { proxy_pass http://x; }" in out
    assert out.rstrip().endswith("}")


def test_inject_requires_closing_brace() -> None:
    with pytest.raises(ReverseProxyError):
        inject_before_last_brace("no braces here", "BLOCK")


# --- push orchestration (mocked SSH) -------------------------------------


class _FakeRunner:
    """Records ssh/scp commands and returns scripted results."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[str] = []
        self.argvs: list[list[str]] = []

    def run(self, argv, *, timeout=20):
        cmd = argv[-1]  # remote command is the last ssh arg
        self.calls.append(cmd)
        self.argvs.append(argv)
        for needle, result in self.responses:
            if needle in cmd:
                return result
        return _Run(0, "", "")

    def run_with_input(self, argv, stdin_text):
        cmd = argv[-1]
        self.calls.append(f"WRITE:{cmd}")
        self.argvs.append(argv)
        self.last_written = stdin_text
        for needle, result in self.responses:
            if needle in cmd:
                return result
        return _Run(0, "", "")


def _install(monkeypatch, runner):
    monkeypatch.setattr(reverse_proxy, "_run", runner.run)
    monkeypatch.setattr(reverse_proxy, "_run_with_input", runner.run_with_input)


def test_push_skips_when_unconfigured() -> None:
    result = push_alias(
        {"nginx_host": "", "nginx_conf_path": "", "ssh_key_path": "", "alias_template": ""},
        apps_server="a.example.com",
        apps_port="80",
        alias="x",
        app_name="X",
        app_id=1,
    )
    assert result.status == "skipped"


def test_push_happy_path(monkeypatch) -> None:
    runner = _FakeRunner(
        [
            ("cat ", _Run(0, "http {\n  server {\n  }\n}", "")),  # read conf
        ]
    )
    _install(monkeypatch, runner)
    result = push_alias(
        _SETTINGS,
        apps_server="apps.example.com",
        apps_port="8080",
        alias="grafana",
        app_name="Grafana",
        app_id=7,
    )
    assert result.status == "ok", result.transcript
    # The injected conf was written and contains the rendered alias.
    assert "location /grafana/" in runner.last_written
    # The block is wrapped in the unique per-app marker for later removal.
    assert "# >>> appmanager-lite-app:7 >>>" in runner.last_written
    assert "# <<< appmanager-lite-app:7 <<<" in runner.last_written
    # The expected steps ran in order.
    assert any("echo ok" in c for c in runner.calls)
    assert any("cp " in c for c in runner.calls)  # backup
    assert any("nginx -s reload" in c for c in runner.calls)
    assert any("nginx -t" in c for c in runner.calls)


def _ssh_targets(runner) -> set[str]:
    """The ssh target (argv element before the remote command) for ssh calls."""
    targets = set()
    for argv in runner.argvs:
        if argv and argv[0] == "ssh" and len(argv) >= 2:
            targets.add(argv[-2])
    return targets


def test_push_uses_bare_host_without_ssh_user(monkeypatch) -> None:
    runner = _FakeRunner([("cat ", _Run(0, "http {\n  server {\n  }\n}", ""))])
    _install(monkeypatch, runner)
    push_alias(
        _SETTINGS,
        apps_server="apps.example.com",
        apps_port="8080",
        alias="grafana",
        app_name="Grafana",
        app_id=7,
    )
    assert _ssh_targets(runner) == {"proxy.example.com"}


def test_push_uses_user_at_host_with_ssh_user(monkeypatch) -> None:
    runner = _FakeRunner([("cat ", _Run(0, "http {\n  server {\n  }\n}", ""))])
    _install(monkeypatch, runner)
    settings = {**_SETTINGS, "nginx_user": "deploy"}
    push_alias(
        settings,
        apps_server="apps.example.com",
        apps_port="8080",
        alias="grafana",
        app_name="Grafana",
        app_id=7,
    )
    assert _ssh_targets(runner) == {"deploy@proxy.example.com"}


def test_push_reverts_on_reload_failure(monkeypatch) -> None:
    runner = _FakeRunner(
        [
            ("cat ", _Run(0, "http {\n  server {\n  }\n}", "")),
            ("nginx -s reload", _Run(1, "", "reload error")),
        ]
    )
    _install(monkeypatch, runner)
    result = push_alias(
        _SETTINGS,
        apps_server="apps.example.com",
        apps_port="8080",
        alias="grafana",
        app_name="Grafana",
        app_id=7,
    )
    assert result.status == "reverted", result.transcript
    # The backup was restored (cp <backup> <conf>).
    assert any("[REVERT]" in s for s in result.steps)


def test_push_fails_when_conf_missing(monkeypatch) -> None:
    runner = _FakeRunner(
        [
            ("test -f", _Run(1, "", "")),  # conf file not found
        ]
    )
    _install(monkeypatch, runner)
    result = push_alias(
        _SETTINGS,
        apps_server="apps.example.com",
        apps_port="8080",
        alias="grafana",
        app_name="Grafana",
        app_id=7,
    )
    assert result.status == "failed"
    assert any("Conf file not found" in s for s in result.steps)


def test_push_result_transcript() -> None:
    r = PushResult()
    r.log("a")
    r.log("b")
    lines = r.transcript.splitlines()
    assert len(lines) == 2
    assert lines[0].endswith(" a")
    assert lines[1].endswith(" b")
    assert lines[0].startswith("[")


def test_ensure_proxy_auth_config_injects_when_missing(monkeypatch) -> None:
    runner = _FakeRunner([("cat ", _Run(0, "http {\n  server {\n  }\n}", ""))])
    _install(monkeypatch, runner)

    result = ensure_proxy_auth_config(_SETTINGS)

    assert result.status == "ok", result.transcript
    assert PROXY_AUTH_BEGIN in runner.last_written
    assert "proxy_pass http://appmanager:8000/api/auth/proxy-check;" in runner.last_written
    assert any("nginx -s reload" in c for c in runner.calls)
    assert any("nginx -t" in c for c in runner.calls)


def test_ensure_proxy_auth_config_skips_existing_marker(monkeypatch) -> None:
    conf = f"http {{\n  server {{\n    {PROXY_AUTH_BEGIN}\n    {PROXY_AUTH_END}\n  }}\n}}"
    runner = _FakeRunner([("cat ", _Run(0, conf, ""))])
    _install(monkeypatch, runner)

    result = ensure_proxy_auth_config(_SETTINGS)

    assert result.status == "ok"
    assert "already present" in result.transcript
    assert not any(c.startswith("WRITE:") for c in runner.calls)


def test_ensure_proxy_auth_config_reverts_on_reload_failure(monkeypatch) -> None:
    runner = _FakeRunner(
        [
            ("cat ", _Run(0, "http {\n  server {\n  }\n}", "")),
            ("nginx -s reload", _Run(1, "", "reload error")),
        ]
    )
    _install(monkeypatch, runner)

    result = ensure_proxy_auth_config(_SETTINGS)

    assert result.status == "reverted", result.transcript
    assert any("[REVERT]" in s for s in result.steps)


# --- marker + removal ----------------------------------------------------


def test_app_marker_is_id_derived() -> None:
    begin, end = app_marker(42)
    assert begin == "# >>> appmanager-lite-app:42 >>>"
    assert end == "# <<< appmanager-lite-app:42 <<<"


def test_remove_marked_block_excises_only_marked_region() -> None:
    begin, end = app_marker(5)
    conf = (
        "http {\n"
        "  server {\n"
        "    listen 443 ssl;\n"
        f"    {begin}\n"
        "    location /grafana/ { proxy_pass http://x; }\n"
        f"    {end}\n"
        "    location / { proxy_pass http://y; }\n"
        "  }\n"
        "}\n"
    )
    new, removed = remove_marked_block(conf, begin, end)
    assert removed is True
    assert "location /grafana/" not in new
    assert begin not in new and end not in new
    # Surrounding content is preserved.
    assert "listen 443 ssl;" in new
    assert "location / { proxy_pass http://y; }" in new
    assert new.rstrip().endswith("}")


def test_remove_marked_block_noop_when_absent() -> None:
    begin, end = app_marker(99)
    conf = "http {\n  server {\n  }\n}\n"
    new, removed = remove_marked_block(conf, begin, end)
    assert removed is False
    assert new == conf


def test_remove_alias_happy_path(monkeypatch) -> None:
    begin, end = app_marker(7)
    conf = (
        "http {\n  server {\n"
        f"    {begin}\n    location /grafana/ {{ proxy_pass http://x; }}\n    {end}\n"
        "  }\n}\n"
    )
    runner = _FakeRunner([("cat ", _Run(0, conf, ""))])
    _install(monkeypatch, runner)
    result = remove_alias(_SETTINGS, app_id=7)
    assert result.status == "ok", result.transcript
    # The written conf no longer contains the marked block.
    assert begin not in runner.last_written
    assert "location /grafana/" not in runner.last_written
    assert any("cp " in c for c in runner.calls)  # backup
    assert any("nginx -s reload" in c for c in runner.calls)


def test_remove_alias_skips_when_marker_absent(monkeypatch) -> None:
    runner = _FakeRunner([("cat ", _Run(0, "http {\n  server {\n  }\n}", ""))])
    _install(monkeypatch, runner)
    result = remove_alias(_SETTINGS, app_id=7)
    assert result.status == "skipped"
    # No write happened.
    assert not any(c.startswith("WRITE:") for c in runner.calls)


def test_remove_alias_skips_when_unconfigured() -> None:
    result = remove_alias(
        {"nginx_host": "", "nginx_conf_path": "", "ssh_key_path": ""},
        app_id=7,
    )
    assert result.status == "skipped"


def test_remove_alias_reverts_on_reload_failure(monkeypatch) -> None:
    begin, end = app_marker(7)
    conf = (
        "http {\n  server {\n"
        f"    {begin}\n    location /grafana/ {{ proxy_pass http://x; }}\n    {end}\n"
        "  }\n}\n"
    )
    runner = _FakeRunner(
        [
            ("cat ", _Run(0, conf, "")),
            ("nginx -s reload", _Run(1, "", "reload error")),
        ]
    )
    _install(monkeypatch, runner)
    result = remove_alias(_SETTINGS, app_id=7)
    assert result.status == "reverted", result.transcript
    assert any("[REVERT]" in s for s in result.steps)
