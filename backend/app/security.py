"""Password hashing, generation, and policy.

Hashing uses Argon2id via ``argon2-cffi``. Password generation uses the
``secrets`` module and guarantees the generated value satisfies the policy.
"""

from __future__ import annotations

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12

# Characters used for generated passwords. Excludes ambiguous look-alikes to
# keep printed credentials easy to transcribe.
_GEN_LOWER = "abcdefghijkmnpqrstuvwxyz"
_GEN_UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_GEN_DIGITS = "23456789"
_GEN_SYMBOLS = "!@#$%^&*-_=+"
_GEN_ALPHABET = _GEN_LOWER + _GEN_UPPER + _GEN_DIGITS + _GEN_SYMBOLS


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return False


def validate_password(password: str) -> list[str]:
    """Return a list of human-readable policy violations (empty if valid)."""
    errors: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )
    if not any(c.islower() for c in password):
        errors.append("Password must contain a lowercase letter.")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain an uppercase letter.")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain a digit.")
    return errors


def generate_password(length: int = 16) -> str:
    """Generate a random password that satisfies :func:`validate_password`."""
    if length < MIN_PASSWORD_LENGTH:
        length = MIN_PASSWORD_LENGTH
    while True:
        candidate = "".join(secrets.choice(_GEN_ALPHABET) for _ in range(length))
        if not validate_password(candidate):
            return candidate


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
