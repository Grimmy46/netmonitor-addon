"""A queued remote command for one agent — the audit log IS the queue.

Lifecycle: queued → sent (delivered inside a report response) → done | error.
Only allow-listed kinds exist (see ALLOWED_COMMAND_KINDS in the agents routes);
the agent independently refuses anything it doesn't recognize. requested_by
records the admin's email for the audit trail.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class AgentCommand(Base, UUIDPk, Timestamps):
    __tablename__ = "agent_commands"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    args: Mapped[dict | None] = mapped_column(JSONB, default=None)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    requested_by: Mapped[str] = mapped_column(default="")
    result: Mapped[dict | None] = mapped_column(JSONB, default=None)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
