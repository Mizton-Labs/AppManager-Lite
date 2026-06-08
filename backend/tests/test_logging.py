"""Tests for consolidated application logging.

These verify that security-relevant events are recorded, that the consolidated
log file is created and written, that the configuration is idempotent (the test
suite builds many app instances), and that secrets never reach the logs.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ADMIN_USERNAME = "admin"


def test_failed_login_emits_warning_without_secret(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "WrongHorseBatteryStaple_SHOULD_NOT_APPEAR"
    with caplog.at_level(logging.INFO, logger="app.routers.auth"):
        resp = client.post(
            "/api/auth/login",
            json={"username": ADMIN_USERNAME, "password": secret},
        )
    assert resp.status_code == 401

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "Login failed" in r.getMessage() and "bad_password" in r.getMessage()
        for r in warnings
    ), caplog.text
    # The attempted password must never be written to the logs.
    assert secret not in caplog.text


def test_app_log_file_is_created_and_written(
    client: TestClient, tmp_path: Path
) -> None:
    # The conftest fixtures point APP_LOG_DIR at ``tmp_path / "logs"``.
    log_file = tmp_path / "logs" / "app.log"
    assert log_file.exists(), "consolidated log file was not created"

    client.get("/api/health")

    contents = log_file.read_text(encoding="utf-8")
    assert "Logging configured" in contents
    assert contents.strip(), "log file is unexpectedly empty"


def test_configure_logging_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("APP_LOG_TO_FILE", "1")

    from app.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    from app.logging_config import _MANAGED, configure_logging

    root = logging.getLogger()

    configure_logging(settings)
    first = [h for h in root.handlers if getattr(h, _MANAGED, False)]

    configure_logging(settings)
    second = [h for h in root.handlers if getattr(h, _MANAGED, False)]

    # A console handler and a rotating-file handler, never accumulating.
    assert len(first) == 2
    assert len(second) == 2
