"""NetMonitor agent PAYLOAD — the auto-updating part of the agent.

This file is served by the cloud to every agent (GET /agents/payload) and RUN on
the kiosk by the bootstrapper. To ship a new agent version (Phase 2 telemetry,
Phase 3 control, or any fix), edit THIS file, bump PAYLOAD_VERSION, and redeploy —
every agent picks it up on its next check-in. No kiosk re-install.

Rules for this file:
  * Pure Python standard library only (it runs inside the bundled runtime).
  * Must define PAYLOAD_VERSION and main(cfg, ctx).
  * main() runs the work loop and RETURNS when the server advertises a newer
    version (so the bootstrapper can fetch + run the new payload).

`cfg`  = the kiosk's config file (server_url, token, target, gateway, intervals…).
`ctx`  = {"server_url", "token", "running_version"} supplied by the bootstrapper.
"""
import json
import os
import platform
import re
import socket
import subprocess
import time
import urllib.request

PAYLOAD_VERSION = "2026.08.04.1"

SYSTEM = platform.system()
_TIME_RE = re.compile(r"time[=<]\s*([\d.,]+)\s*ms", re.IGNORECASE)
_MS_RE = re.compile(r"([\d.,]+)\s*ms", re.IGNORECASE)


def _no_window_kwargs():
    if SYSTEM == "Windows":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": si, "creationflags": 0x08000000}
    return {}


def detect_gateway():
    try:
        if SYSTEM == "Windows":
            out = subprocess.run(["ipconfig"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=6, **_no_window_kwargs()).stdout.decode("utf-8", "ignore")
            for line in out.splitlines():
                if "Default Gateway" in line:
                    m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                    if m and not m.group(1).startswith("0."):
                        return m.group(1)
        elif SYSTEM == "Darwin":
            out = subprocess.run(["route", "-n", "get", "default"], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=6).stdout.decode("utf-8", "ignore")
            m = re.search(r"gateway:\s*([\d.]+)", out)
            return m.group(1) if m else ""
        else:
            out = subprocess.run(["ip", "route"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=6).stdout.decode("utf-8", "ignore")
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


def _post(ctx, cfg, gw_ip, hostname, os_str, samples):
    payload = json.dumps({
        "target": cfg["target"],
        "gateway": gw_ip,
        "hostname": hostname,
        "os": os_str,
        "agent_version": PAYLOAD_VERSION,
        "samples": samples,
    }).encode("utf-8")
    req = urllib.request.Request(
        ctx["server_url"].rstrip("/") + "/agents/report",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Agent-Token": ctx["token"]})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def _server_version(ctx):
    try:
        req = urllib.request.Request(
            ctx["server_url"].rstrip("/") + "/agents/version",
            headers={"X-Agent-Token": ctx["token"]})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore")).get("version")
    except Exception:
        return None


def main(cfg, ctx):
    hostname = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown").strip()
    os_str = f"{platform.system()} {platform.release()}".strip()
    gw = cfg.get("gateway", "auto")
    gw_ip = detect_gateway() if gw == "auto" else gw
    interval = max(0.2, float(cfg.get("interval", 1.0)))
    post_every = max(5.0, float(cfg.get("post_interval", 30.0)))
    to = min(4.0, max(1.0, float(cfg.get("timeout", 2.0))))
    max_buffer = int(cfg.get("max_buffer", 5000))
    ver_check_every = max(60.0, float(cfg.get("version_check_interval", 600.0)))

    print(f"[netmon-payload v{PAYLOAD_VERSION}] {hostname} target={cfg['target']} "
          f"gateway={gw_ip or 'none'} -> {ctx['server_url']}", flush=True)

    buffer = []
    last_post = time.time()
    last_ver_check = time.time()
    while True:
        t = time.time()
        rtt = ping_once(cfg["target"], to)
        sample = {"ts": round(t, 3), "rtt": rtt}
        if gw_ip:
            sample["gw"] = ping_once(gw_ip, to)
        buffer.append(sample)

        if time.time() - last_post >= post_every and buffer:
            try:
                _post(ctx, cfg, gw_ip, hostname, os_str, buffer)
                buffer = []
            except Exception as e:
                print(f"[netmon-payload] post failed, buffering {len(buffer)}: {e}", flush=True)
                if len(buffer) > max_buffer:
                    buffer = buffer[-max_buffer:]
            last_post = time.time()

        # Periodically ask the server if a newer payload exists; if so, hand back
        # to the bootstrapper to fetch + run it.
        if time.time() - last_ver_check >= ver_check_every:
            last_ver_check = time.time()
            sv = _server_version(ctx)
            if sv and sv != ctx.get("running_version"):
                print(f"[netmon-payload] newer version {sv} available — updating", flush=True)
                return

        remaining = interval - (time.time() - t)
        if remaining > 0:
            time.sleep(remaining)
