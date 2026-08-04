#!/usr/bin/env python3
"""NetMonitor kiosk agent — Phase 1 (ping reporting).

Runs on each kiosk / site PC and pushes ping samples to the NetMonitor cloud on
a heartbeat. Uses ONLY the Python standard library, so a bundled .exe (Python
baked in) needs nothing installed on the kiosk.

Config: netmon_agent.config.json next to this file
(copy netmon_agent.config.example.json and paste the agent token from the
dashboard → Settings → Agents → Add).

Later phases add system telemetry (CPU/RAM/disk/POS) and remote commands over the
same connection; the report response already carries a `commands` list.
"""
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM = platform.system()
AGENT_VERSION = "2.0"
_TIME_RE = re.compile(r"time[=<]\s*([\d.,]+)\s*ms", re.IGNORECASE)
_MS_RE = re.compile(r"([\d.,]+)\s*ms", re.IGNORECASE)


def load_config():
    path = os.path.join(HERE, "netmon_agent.config.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("interval", 1.0)          # seconds between pings
    cfg.setdefault("post_interval", 30.0)    # seconds between server POSTs
    cfg.setdefault("timeout", 2.0)
    cfg.setdefault("gateway", "auto")        # "" = skip; "auto" = detect
    cfg.setdefault("max_buffer", 5000)       # cap if server unreachable
    if not cfg.get("server_url") or not cfg.get("token"):
        raise SystemExit("Config needs 'server_url' and 'token'. "
                         "Get the token from the dashboard → Settings → Agents.")
    if not cfg.get("target"):
        raise SystemExit("Config needs a 'target' to ping (e.g. rcs.funcardapp.com).")
    return cfg


def _no_window_kwargs():
    if SYSTEM == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def detect_gateway():
    try:
        if SYSTEM == "Windows":
            out = subprocess.run(["ipconfig"], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=6,
                                 **_no_window_kwargs()).stdout.decode("utf-8", "ignore")
            for line in out.splitlines():
                if "Default Gateway" in line:
                    m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                    if m and not m.group(1).startswith("0."):
                        return m.group(1)
        elif SYSTEM == "Darwin":
            out = subprocess.run(["route", "-n", "get", "default"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=6).stdout.decode("utf-8", "ignore")
            m = re.search(r"gateway:\s*([\d.]+)", out)
            return m.group(1) if m else ""
        else:
            out = subprocess.run(["ip", "route"], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=6
                                 ).stdout.decode("utf-8", "ignore")
            m = re.search(r"default via ([\d.]+)", out)
            return m.group(1) if m else ""
    except Exception:
        return ""
    return ""


def ping_once(host, timeout_s):
    if not host:
        return None
    if SYSTEM == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), host]
    elif SYSTEM == "Darwin":
        cmd = ["ping", "-c", "1", host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(round(timeout_s)))), host]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout_s + 1.0, **_no_window_kwargs())
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.decode("utf-8", "ignore")
    m = _TIME_RE.search(out) or _MS_RE.search(out)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def post(cfg, samples):
    payload = json.dumps({
        "target": cfg["target"],
        "gateway": cfg.get("_gw_ip", ""),
        "hostname": cfg["_hostname"],
        "os": cfg["_os"],
        "agent_version": AGENT_VERSION,
        "samples": samples,
    }).encode("utf-8")
    req = urllib.request.Request(
        cfg["server_url"].rstrip("/") + "/agents/report",
        data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Agent-Token": cfg["token"]})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", "ignore")
    try:
        return json.loads(body)
    except ValueError:
        return {}


def main():
    cfg = load_config()
    cfg["_hostname"] = (os.environ.get("COMPUTERNAME")
                        or socket.gethostname() or "unknown").strip()
    cfg["_os"] = f"{platform.system()} {platform.release()}".strip()
    gw = cfg.get("gateway", "auto")
    cfg["_gw_ip"] = detect_gateway() if gw == "auto" else gw
    interval = max(0.2, float(cfg["interval"]))
    post_every = max(5.0, float(cfg["post_interval"]))
    to = min(4.0, max(1.0, float(cfg["timeout"])))

    print(f"[netmon-agent] {cfg['_hostname']} target={cfg['target']} "
          f"gateway={cfg['_gw_ip'] or 'none'} -> {cfg['server_url']}", flush=True)

    buffer = []
    last_post = time.time()
    while True:
        t = time.time()
        rtt = ping_once(cfg["target"], to)
        sample = {"ts": round(t, 3), "rtt": rtt}
        if cfg["_gw_ip"]:
            sample["gw"] = ping_once(cfg["_gw_ip"], to)
        buffer.append(sample)

        if time.time() - last_post >= post_every and buffer:
            try:
                post(cfg, buffer)
                buffer = []
                last_post = time.time()
            except Exception as e:
                print(f"[netmon-agent] post failed, buffering {len(buffer)}: {e}",
                      flush=True)
                if len(buffer) > cfg["max_buffer"]:
                    buffer = buffer[-cfg["max_buffer"]:]
                last_post = time.time()

        elapsed = time.time() - t
        remaining = interval - elapsed
        if remaining > 0:
            time.sleep(remaining)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        raise
    except Exception as e:  # keep a crash visible on a kiosk
        sys.stderr.write(f"[netmon-agent] fatal: {e}\n")
        with open(os.path.join(HERE, "netmon_agent_crash.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.ctime()}  {e}\n")
        raise
