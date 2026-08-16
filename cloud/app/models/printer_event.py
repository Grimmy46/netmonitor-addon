"""A logged ticket-printer status CHANGE for one station. Written only on a
transition (ok → paper_out, cover_open → ok, …), so the table is an event log,
not a per-poll firehose. Powers the per-card history and the printer log report.
"""
import uuid

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class PrinterEvent(Base, UUIDPk, Timestamps):
    __tablename__ = "printer_events"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(default="")       # ok|paper_out|cover_open|error|unknown|removed
    prev_state: Mapped[str | None] = mapped_column(default=None)
    raw: Mapped[str | None] = mapped_column(default=None)
    detail: Mapped[str | None] = mapped_column(default=None)
    # created_at (Timestamps) = when the change was observed.
