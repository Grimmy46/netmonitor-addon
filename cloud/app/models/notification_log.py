"""Every push the server tried to send — so 'did it alert?' has an answer."""
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class NotificationLog(UUIDPk, Timestamps, Base):
    __tablename__ = "notification_log"

    title: Mapped[str] = mapped_column(String, default="")
    body: Mapped[str] = mapped_column(String, default="")
    tag: Mapped[str | None] = mapped_column(String, default=None)
    url: Mapped[str | None] = mapped_column(String, default=None)
    devices: Mapped[int] = mapped_column(Integer, default=0)   # subscriptions targeted
    delivered: Mapped[int] = mapped_column(Integer, default=0)  # accepted by Apple/Google
    error: Mapped[str | None] = mapped_column(String, default=None)
