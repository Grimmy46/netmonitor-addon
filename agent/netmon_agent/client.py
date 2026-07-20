"""Outbound client — pushes batched results to the cloud, buffers on failure.

The agent NEVER accepts inbound connections. It only dials out to the cloud, so
it works behind NAT/CGNAT with no port forwarding.
"""
import logging
from collections import deque

import httpx

from netmon_agent.config import AgentSettings

logger = logging.getLogger("netmonitor.agent.client")


class CloudClient:
    def __init__(self, settings: AgentSettings, buffer_max: int = 5000):
        self._settings = settings
        self._buffer: deque[dict] = deque(maxlen=buffer_max)

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._settings.agent_token}"}

    def enqueue(self, results: list[dict]) -> None:
        self._buffer.extend(results)

    async def flush(self) -> bool:
        """Try to send everything buffered. Keep it on failure for next cycle."""
        if not self._buffer:
            return True
        batch = list(self._buffer)
        url = f"{self._settings.cloud_url.rstrip('/')}/agents/results"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json={"results": batch}, headers=self._headers)
                resp.raise_for_status()
            self._buffer.clear()
            logger.info("Flushed %d results to cloud", len(batch))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cloud push failed (%s) — buffering %d results", exc, len(batch))
            return False
