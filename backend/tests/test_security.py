"""Tests for the defensive HTTP security headers applied to every response."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    secure_cookies: bool = False,
    enable_auth: bool = False,
) -> TestClient:
    data = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(data))
    monkeypatch.setenv("APP_DB_PATH", str(data / "app.db"))
    monkeypatch.setenv("APP_FRONTEND_DIST", str(tmp_path / "no-dist"))
    monkeypatch.setenv("APP_ENABLE_AUTH", "1" if enable_auth else "0")
    monkeypatch.setenv("APP_BASE_PREFIX", "")
    monkeypatch.setenv("APP_DEV", "1")
    monkeypatch.setenv("APP_SECURE_COOKIES", "1" if secure_cookies else "0")
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "logs"))

    from app.config import get_settings

    get_settings.cache_clear()

    from app.main import create_app

    return TestClient(create_app())


def test_standard_security_headers_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _build_client(monkeypatch, tmp_path) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "no-referrer"
        assert resp.headers["Cross-Origin-Opener-Policy"] == "same-origin"


def test_content_security_policy_locks_down_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _build_client(monkeypatch, tmp_path) as client:
        csp = client.get("/api/health").headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "script-src 'self'" in csp


def test_api_responses_are_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _build_client(monkeypatch, tmp_path) as client:
        resp = client.get("/api/health")
        assert resp.headers["Cache-Control"] == "no-store"


def test_hsts_absent_without_secure_cookies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _build_client(monkeypatch, tmp_path, secure_cookies=False) as client:
        resp = client.get("/api/health")
        assert "Strict-Transport-Security" not in resp.headers


def test_hsts_present_with_secure_cookies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with _build_client(monkeypatch, tmp_path, secure_cookies=True) as client:
        resp = client.get("/api/health")
        assert resp.headers["Strict-Transport-Security"].startswith("max-age=")


def test_docs_are_exempt_from_csp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Dev mode (APP_DEV=1) exposes the OpenAPI schema; it must remain usable.
    with _build_client(monkeypatch, tmp_path) as client:
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        assert "Content-Security-Policy" not in resp.headers
        # Other defensive headers are still present.
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
