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
import ssl
import subprocess
import threading
import time
import urllib.request

# NOTE: only import stdlib modules the BOOTSTRAPPER already bundles into the .exe
# (see kiosk-agent/netmon_agent.py import list). The payload is exec'd inside the
# frozen runtime, so an import it needs that the exe didn't bundle crashes the
# agent. `threading` is bundled; `concurrent.futures` is NOT — hence the manual
# thread pool below instead of ThreadPoolExecutor.
PAYLOAD_VERSION = "2026.08.06.1"

SYSTEM = platform.system()
_TIME_RE = re.compile(r"time[=<]\s*([\d.,]+)\s*ms", re.IGNORECASE)
_MS_RE = re.compile(r"([\d.,]+)\s*ms", re.IGNORECASE)


# ── TLS trust: pinned Let's Encrypt roots ─────────────────────────────────--
# Old / locked-down kiosks often lack ISRG Root X1/X2 in the Windows cert
# store (no Windows Update in years, no admin rights to add them). We verify
# TLS against the OS store PLUS these two pinned public roots — extracted
# from the certifi bundle, sha256 96BCEC06… (X1) / 69729B8E… (X2) — so the
# agent works everywhere with no OS changes. Verification stays ON.
ISRG_ROOTS = """-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICGzCCAaGgAwIBAgIQQdKd0XLq7qeAwSxs6S+HUjAKBggqhkjOPQQDAzBPMQsw
CQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJuZXQgU2VjdXJpdHkgUmVzZWFyY2gg
R3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBYMjAeFw0yMDA5MDQwMDAwMDBaFw00
MDA5MTcxNjAwMDBaME8xCzAJBgNVBAYTAlVTMSkwJwYDVQQKEyBJbnRlcm5ldCBT
ZWN1cml0eSBSZXNlYXJjaCBHcm91cDEVMBMGA1UEAxMMSVNSRyBSb290IFgyMHYw
EAYHKoZIzj0CAQYFK4EEACIDYgAEzZvVn4CDCuwJSvMWSj5cz3es3mcFDR0HttwW
+1qLFNvicWDEukWVEYmO6gbf9yoWHKS5xcUy4APgHoIYOIvXRdgKam7mAHf7AlF9
ItgKbppbd9/w+kHsOdx1ymgHDB/qo0IwQDAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0T
AQH/BAUwAwEB/zAdBgNVHQ4EFgQUfEKWrt5LSDv6kviejM9ti6lyN5UwCgYIKoZI
zj0EAwMDaAAwZQIwe3lORlCEwkSHRhtFcP9Ymd70/aTSVaYgLXTWNLxBo1BfASdW
tL4ndQavEi51mI38AjEAi/V3bNTIZargCyzuFJ0nN6T5U6VR5CmD1/iQMVtCnwr1
/q4AaOeMSQ+2b1tbFfLn
-----END CERTIFICATE-----"""


def _ssl_context():
    try:
        ctx = ssl.create_default_context()
    except Exception:
        return None
    try:
        ctx.load_verify_locations(cadata=ISRG_ROOTS)
    except Exception:
        pass
    return ctx


SSL_CTX = _ssl_context()


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
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
        resp.read()


def _fetch_targets(ctx):
    """GET the site's device list this agent should ping on the LAN."""
    req = urllib.request.Request(
        ctx["server_url"].rstrip("/") + "/agents/targets",
        headers={"X-Agent-Token": ctx["token"]})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _post_device_report(ctx, results):
    data = json.dumps({"results": results}).encode("utf-8")
    req = urllib.request.Request(
        ctx["server_url"].rstrip("/") + "/agents/device-report",
        data=data, method="POST",
        headers={"Content-Type": "application/json", "X-Agent-Token": ctx["token"]})
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as resp:
        resp.read()


def _probe_sweep(ctx, timeout_s):
    """Fetch this site's devices, ping each on the LAN in parallel, report the
    reachability back. Returns the server-suggested sweep interval (or None)."""
    try:
        info = _fetch_targets(ctx)
    except Exception as e:
        print(f"[netmon-payload] targets fetch failed: {e}", flush=True)
        return None
    targets = info.get("targets") or []
    if not targets:
        return info.get("interval")

    results = []
    lock = threading.Lock()

    def _probe(t):
        rtt = ping_once(t.get("ip"), timeout_s)
        with lock:
            results.append({"id": t["id"], "reachable": rtt is not None, "rtt_ms": rtt})

    # Manual bounded thread pool (stdlib threading — bundled in the exe; unlike
    # concurrent.futures). Ping up to `batch` devices at once.
    batch = 24
    for i in range(0, len(targets), batch):
        chunk = targets[i:i + batch]
        threads = [threading.Thread(target=_probe, args=(t,)) for t in chunk]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout_s + 2.0)
    try:
        _post_device_report(ctx, results)
        up = sum(1 for r in results if r["reachable"])
        print(f"[netmon-payload] LAN sweep: {up}/{len(results)} reachable "
              f"(site {info.get('site_name') or '—'})", flush=True)
    except Exception as e:
        print(f"[netmon-payload] device-report failed: {e}", flush=True)
    return info.get("interval")


def _server_version(ctx):
    try:
        req = urllib.request.Request(
            ctx["server_url"].rstrip("/") + "/agents/version",
            headers={"X-Agent-Token": ctx["token"]})
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
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
    probe_lan = bool(cfg.get("probe_lan", True))
    probe_every = max(30.0, float(cfg.get("probe_interval", 120.0)))

    print(f"[netmon-payload v{PAYLOAD_VERSION}] {hostname} target={cfg['target']} "
          f"gateway={gw_ip or 'none'} lan_probe={'on' if probe_lan else 'off'} "
          f"-> {ctx['server_url']}", flush=True)

    buffer = []
    last_post = time.time()
    last_ver_check = time.time()
    # First LAN sweep ~15s after start so devices populate quickly.
    last_probe = time.time() - probe_every + 15.0
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

        # Periodically sweep the local LAN: ping every device the site has in
        # UniFi and report which actually answer (the "unreachable" signal).
        if probe_lan and time.time() - last_probe >= probe_every:
            last_probe = time.time()
            suggested = _probe_sweep(ctx, to)
            if suggested and float(suggested) >= 30:
                probe_every = float(suggested)

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
