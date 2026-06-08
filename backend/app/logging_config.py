"""Centralised logging configuration.

The application owns a single consolidated log file (``logs/app.log`` by
default) so the same records are produced regardless of how the process is
launched: backgrounded by the lifecycle script, in the foreground via
``--dev``, or under a bare ``uvicorn`` invocation. uvicorn's ``error`` and
``access`` loggers are routed through the same handlers so HTTP access lines and
startup/shutdown messages are consolidated into that file too.

Secrets (passwords, generated passwords, password hashes, session identifiers,
CSRF tokens) must never be passed to logging calls elsewhere in the application.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import Settings

# Handlers installed here are tagged so the configuration can be rebuilt
# idempotently. The test-suite builds many app instances, each pointing the log
# file at a fresh temporary directory, so repeated calls must not accumulate
# handlers or leak file descriptors.
_MANAGED = "_app_managed_handler"

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"

# uvicorn's loggers are redirected through the root logger so their output is
# consolidated with the application's own records.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# 5 MiB per file, five rotated backups.
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5


def _resolve_level(name: str) -> int:
    return logging.getLevelNamesMapping().get(name.upper(), logging.INFO)


def _remove_managed_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _MANAGED, False):
            logger.removeHandler(handler)
            handler.close()


def configure_logging(settings: Settings) -> None:
    """Install console + consolidated-file logging. Safe to call repeatedly."""
    level = _resolve_level(settings.log_level)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    root = logging.getLogger()
    _remove_managed_handlers(root)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    setattr(console, _MANAGED, True)
    root.addHandler(console)

    if settings.log_to_file:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.log_file,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _MANAGED, True)
        root.addHandler(file_handler)

    root.setLevel(level)

    # Clear uvicorn's own handlers and let its records propagate to the root
    # handlers, so access/error output lands in the consolidated file.
    for name in _UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    logging.getLogger(__name__).info(
        "Logging configured (level=%s, file=%s)",
        settings.log_level,
        settings.log_file if settings.log_to_file else "disabled",
    )
