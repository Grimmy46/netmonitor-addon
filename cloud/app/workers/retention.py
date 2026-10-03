"""Ping-history retention: roll raw kiosk pings up, then prune them.

ping_samples gets one row per kiosk per second (~4-5M rows/day in season) and
was never pruned, so it would eventually fill the server's disk. This worker:

1. Rolls raw samples into per-minute buckets (ping_rollup_1m). The trailing
   couple of hours are recomputed on every pass, so samples that a kiosk
   buffered while offline and uploaded late still land in the right minute.
2. Rolls minute buckets into per-hour buckets (ping_rollup_1h).
3. Deletes raw samples older than `ping_raw_retention_days`, and minute rollups
   older than `ping_minute_retention_days`. It only ever deletes data that has
   already been rolled up, and deletes in small batches so live ingest isn't
   blocked.

On first start it works through the whole backlog in small chunks, then
settles into one pass every `ping_retention_interval_seconds`.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal

logger = logging.getLogger("netmonitor.retention")

MINUTE_LOOKBACK = timedelta(hours=2)
HOUR_LOOKBACK = timedelta(hours=3)
MINUTE_CHUNK = timedelta(hours=6)
HOUR_CHUNK = timedelta(days=7)
DELETE_BATCH = 20_000
DELETE_BATCHES_PER_PASS = 10
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_ROLLUP_1M = text("""
    INSERT INTO ping_rollup_1m
        (agent_id, bucket, n, ok, avg_rtt_ms, min_rtt_ms, max_rtt_ms, avg_gw_ms, gw_n)
    SELECT agent_id, date_trunc('minute', ts), count(*), count(rtt_ms),
           avg(rtt_ms), min(rtt_ms), max(rtt_ms),
           avg(gateway_rtt_ms), count(gateway_rtt_ms)
    FROM ping_samples
    WHERE ts >= :start AND ts < :end
    GROUP BY 1, 2
    ON CONFLICT (agent_id, bucket) DO UPDATE SET
        n = EXCLUDED.n, ok = EXCLUDED.ok,
        avg_rtt_ms = EXCLUDED.avg_rtt_ms, min_rtt_ms = EXCLUDED.min_rtt_ms,
        max_rtt_ms = EXCLUDED.max_rtt_ms, avg_gw_ms = EXCLUDED.avg_gw_ms,
        gw_n = EXCLUDED.gw_n
""")

_ROLLUP_1H = text("""
    INSERT INTO ping_rollup_1h
        (agent_id, bucket, n, ok, avg_rtt_ms, min_rtt_ms, max_rtt_ms, avg_gw_ms, gw_n)
    SELECT agent_id, date_trunc('hour', bucket), sum(n), sum(ok),
           sum(avg_rtt_ms * ok) / nullif(sum(ok), 0),
           min(min_rtt_ms), max(max_rtt_ms),
           sum(avg_gw_ms * gw_n) / nullif(sum(gw_n), 0), sum(gw_n)
    FROM ping_rollup_1m
    WHERE bucket >= :start AND bucket < :end
    GROUP BY 1, 2
    ON CONFLICT (agent_id, bucket) DO UPDATE SET
        n = EXCLUDED.n, ok = EXCLUDED.ok,
        avg_rtt_ms = EXCLUDED.avg_rtt_ms, min_rtt_ms = EXCLUDED.min_rtt_ms,
        max_rtt_ms = EXCLUDED.max_rtt_ms, avg_gw_ms = EXCLUDED.avg_gw_ms,
        gw_n = EXCLUDED.gw_n
""")


def _trunc_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _trunc_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


async def _scalar(db, sql: str):
    return (await db.execute(text(sql))).scalar()


async def _rollup_minutes(db, now: datetime) -> bool:
    """Roll one chunk of raw samples into minute buckets. True = more backlog."""
    hi = _trunc_minute(now) + timedelta(minutes=1)   # include the current minute
    wm = await _scalar(db, "SELECT max(bucket) FROM ping_rollup_1m")
    # Jump straight to the first raw sample after the last rolled-up minute, so
    # a long quiet gap (whole fleet dark between venues) is skipped, not crawled.
    # Once caught up, the trailing MINUTE_LOOKBACK is recomputed every pass.
    nxt = (await db.execute(
        text("SELECT min(ts) FROM ping_samples WHERE ts >= :after"),
        {"after": wm + timedelta(minutes=1) if wm else _EPOCH},
    )).scalar()
    if nxt is None:
        return False
    start = min(_trunc_minute(nxt), hi - MINUTE_LOOKBACK)
    end = min(start + MINUTE_CHUNK, hi)
    await db.execute(_ROLLUP_1M, {"start": start, "end": end})
    await db.commit()
    return end < hi


async def _rollup_hours(db, now: datetime) -> bool:
    """Roll one chunk of minute buckets into hour buckets. True = more backlog."""
    covered = await _scalar(db, "SELECT max(bucket) FROM ping_rollup_1m")
    if covered is None:
        return False
    # Only complete hours whose minutes have all been rolled up.
    hi = min(_trunc_hour(now), _trunc_hour(covered))
    wm = await _scalar(db, "SELECT max(bucket) FROM ping_rollup_1h")
    nxt = (await db.execute(
        text("SELECT min(bucket) FROM ping_rollup_1m WHERE bucket >= :after"),
        {"after": wm + timedelta(hours=1) if wm else _EPOCH},
    )).scalar()
    if nxt is None:
        return False
    start = min(_trunc_hour(nxt), hi - HOUR_LOOKBACK)
    end = min(start + HOUR_CHUNK, hi)
    if end <= start:
        return False
    await db.execute(_ROLLUP_1H, {"start": start, "end": end})
    await db.commit()
    return end < hi


async def _batched_delete(db, table: str, col: str, cutoff: datetime) -> tuple[int, bool]:
    """Delete rows older than cutoff in small batches. Returns (deleted, more_left)."""
    sql = text(f"""
        DELETE FROM {table} WHERE ctid = ANY(ARRAY(
            SELECT ctid FROM {table} WHERE {col} < :cutoff LIMIT {DELETE_BATCH}
        ))
    """)
    total = 0
    for _ in range(DELETE_BATCHES_PER_PASS):
        n = (await db.execute(sql, {"cutoff": cutoff})).rowcount or 0
        await db.commit()
        total += n
        if n < DELETE_BATCH:
            return total, False
        await asyncio.sleep(0.2)   # let live ingest breathe between batches
    return total, True


async def _prune(db, now: datetime) -> bool:
    st = get_settings()
    more = False
    # Raw samples: only delete what the minute rollup already covers.
    covered_1m = await _scalar(db, "SELECT max(bucket) FROM ping_rollup_1m")
    if covered_1m is not None:
        cutoff = min(now - timedelta(days=st.ping_raw_retention_days),
                     covered_1m - MINUTE_LOOKBACK)
        n, m = await _batched_delete(db, "ping_samples", "ts", cutoff)
        more |= m
        if n:
            logger.info("Pruned %s raw ping samples older than %s", n, cutoff.isoformat())
    # Minute rollups: only delete what the hour rollup already covers.
    covered_1h = await _scalar(db, "SELECT max(bucket) FROM ping_rollup_1h")
    if covered_1h is not None:
        cutoff = min(now - timedelta(days=st.ping_minute_retention_days),
                     covered_1h - HOUR_LOOKBACK)
        n, m = await _batched_delete(db, "ping_rollup_1m", "bucket", cutoff)
        more |= m
        if n:
            logger.info("Pruned %s minute rollups older than %s", n, cutoff.isoformat())
    return more


async def retention_pass() -> bool:
    """One unit of work. Returns True while there is still backlog to chew."""
    now = datetime.now(tz=timezone.utc)
    async with SessionLocal() as db:
        more = await _rollup_minutes(db, now)
        more |= await _rollup_hours(db, now)
        more |= await _prune(db, now)
    return more


async def run_retention() -> None:
    st = get_settings()
    logger.info("Ping retention started (raw %sd, minute rollups %sd, hour rollups forever)",
                st.ping_raw_retention_days, st.ping_minute_retention_days)
    await asyncio.sleep(30)   # let the app finish booting first
    while True:
        try:
            backlog = await retention_pass()
        except Exception:
            logger.exception("Ping retention pass failed")
            backlog = False
        await asyncio.sleep(2 if backlog else st.ping_retention_interval_seconds)
