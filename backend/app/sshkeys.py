"""Per-user SSH keypair generation.

Each user account carries its own Ed25519 SSH keypair, generated at user
creation (and backfilled by migration for pre-existing accounts). Key material
is stored only in the ``users`` table and returned only by the owner-gated
account endpoints; it must never be logged or written to audit entries.
"""

from __future__ import annotations

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
