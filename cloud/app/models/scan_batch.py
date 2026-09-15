"""A batch of devices captured by the Scan Station and uploaded when its CSV
was built. The CSV text is stored verbatim so what you download later is byte
for byte what the handheld produced; `rows` is the same data parsed, for the
viewer. Batches are a record of a counting run, not a device inventory — the
inventory app remains the system of record."""
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class ScanBatch(Base, UUIDPk, Timestamps):
    __tablename__ = "scan_batches"

    # Who/what sent it — a free-text label the operator sets on the handheld.
    scanner: Mapped[str] = mapped_column(default="")
    note: Mapped[str] = mapped_column(default="")
    row_count: Mapped[int] = mapped_column(default=0)
    # {"battery": 12, "scanner": 30} — cheap enough to store, saves the viewer
    # walking every row just to show a one-line summary.
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    # The exact CSV the handheld built, including its header row.
    csv_text: Mapped[str] = mapped_column(default="")
    # The same rows as objects, for rendering without re-parsing the CSV.
    rows: Mapped[list] = mapped_column(JSONB, default=list)
    # Set once the batch has been pushed into the inventory app, so a run is
    # never imported twice (duplicate asset tags are rejected on import).
    # timezone=True to match migration 0026 — on a fresh DB this table is built
    # from the model (0001 does create_all), on an existing one from the migration.
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
