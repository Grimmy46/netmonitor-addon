"""The current agent .exe (bootstrapper), uploaded by an admin and served to
kiosks that self-update. Stored in the DB (single active row per account) so it
survives redeploys with no extra volume — the binary is ~10-15 MB, well within
Postgres/TOAST. sha256 is the integrity check the agent verifies before swapping.
"""
import uuid

from sqlalchemy import ForeignKey, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class AgentBinary(Base, UUIDPk, Timestamps):
    __tablename__ = "agent_binaries"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    version: Mapped[str] = mapped_column(default="")           # bootstrap version this exe reports
    sha256: Mapped[str] = mapped_column(default="")
    size: Mapped[int] = mapped_column(default=0)
    filename: Mapped[str] = mapped_column(default="NetMonAgent.exe")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    active: Mapped[bool] = mapped_column(default=True, index=True)
