#!/usr/bin/env python3
"""NetMonitor agent BOOTSTRAPPER (stable — built into the .exe).

Two jobs, both deliberately simple so this file rarely changes:
  1. First run with no token → ENROLL: ask for the dashboard PIN, show a dropdown
     of stations (pick one, or add a new one), claim it, and save the token. Every
     kiosk ships the SAME three files; the station is chosen here.
  2. Every run → keep the agent up to date: download the latest payload from the
     server, cache it, run it, and re-fetch when a newer version is published.

Robust: offline-safe (runs the cached payload), won't adopt a broken update
(compile-checked), crash-safe (logs + retries). Standard library only.

Install folder:  NetMonAgent.exe  +  netmon_agent.config.json  +  agent_payload.py
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# PyInstaller only bundles modules it can SEE at build time. The payload is
# fetched + exec'd at runtime, so we import its (and the enrollment GUI's) modules
# here so they're baked into the .exe. Add new stdlib modules a future payload
# needs to this list — the only reason to ever rebuild the exe.
import base64  # noqa: F401
import ctypes  # noqa: F401
import glob  # noqa: F401
import hashlib  # noqa: F401
import platform  # noqa: F401
import shutil  # noqa: F401
import socket
import struct  # noqa: F401
import subprocess  # noqa: F401
import threading  # noqa: F401
try:  # Windows-only extras for later phases (telemetry / control)
    import ctypes.wintypes  # noqa: F401
    import winreg
except Exception:
    winreg = None
try:
    import tkinter as tk
    from tkinter import ttk
except Exception:
    tk = None

if getattr(sys, "frozen", False):
    HERE = os.path.dirname(sys.executable)          # folder the .exe lives in
else:
    HERE = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(HERE, "netmon_agent.config.json")
PAYLOAD_PATH = os.path.join(HERE, "agent_payload.py")
_VER_RE = re.compile(r"""PAYLOAD_VERSION\s*=\s*["']([^"']+)["']""")
BOOTSTRAP_VERSION = "2.0"
ADD_NEW_LABEL = "➕ Add a new station…"


# ── config ───────────────────────────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("server_url"):
        raise SystemExit("Config needs 'server_url'.")
    if not cfg.get("target"):
        raise SystemExit("Config needs a 'target' to ping (e.g. rcs.funcardapp.com).")
    cfg["server_url"] = cfg["server_url"].rstrip("/")
    return cfg


def save_config(cfg):
    keep = {k: v for k, v in cfg.items() if not k.startswith("_")}
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def machine_id():
    """A stable per-machine id so a station locks to the PC that claimed it."""
    if winreg is not None:
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            val, _ = winreg.QueryValueEx(k, "MachineGuid")
            winreg.CloseKey(k)
            if val:
                return str(val)
        except Exception:
            pass
    return socket.gethostname() or "unknown"


def hostname():
    return (os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown").strip()


# ── http ───────────────────────────────────────────────────────────────────--
def http_get(cfg, path, timeout=10):
    req = urllib.request.Request(cfg["server_url"] + path,
                                 headers={"X-Agent-Token": cfg["token"]})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def http_post(cfg, path, body, timeout=20):
    req = urllib.request.Request(cfg["server_url"] + path,
                                 data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


# ── enrollment (first run only) ───────────────────────────────────────────────
def enroll(cfg):
    """Return a token by claiming a station. GUI if possible, else console."""
    if tk is not None:
        try:
            tok = _enroll_gui(cfg)
            if tok:
                return tok
        except Exception as e:
            print(f"[enroll] GUI unavailable ({e}); using console prompts", flush=True)
    return _enroll_console(cfg)


def _claim_selection(cfg, pin, stations, label, new_name):
    ctx = {"hostname": hostname(), "machine_id": machine_id()}
    if label == ADD_NEW_LABEL:
        return http_post(cfg, "/agents/enroll/add",
                         {"pin": pin, "name": new_name, **ctx})
    station = next(s for s in stations if _station_label(s) == label)
    return http_post(cfg, "/agents/enroll/claim",
                     {"pin": pin, "station_id": station["id"], **ctx})


def _station_label(s):
    return s["name"] + (" (in use)" if s.get("claimed") else "")


def _enroll_gui(cfg):
    root = tk.Tk()
    root.title("NetMonitor — set up this kiosk")
    root.geometry("440x300")
    result = {"token": None}
    stations = []

    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Set up this kiosk", font=("", 14, "bold")).pack(anchor="w")
    ttk.Label(frm, text=f"{hostname()} → {cfg['server_url']}",
              foreground="#888").pack(anchor="w", pady=(0, 10))

    ttk.Label(frm, text="Enrollment PIN (from the dashboard):").pack(anchor="w")
    pin_var = tk.StringVar()
    pin_row = ttk.Frame(frm)
    pin_row.pack(fill="x", pady=(2, 6))
    pin_entry = ttk.Entry(pin_row, textvariable=pin_var, width=12)
    pin_entry.pack(side="left")
    pin_entry.focus()
    status = ttk.Label(frm, text="", foreground="#c0392b")
    status.pack(anchor="w")

    picker = ttk.Frame(frm)
    ttk.Label(picker, text="Station:").pack(anchor="w")
    station_var = tk.StringVar()
    combo = ttk.Combobox(picker, textvariable=station_var, state="readonly", width=44)
    combo.pack(anchor="w", pady=(2, 6))
    name_var = tk.StringVar()
    name_entry = ttk.Entry(picker, textvariable=name_var, width=46)

    def on_combo(*_):
        if station_var.get() == ADD_NEW_LABEL:
            ttk.Label(picker, text="New station name:").pack(anchor="w")
            name_entry.pack(anchor="w", pady=(2, 6))
        else:
            name_entry.pack_forget()
    combo.bind("<<ComboboxSelected>>", on_combo)

    def do_connect():
        status.config(text="Connecting…", foreground="#888")
        root.update_idletasks()
        try:
            data = http_post(cfg, "/agents/enroll/stations", {"pin": pin_var.get().strip()})
        except urllib.error.HTTPError as e:
            status.config(text="Wrong PIN." if e.code == 401 else f"Error {e.code}.",
                          foreground="#c0392b")
            return
        except Exception as e:
            status.config(text=f"Can't reach server: {e}", foreground="#c0392b")
            return
        stations.clear()
        stations.extend(data)
        combo["values"] = [_station_label(s) for s in stations] + [ADD_NEW_LABEL]
        combo.current(0)
        status.config(text="")
        picker.pack(fill="x", pady=(4, 0))
        save_btn.config(state="normal")

    def do_save():
        try:
            r = _claim_selection(cfg, pin_var.get().strip(), stations,
                                 station_var.get(), name_var.get().strip())
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore") if hasattr(e, "read") else str(e)
            status.config(text=f"{e.code}: {msg[:120]}", foreground="#c0392b")
            return
        except Exception as e:
            status.config(text=str(e), foreground="#c0392b")
            return
        result["token"] = r["token"]
        root.destroy()

    ttk.Button(pin_row, text="Connect", command=do_connect).pack(side="left", padx=8)
    save_btn = ttk.Button(frm, text="Save & start", command=do_save, state="disabled")
    save_btn.pack(anchor="e", pady=(12, 0))
    pin_entry.bind("<Return>", lambda _e: do_connect())

    root.mainloop()
    return result["token"]


def _enroll_console(cfg):
    print("\n=== NetMonitor kiosk setup ===", flush=True)
    for _ in range(5):
        pin = input("Enrollment PIN (from dashboard): ").strip()
        try:
            stations = http_post(cfg, "/agents/enroll/stations", {"pin": pin})
            break
        except urllib.error.HTTPError as e:
            print("Wrong PIN." if e.code == 401 else f"Error {e.code}.")
        except Exception as e:
            print(f"Can't reach server: {e}")
    else:
        raise SystemExit("Enrollment cancelled.")
    for i, s in enumerate(stations, 1):
        print(f"  {i}. {_station_label(s)}")
    print(f"  {len(stations) + 1}. {ADD_NEW_LABEL}")
    choice = input("Pick a station number: ").strip()
    idx = int(choice) - 1 if choice.isdigit() else -1
    if idx == len(stations):
        name = input("New station name: ").strip()
        r = _claim_selection(cfg, pin, stations, ADD_NEW_LABEL, name)
    elif 0 <= idx < len(stations):
        r = _claim_selection(cfg, pin, stations, _station_label(stations[idx]), "")
    else:
        raise SystemExit("Invalid selection.")
    return r["token"]


# ── self-update ────────────────────────────────────────────────────────────--
def server_version(cfg):
    try:
        return json.loads(http_get(cfg, "/agents/version")).get("version")
    except Exception:
        return None


def fetch_payload(cfg):
    try:
        return http_get(cfg, "/agents/payload", timeout=20)
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
    try:
        compile(source, "agent_payload.py", "exec")
    except SyntaxError as e:
        print(f"[bootstrap] rejecting bad payload update: {e}", flush=True)
        return False
    tmp = PAYLOAD_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(source)
    os.replace(tmp, PAYLOAD_PATH)
    return True


def maybe_update(cfg):
    sv = server_version(cfg)
    if sv and sv != local_version():
        print(f"[bootstrap] updating payload -> {sv}", flush=True)
        src = fetch_payload(cfg)
        if src:
            _write_payload(src)


def run():
    print(f"[bootstrap v{BOOTSTRAP_VERSION}] starting", flush=True)
    cfg = load_config()
    if not cfg.get("token"):
        print("[bootstrap] no token — starting enrollment", flush=True)
        token = enroll(cfg)
        if not token:
            raise SystemExit("Enrollment not completed.")
        cfg["token"] = token
        save_config(cfg)
        print("[bootstrap] enrolled and saved token", flush=True)

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
