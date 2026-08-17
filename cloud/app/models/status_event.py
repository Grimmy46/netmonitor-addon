"""Append-only log of site online↔offline transitions.

This is the raw teardown/build sequence: the ORDER in which sites (UXG venues)
go dark when packing up, and come back when setting up at the next event. It's
the seed data for the teardown run sheet and the ASF build sheet.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import UUIDPk


class StatusEvent(Base, UUIDPk):
    __tablename__ = "status_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), default=None
    )
    name: Mapped[str] = mapped_column(String, default="")      # site name at the time
    kind: Mapped[str] = mapped_column(String, default="site")  # site (device later)
    event: Mapped[str] = mapped_column(String, default="")     # offline | online
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_status_events_account_ts", "account_id", "ts"),)
