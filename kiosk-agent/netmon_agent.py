#!/usr/bin/env python3
"""NetMonitor agent BOOTSTRAPPER (stable — this is what gets built into the .exe).

Keep this file small and rarely-changed: it's the part that's hard to update once
an .exe is on a kiosk. All the real work lives in the *payload*, which this
bootstrapper downloads from the server, caches, and runs — re-fetching whenever a
newer version is published. That means future agent changes (Phase 2 telemetry,
Phase 3 remote control, fixes) ship by editing the payload on the server and
redeploying once; kiosks self-update on their next check-in with no re-install.

Robustness:
  * Offline-safe: if the server is unreachable it runs the cached / seed payload.
  * Won't adopt a broken update: fetched payloads are compile-checked before use,
    and a bad one is discarded (the last good payload keeps running).
  * Crash-safe: any error just logs, waits, and retries.

Files in the install folder:  this .exe  +  netmon_agent.config.json  +  agent_payload.py (seed/cache)
Uses ONLY the Python standard library.
"""
import json
import os
import re
import sys
import time
import urllib.request

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)          # folder the .exe lives in
else:
    HERE = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(HERE, "netmon_agent.config.json")
PAYLOAD_PATH = os.path.join(HERE, "agent_payload.py")
_VER_RE = re.compile(r"""PAYLOAD_VERSION\s*=\s*["']([^"']+)["']""")
BOOTSTRAP_VERSION = "1.0"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("server_url") or not cfg.get("token"):
        raise SystemExit("Config needs 'server_url' and 'token' "
                         "(Settings → Agents in the dashboard).")
    if not cfg.get("target"):
        raise SystemExit("Config needs a 'target' to ping (e.g. rcs.funcardapp.com).")
    cfg["server_url"] = cfg["server_url"].rstrip("/")
    return cfg


def _get(cfg, path, timeout=10):
    req = urllib.request.Request(cfg["server_url"] + path,
                                 headers={"X-Agent-Token": cfg["token"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def server_version(cfg):
    try:
        return json.loads(_get(cfg, "/agents/version")).get("version")
    except Exception:
        return None


def fetch_payload(cfg):
    try:
        return _get(cfg, "/agents/payload", timeout=20)
    except Exception as e:
        print(f"[bootstrap] payload fetch failed: {e}", flush=True)
        return None


def local_version():
    try:
        with open(PAYLOAD_PATH, encoding="utf-8") as f:
            m = _VER_RE.search(f.read())
            return m.group(1) if m else None
    except OSError:
        return None


def _write_payload(source):
    """Compile-check first, then write atomically. Returns True on success."""
    try:
        compile(source, "agent_payload.py", "exec")
    except SyntaxError as e:
        print(f"[bootstrap] rejecting bad payload update: {e}", flush=True)
        return False
    tmp = PAYLOAD_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(source)
    os.replace(tmp, PAYLOAD_PATH)  # atomic on Win + POSIX
    return True


def maybe_update(cfg):
    sv = server_version(cfg)
    if sv and sv != local_version():
        print(f"[bootstrap] updating payload -> {sv}", flush=True)
        src = fetch_payload(cfg)
        if src:
            _write_payload(src)  # keeps old copy if this fails


def run():
    print(f"[bootstrap v{BOOTSTRAP_VERSION}] starting", flush=True)
    cfg = load_config()
    while True:
        try:
            maybe_update(cfg)
            if not os.path.exists(PAYLOAD_PATH):
                print("[bootstrap] no payload yet (server unreachable on first run) — retrying",
                      flush=True)
                time.sleep(15)
                continue
            with open(PAYLOAD_PATH, encoding="utf-8") as f:
                source = f.read()
            ns = {}
            exec(compile(source, "agent_payload.py", "exec"), ns)  # noqa: S102 — trusted server payload
            if "main" not in ns:
                print("[bootstrap] payload has no main(); retrying", flush=True)
                time.sleep(15)
                continue
            ctx = {"server_url": cfg["server_url"], "token": cfg["token"],
                   "running_version": ns.get("PAYLOAD_VERSION")}
            ns["main"](cfg, ctx)  # returns when a newer version is available
        except SystemExit:
            raise
        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"[bootstrap] error: {e} — retrying in 30s", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
