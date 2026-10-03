"""Background loop: keep every site's network map + WAN status fresh."""
import asyncio
import logging

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services.topology import refresh_all

logger = logging.getLogger("netmonitor.topology")


async def run_topology() -> None:
    interval = get_settings().topology_interval_seconds
    logger.info("Topology refresher started (every %ss)", interval)
    await asyncio.sleep(20)
    while True:
        try:
            async with SessionLocal() as db:
                await refresh_all(db)
        except Exception:
            logger.exception("Topology refresh pass failed")
        await asyncio.sleep(interval)
