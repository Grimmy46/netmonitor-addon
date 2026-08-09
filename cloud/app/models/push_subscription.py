"""A browser's Web Push subscription, owned by a signed-in user.

One row per device/browser the user enabled alerts on (phone home-screen app,
desktop Chrome, …). The endpoint is the push service URL and is unique; when a
push service says the subscription is gone (404/410) the row is deleted.
"""
import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.mixins import Timestamps, UUIDPk


class PushSubscription(Base, UUIDPk, Timestamps):
    __tablename__ = "push_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)
    ua: Mapped[str | None] = mapped_column(Text, default=None)
    failures: Mapped[int] = mapped_column(default=0)
