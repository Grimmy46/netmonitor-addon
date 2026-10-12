"""Server health for the Server tab: host CPU / memory / disk / load, the
NetMonitor containers' reachability, database size, and the camera relay.

Read from inside the cloud container: /proc/stat, /proc/meminfo, /proc/loadavg
and /proc/uptime are the host's (Docker doesn't virtualise them), and "/" is the
overlay on the host disk, so disk_usage("/") is the host's root disk.

A small in-memory sampler keeps the last 24 h at one point per minute for the
charts. It restarts empty after a deploy — that's fine for a health glance.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
from collections import deque

logger = logging.getLogger("netmonitor.serverstats")

SAMPLE_S = 60
HISTORY = deque(maxlen=24 * 60)  # (ts, cpu %, mem used %, load1)
_last_cpu: tuple[int, int] | None = None
_proc_start = time.time()


def _cpu_times() -> tuple[int, int] | None:
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = [int(x) for x in parts]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return sum(vals), idle
    except Exception:  # noqa: BLE001
        return None


def cpu_percent() -> float | None:
    """Host CPU busy % since the previous call (first call: since boot)."""
    global _last_cpu
    now = _cpu_times()
    if now is None:
        return None
    prev = _last_cpu or (0, 0)
    _last_cpu = now
    dt, di = now[0] - prev[0], now[1] - prev[1]
    return round(100.0 * (dt - di) / dt, 1) if dt > 0 else None


def meminfo() -> dict:
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                out[k] = int(v.split()[0]) * 1024
    except Exception:  # noqa: BLE001
        return {}
    total = out.get("MemTotal", 0)
    avail = out.get("MemAvailable", 0)
    return {"total": total, "available": avail, "used": total - avail,
            "used_pct": round(100.0 * (total - avail) / total, 1) if total else None,
            "swap_total": out.get("SwapTotal", 0), "swap_used": out.get("SwapTotal", 0) - out.get("SwapFree", 0)}


def loadavg() -> list[float]:
    try:
        with open("/proc/loadavg") as f:
            return [float(x) for x in f.read().split()[:3]]
    except Exception:  # noqa: BLE001
        return []


def host_uptime_s() -> int | None:
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except Exception:  # noqa: BLE001
        return None


def disk() -> dict:
    try:
        u = shutil.disk_usage("/")
        return {"total": u.total, "used": u.used, "free": u.free, "used_pct": round(100.0 * u.used / u.total, 1)}
    except Exception:  # noqa: BLE001
        return {}


def app_memory() -> int | None:
    """This container's memory (cgroup v2), else the process RSS."""
    for p in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        with contextlib.suppress(Exception):
            with open(p) as f:
                return int(f.read().strip())
    with contextlib.suppress(Exception):
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    return None


def sample() -> None:
    cpu = cpu_percent()
    m = meminfo()
    la = loadavg()
    HISTORY.append((int(time.time()), cpu, m.get("used_pct"), la[0] if la else None))


async def run_server_sampler() -> None:
    cpu_percent()  # prime the delta
    while True:
        await asyncio.sleep(SAMPLE_S)
        try:
            sample()
        except Exception:  # noqa: BLE001
            logger.exception("server sample failed")


def snapshot() -> dict:
    m = meminfo()
    last = HISTORY[-1] if HISTORY else None
    return {
        "cpus": os.cpu_count(),
        "cpu_pct": last[1] if last else None,
        "load": loadavg(),
        "memory": m,
        "disk": disk(),
        "host_uptime_s": host_uptime_s(),
        "app_uptime_s": int(time.time() - _proc_start),
        "app_memory": app_memory(),
        "history": [{"t": t, "cpu": c, "mem": mm, "load": l} for t, c, mm, l in HISTORY],
    }
