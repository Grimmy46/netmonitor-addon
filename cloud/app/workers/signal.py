"""Pull new Signal messages every few seconds (only while an account is linked)."""
import asyncio
import logging

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services import signal as sig

logger = logging.getLogger("netmonitor.signal")


async def run_signal_receiver() -> None:
    st = get_settings()
    await asyncio.sleep(20)
    while True:
        delay = st.signal_poll_seconds
        try:
            async with SessionLocal() as db:
                cfg = await sig.get_config(db)
                await db.commit()
                if cfg.number or await sig.linked_number(db):
                    await sig.receive_once(db)
                else:
                    delay = 120  # nothing linked yet — check back occasionally
        except Exception as exc:  # noqa: BLE001
            delay = 120
            logger.debug("signal receive skipped: %s", exc)
        await asyncio.sleep(delay)
