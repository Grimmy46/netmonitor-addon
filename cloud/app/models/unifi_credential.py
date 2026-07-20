"""Stored UniFi Site Manager API key — encrypted at rest.

The plaintext key is NEVER stored; only the Fernet ciphertext. Decryption happens
in-memory just before an outbound call to api.ui.com.
"""
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class UnifiCredential(Base, UUIDPk, Timestamps):
    __tablename__ = "unifi_credentials"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    label: Mapped[str] = mapped_column(default="UniFi Site Manager")
    encrypted_api_key: Mapped[str]
    # Last 4 chars kept in clear for display ("…EHCH") so users can identify the key.
    key_hint: Mapped[str] = mapped_column(default="")
    last_synced_at: Mapped[str | None] = mapped_column(default=None)
