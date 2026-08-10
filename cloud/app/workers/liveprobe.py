"""Server-vantage prober for the Live landing page.

Always on: pings the ping-targets (via the system `ping` binary — installed in
the container image) and times HTTPS GETs to the http-targets, storing samples
with agent_id NULL. This is the "cloud view" that keeps the Live charts moving
overnight when the lot (and the designated kiosk) powers down.

Skips the special target "gateway" — a cloud VM's default gateway says nothing
about the carnival's network. Also owns retention: samples older than the
configured window are pruned periodically.
"""
import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models import ProbeSample, ProbeTarget

logger = logging.getLogger("netmonitor.liveprobe")

_TIME_RE = re.compile(r"time[=<]\s*([\d.,]+)\s*ms", re.IGNORECASE)


async def _ping_once(host: str, timeout_s: float = 2.0) -> float | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(max(1, int(round(timeout_s)))), host,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 1.5)
    except (FileNotFoundError, asyncio.TimeoutError, OSError):
        return None
    if proc.returncode != 0:
        return None
    m = _TIME_RE.search(out.decode("utf-8", "ignore"))
    try:
        return float(m.group(1).replace(",", ".")) if m else None
    except ValueError:
        return None


async def _http_once(client: httpx.AsyncClient, url: str) -> float | None:
    """Any HTTP response counts as reachable; ms = total request time."""
    t0 = time.monotonic()
    try:
        await client.get(url)
        return round((time.monotonic() - t0) * 1000.0, 2)
    except Exception:
        return None


async def _tick(client: httpx.AsyncClient) -> int:
    async with SessionLocal() as db:
        targets = list(
            (await db.execute(
                select(ProbeTarget).where(ProbeTarget.enabled.is_(True))
            )).scalars()
        )
        if not targets:
            return 0

        async def probe(t: ProbeTarget):
            if t.kind == "http":
                return t.id, await _http_once(client, t.target)
            if t.target.strip().lower() == "gateway":
                return t.id, ...  # sentinel: not probeable from the cloud
            return t.id, await _ping_once(t.target)

        results = await asyncio.gather(*(probe(t) for t in targets))
        now = datetime.now(tz=timezone.utc)
        stored = 0
        for target_id, ms in results:
            if ms is ...:
                continue
            db.add(ProbeSample(target_id=target_id, agent_id=None, ts=now, ms=ms))
            stored += 1
        if stored:
            await db.commit()
        return stored


async def _prune() -> None:
    st = get_settings()
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=st.live_retention_hours)
    async with SessionLocal() as db:
        await db.execute(delete(ProbeSample).where(ProbeSample.ts < cutoff))
        await db.commit()


async def run_live_prober() -> None:
    st = get_settings()
    interval = max(2.0, st.live_server_probe_interval_seconds)
    logger.info("Live prober started (every %ss, retention %sh)",
                interval, st.live_retention_hours)
    last_prune = 0.0
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        while True:
            t0 = time.monotonic()
            try:
                await _tick(client)
            except Exception as exc:  # noqa: BLE001 — keep the loop alive
                logger.warning("Live probe tick failed: %s", exc)
            if time.monotonic() - last_prune > 600:
                last_prune = time.monotonic()
                try:
                    await _prune()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Live probe prune failed: %s", exc)
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.5, interval - elapsed))
