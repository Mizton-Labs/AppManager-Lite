"""Administrative bootstrap and CLI.

Provides first-run admin provisioning and an out-of-band admin password reset
that can run without starting the web server. The generated password is written
only to a ``0600`` credentials file and is never written to logs.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from . import repository, security, sessions
from .config import get_settings
from .db import get_connection, init_db

ADMIN_USERNAME = "admin"

logger = logging.getLogger(__name__)


def _write_credentials_file(password: str) -> None:
    settings = get_settings()
    settings.ensure_dirs()
    path = settings.credentials_file
    # Create with restrictive permissions before writing any content.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(
                "AppManager Lite - first-run administrator credentials\n"
                f"username: {ADMIN_USERNAME}\n"
                f"password: {password}\n"
                "\n"
                "You must change this password at first login.\n"
                "Delete this file once the password has been changed.\n"
            )
    finally:
        # os.fdopen closed fd; ensure mode is correct even if umask interfered.
        os.chmod(path, 0o600)


def ensure_first_run_admin() -> str | None:
    """Create the initial admin if no users exist. Returns the path if created."""
    init_db()
    with get_connection() as conn:
        if repository.count_users(conn) > 0:
            return None
        password = security.generate_password()
        repository.create_user(
            conn,
            username=ADMIN_USERNAME,
            password=password,
            role="admin",
            teams=[],
            must_change_password=True,
            self_service=True,
        )
    _write_credentials_file(password)
    logger.info(
        "First-run administrator provisioned username=%r; credentials written to %s",
        ADMIN_USERNAME,
        get_settings().credentials_file,
    )
    return str(get_settings().credentials_file)


def reset_admin_password() -> str:
    """Reset the admin password out-of-band and force a change on next login."""
    init_db()
    with get_connection() as conn:
        row = repository.get_user_by_username(conn, ADMIN_USERNAME)
        password = security.generate_password()
        if row is None:
            repository.create_user(
                conn,
                username=ADMIN_USERNAME,
                password=password,
                role="admin",
                teams=[],
                must_change_password=True,
                self_service=True,
            )
        else:
            repository.set_password(
                conn, row["id"], password, must_change_password=True
            )
            sessions.delete_user_sessions(conn, row["id"])
    _write_credentials_file(password)
    logger.info("Administrator password reset out-of-band username=%r", ADMIN_USERNAME)
    return password


def _cmd_reset_admin_password(_: argparse.Namespace) -> int:
    password = reset_admin_password()
    settings = get_settings()
    # Print the password to the operator's terminal (interactive, out-of-band),
    # and persist it to the 0600 credentials file.
    print("Admin password has been reset.")
    print(f"username: {ADMIN_USERNAME}")
    print(f"password: {password}")
    print(f"(also written to {settings.credentials_file})")
    print("The admin must change this password at next login.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.admin", description="Admin utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    p_reset = sub.add_parser(
        "reset-admin-password", help="Reset the admin password and force a change"
    )
    p_reset.set_defaults(func=_cmd_reset_admin_password)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
