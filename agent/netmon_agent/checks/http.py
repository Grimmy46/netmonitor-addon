"""HTTP check — adapted from NetMonitor 1.0 http_test()."""
import time

import httpx


def http_check(url: str, timeout: float = 10.0) -> dict:
    """Return status code and response latency for `url`."""
    result: dict = {"type": "http", "url": url, "ok": False,
                    "status": None, "latency_ms": None, "error": None}
    start = time.perf_counter()
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        result["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        result["status"] = resp.status_code
        result["ok"] = resp.status_code < 400
    except Exception as exc:  # noqa: BLE001
        result["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
        result["error"] = str(exc)
    return result
