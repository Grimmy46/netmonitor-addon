"""Agent entrypoint — run the check cycle and push results outbound.

Phase 0: a working skeleton with ping/http checks against a static demo target
list, buffering to the cloud. Phase 2: pull the target list + config from the
cloud, add api/traceroute/bandwidth, enrollment, and auto-update.
"""
import asyncio
import logging

from netmon_agent import __version__
from netmon_agent.checks import http_check, ping_check
from netmon_agent.client import CloudClient
from netmon_agent.config import AgentSettings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("netmonitor.agent")

# Placeholder targets until config is pulled from the cloud (Phase 2).
DEMO_PING = ["8.8.8.8", "1.1.1.1"]
DEMO_HTTP = ["https://www.google.com", "https://www.cloudflare.com"]


async def run_cycle(client: CloudClient) -> None:
    results = [ping_check(h) for h in DEMO_PING] + [http_check(u) for u in DEMO_HTTP]
    client.enqueue(results)
    await client.flush()


async def run() -> None:
    settings = AgentSettings()
    client = CloudClient(settings)
    logger.info("NetMonitor agent %s starting (name=%s, cloud=%s)",
                __version__, settings.agent_name, settings.cloud_url)
    while True:
        try:
            await run_cycle(client)
        except Exception:  # noqa: BLE001
            logger.exception("Check cycle failed")
        await asyncio.sleep(settings.interval)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Agent stopped.")


if __name__ == "__main__":
    main()
