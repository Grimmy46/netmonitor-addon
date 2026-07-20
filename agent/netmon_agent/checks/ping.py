"""Ping check — adapted from NetMonitor 1.0 ping_test()."""
import platform
import re
import subprocess


def ping_check(host: str, count: int = 4) -> dict:
    """Return latency (ms) and packet loss (%) for `host`."""
    system = platform.system().lower()
    count_flag = "-n" if system == "windows" else "-c"
    result: dict = {"type": "ping", "host": host, "ok": False,
                    "latency_ms": None, "loss_pct": None, "error": None}
    try:
        proc = subprocess.run(
            ["ping", count_flag, str(count), host],
            capture_output=True, text=True, timeout=count * 2 + 5,
        )
        out = proc.stdout
        # Average latency: "min/avg/max/mdev = 12.3/14.5/..." (Linux/macOS).
        m = re.search(r"=\s*[\d.]+/([\d.]+)/", out)
        if m:
            result["latency_ms"] = float(m.group(1))
        # Packet loss: "0% packet loss".
        m = re.search(r"([\d.]+)%\s*packet loss", out)
        if m:
            result["loss_pct"] = float(m.group(1))
        result["ok"] = proc.returncode == 0 and (result["loss_pct"] or 0) < 100
    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result
