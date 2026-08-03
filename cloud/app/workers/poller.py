"""Background poller: periodically re-sync the UniFi fleet.

Runs as an asyncio task started in the app lifespan. Silently no-ops until a key
is configured, so it's safe to run from first boot.
"""
import asyncio
import contextlib
import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import UnifiConsole, UnifiCredential
from app.services.sync import sync_all_consoles, sync_unifi
from app.services.unifi import UnifiError

logger = logging.getLogger("netmonitor.poller")


async def _tick() -> None:
    async with SessionLocal() as db:
        # 1) Site Manager credential (account-wide), if one is configured.
        has_key = (await db.execute(select(UnifiCredential))).scalars().first()
        if has_key is not None:
            try:
                result = await sync_unifi(db)
                logger.info(
                    "Site Manager sync: %s sites, %s devices, %s metrics",
                    result["sites"], result["devices"], result["metrics"],
                )
            except Exception as exc:  # noqa: BLE001 — SM failure must not block consoles
                await db.rollback()
                logger.warning("Site Manager sync failed: %s", exc)

        # 2) Every connected console (Network Integration API).
        has_console = (await db.execute(select(UnifiConsole))).scalars().first()
        if has_console is not None:
            result = await sync_all_consoles(db)
            logger.info(
                "Console sync: %s consoles, %s sites, %s devices (%s errors)",
                result["consoles"], result["sites"], result["devices"],
                len(result["errors"]),
            )


async def run_unifi_poller() -> None:
    interval = get_settings().unifi_sync_interval
    logger.info("UniFi poller started (every %ss)", interval)
    while True:
        try:
            await _tick()
        except (UnifiError, Exception) as exc:  # noqa: BLE001 — keep the loop alive
            logger.warning("UniFi poll failed: %s", exc)
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def poller_lifespan(_app):
    task = asyncio.create_task(run_unifi_poller())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
