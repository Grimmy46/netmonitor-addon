"""A fleet-wide action (test print all / power off all) and who asked for it.
Each kiosk's part is an AgentCommand with args.batch = this id."""
from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class FleetBatch(UUIDPk, Timestamps, Base):
    __tablename__ = "fleet_batches"

    kind: Mapped[str] = mapped_column(String(32))
    requested_by: Mapped[str] = mapped_column(String, default="")
    total: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[list] = mapped_column(JSONB, default=list)  # [{id, name, why}]
