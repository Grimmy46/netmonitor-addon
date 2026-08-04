"""Encryption helpers for third-party credentials at rest.

The UniFi API key (and any future secrets) are encrypted with Fernet before being
written to the database, so a DB dump never exposes a usable credential.
"""
import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover
        raise ValueError("Could not decrypt value — wrong ENCRYPTION_KEY?") from exc


# ── Agent auth tokens ────────────────────────────────────────────────────────
# Agents authenticate with a bearer token. We store only its SHA-256 hash, so a
# DB leak can't be replayed; the plaintext is shown to the user exactly once.
def make_agent_token() -> str:
    return secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
