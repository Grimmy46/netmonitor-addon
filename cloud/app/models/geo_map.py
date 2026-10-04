"""The live satellite MAP for a site: where the map is, where each switch/AP
physically sits (keyed by MAC, so UniFi re-syncs never lose a pin), and an
optional view-only share link."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class GeoMap(UUIDPk, Timestamps, Base):
    __tablename__ = "geo_maps"

    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), unique=True)
    center_lat: Mapped[float | None] = mapped_column(Float, default=None)
    center_lng: Mapped[float | None] = mapped_column(Float, default=None)
    zoom: Mapped[float | None] = mapped_column(Float, default=None)  # fractional zoom
    # {"<mac>": {"lat": .., "lng": ..}}
    placements: Mapped[dict] = mapped_column(JSONB, default=dict)
    share_token: Mapped[str | None] = mapped_column(String, unique=True, default=None)
    share_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Optional background image (site plan / drone shot) instead of / over the
    # satellite: raw bytes + 3 anchor corners (tl, tr, bl as [lat, lng]) which
    # give move/scale/rotate, plus opacity.
    bg_image: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    bg_mime: Mapped[str | None] = mapped_column(String, default=None)
    bg_corners: Mapped[dict | None] = mapped_column(JSONB, default=None)
    bg_opacity: Mapped[float | None] = mapped_column(Float, default=None)
    bg_version: Mapped[int] = mapped_column(Integer, default=0)
