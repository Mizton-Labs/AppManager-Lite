"""Per-user SSH keypair generation.

Each user account carries its own Ed25519 SSH keypair, generated at user
creation (and backfilled by migration for pre-existing accounts). Key material
is stored only in the ``users`` table and returned only by the owner-gated
account endpoints; it must never be logged or written to audit entries.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# Comments may end up on remote ``authorized_keys`` lines; keep them to a safe
# characterset regardless of what the username contains.
_SAFE_COMMENT_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.@-_"
)


def _safe_comment(comment: str) -> str:
    return "".join(ch for ch in comment if ch in _SAFE_COMMENT_CHARS)[:64]


def generate_keypair(comment: str = "") -> tuple[str, str]:
    """Return ``(private_key_openssh, public_key_openssh)`` for a new key.

    The private key is PEM/OpenSSH-encoded without a passphrase (it is served
    only to the owning, authenticated user). The public key is a standard
    single-line ``ssh-ed25519 AAAA... comment`` entry.
    """
    key = Ed25519PrivateKey.generate()
    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_key = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode("ascii")
    )
    safe = _safe_comment(comment)
    if safe:
        public_key = f"{public_key} {safe}"
    return private_key, public_key


class SshKeyError(ValueError):
    """Raised for malformed key material supplied by an administrator."""


def public_key_from_private(private_key_pem: str) -> str:
    """Derive the OpenSSH public key line from an OpenSSH/PEM private key.

    Accepts unencrypted private keys only (a passphrase-protected key cannot
    be used non-interactively by the app). Raises ``SshKeyError`` on any
    parse failure so the caller can return a 400.
    """
    try:
        key = serialization.load_ssh_private_key(
            private_key_pem.encode("utf-8"), password=None
        )
    except ValueError:
        try:
            key = serialization.load_pem_private_key(
                private_key_pem.encode("utf-8"), password=None
            )
        except (ValueError, TypeError) as exc:
            raise SshKeyError(
                "Could not parse the private key. Provide an unencrypted "
                "OpenSSH private key (no passphrase)."
            ) from exc
    except TypeError as exc:
        raise SshKeyError(
            "The private key appears to be passphrase-protected; provide an "
            "unencrypted key."
        ) from exc
    try:
        return (
            key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.OpenSSH,
                format=serialization.PublicFormat.OpenSSH,
            )
            .decode("ascii")
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise SshKeyError("Unsupported key type.") from exc


def fingerprint(public_key: str) -> str:
    """SHA256 fingerprint of an OpenSSH public key (``SHA256:...`` form)."""
    parts = public_key.split()
    if len(parts) < 2:
        return ""
    try:
        blob = base64.b64decode(parts[1])
    except (ValueError, base64.binascii.Error):
        return ""
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
