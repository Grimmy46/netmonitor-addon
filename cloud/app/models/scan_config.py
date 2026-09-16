"""The Scan Station's category schema, held server-side.

It used to live in each handheld's localStorage, which meant every device kept
its own copy and they drifted — one terminal ended up offering a PAX E700M under
Battery and no amount of reloading fixed it, because the stale data was on the
device, not in the build. One row here is the only copy; handhelds read it on
open and never persist their own."""
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class ScanConfig(Base, UUIDPk, Timestamps):
    __tablename__ = "scan_config"

    # bumped on every save so a device can tell its copy is behind
    version: Mapped[int] = mapped_column(default=1)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
