"""A connected UniFi **console** (Network Integration API) — key encrypted at rest.

Unlike a Site Manager credential, a console connection points at ONE controller's
hosting URL and its Network API key reaches every site adopted on that console.
An account can connect several consoles (e.g. multiple UDMs / hosting consoles).

The plaintext key is never stored — only the Fernet ciphertext, decrypted in
memory just before an outbound call.
"""
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class UnifiConsole(Base, UUIDPk, Timestamps):
    __tablename__ = "unifi_consoles"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    label: Mapped[str] = mapped_column(default="UniFi Console")
    # Canonical integration base URL, e.g.
    # https://<id>.unifi-hosting.ui.com/proxy/network/integration/v1
    base_url: Mapped[str] = mapped_column(default="")
    encrypted_api_key: Mapped[str] = mapped_column(default="")
    # Last 4 chars kept in clear for display ("…7sJs").
    key_hint: Mapped[str] = mapped_column(default="")
    # UniFi hosting certs don't validate by default; toggle per console.
    verify_tls: Mapped[bool] = mapped_column(default=False)
    last_synced_at: Mapped[str | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(default=None)
