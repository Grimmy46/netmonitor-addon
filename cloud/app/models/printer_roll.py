"""Paper-roll changes (one row per real change) and per-day print totals."""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class PrinterRoll(Base, UUIDPk, Timestamps):
    __tablename__ = "printer_rolls"

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    how: Mapped[str] = mapped_column(String(16))                 # ran_out | swapped | manual | inferred
    out_seconds: Mapped[int | None] = mapped_column(default=None)
    prev_roll_tickets: Mapped[int | None] = mapped_column(default=None)
    prev_roll_cm: Mapped[int | None] = mapped_column(default=None)
    prev_roll_partial: Mapped[bool] = mapped_column(default=False)
    cut_count: Mapped[int | None] = mapped_column(default=None)
    paper_cm: Mapped[int | None] = mapped_column(default=None)


class PrinterDaily(Base):
    __tablename__ = "printer_daily"

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    first_cuts: Mapped[int | None] = mapped_column(default=None)
    last_cuts: Mapped[int | None] = mapped_column(default=None)
    first_cm: Mapped[int | None] = mapped_column(default=None)
    last_cm: Mapped[int | None] = mapped_column(default=None)
    prev_last_cuts: Mapped[int | None] = mapped_column(default=None)
    prev_last_cm: Mapped[int | None] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
