"""A SitePlanner project stored per site: the plan JSON (markers, runs, Q
blocks, calibration) plus the aerial background image, stored separately so
plan saves don't re-upload megabytes of photo."""
import uuid

from sqlalchemy import ForeignKey, LargeBinary
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class SitePlan(Base, UUIDPk, Timestamps):
    __tablename__ = "site_plans"

    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(default="Site plan")
    # SitePlanner's own project schema version (it owns the format; we never migrate it).
    schema_version: Mapped[int] = mapped_column(default=4)
    # The project JSON WITHOUT the aerial image (imageDataUrl stripped client-side).
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    # The aerial photo, raw bytes + mime (nullable — plans can exist before a photo).
    aerial: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    aerial_mime: Mapped[str | None] = mapped_column(default=None)
