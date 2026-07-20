"""Scheduled UniFi sync poller (Phase 1 will wire this into app startup).

Kept as a standalone coroutine now so the scheduling strategy (interval, backoff,
per-account fan-out) can be designed without touching request handlers.
"""
import asyncio
import logging

logger = logging.getLogger("netmonitor.poller")

DEFAULT_INTERVAL_SECONDS = 60


async def run_unifi_poller(interval: int = DEFAULT_INTERVAL_SECONDS) -> None:
    """Placeholder loop. Phase 1: call the /integrations/unifi/sync logic here."""
    while True:
        logger.info("UniFi poll tick (Phase 1: perform sync)")
        await asyncio.sleep(interval)
