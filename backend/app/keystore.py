"""At-rest encryption for SSH private keys (issue_015-r1).

A single master key encrypts secret material stored in the database
(registered SSH private keys and per-user keypairs). The master key is
resolved once per process:

1. ``APP_MASTER_KEY`` env var (a urlsafe-base64 Fernet key), if set; else
2. ``data/master.key`` (auto-generated at 0600 on first use).

Fernet provides authenticated symmetric encryption. Ciphertext is stored as
an ASCII token prefixed with ``enc:v1:`` so encrypted values are
self-describing and plaintext-vs-ciphertext is unambiguous during
migrations.
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_PREFIX = "enc:v1:"


class MasterKeyError(RuntimeError):
    """Raised when the master key cannot be resolved or is invalid."""


def _load_or_create_key() -> bytes:
    settings = get_settings()
    if settings.master_key_env:
        key = settings.master_key_env.encode("ascii")
        _validate_key(key)
        return key
    path = settings.master_key_file
    if path.exists():
        key = path.read_bytes().strip()
        _validate_key(key)
        return key
    # Auto-generate on first use, created 0600 atomically (no world-readable
    # window) in the (0700) data dir.
    settings.ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def _validate_key(key: bytes) -> None:
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise MasterKeyError(
            "APP_MASTER_KEY / master.key is not a valid Fernet key"
        ) from exc


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    return Fernet(_load_or_create_key())


def reset_cache() -> None:
    """Clear the cached Fernet (tests change env/key between cases)."""
    _fernet.cache_clear()


def is_encrypted(value: str) -> bool:
    return value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    """Encrypt a secret string, returning an ``enc:v1:`` token.

    Empty input returns empty (nothing to protect). Already-encrypted input
    is returned unchanged so callers/migrations are idempotent.
    """
    if plaintext == "":
        return ""
    if is_encrypted(plaintext):
        return plaintext
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(value: str) -> str:
    """Decrypt an ``enc:v1:`` token; pass through plaintext/empty unchanged.

    Passing plaintext through (rather than raising) lets callers read rows
    that predate encryption during a rolling migration.
    """
    if value == "" or not is_encrypted(value):
        return value
    token = value[len(_PREFIX):].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise MasterKeyError(
            "stored secret could not be decrypted with the current master key"
        ) from exc
