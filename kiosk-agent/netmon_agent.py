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
import concurrent.futures  # noqa: F401  (bundle for future payloads; not used here)
import ctypes  # noqa: F401
import glob  # noqa: F401
import hashlib  # noqa: F401
import platform  # noqa: F401
import shutil  # noqa: F401
import ssl
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
LOG_PATH = os.path.join(HERE, "netmon_agent.log")
_VER_RE = re.compile(r"""PAYLOAD_VERSION\s*=\s*["']([^"']+)["']""")
BOOTSTRAP_VERSION = "2.3"
ADD_NEW_LABEL = "➕ Add a new station…"


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


def _redirect_logs():
    """Windowless build has no console, so send prints to a rolling log file
    next to the exe (kept small) — that's where you troubleshoot a kiosk."""
    if not getattr(sys, "frozen", False):
        return
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        f = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass


def _hide_console():
    """The build ships as a --console exe; hide its console window immediately so
    it's invisible to customers on the kiosk. (A --windowed build avoids even the
    brief flash, but publishing that workflow change needs a GitHub token with
    'workflow' scope — this achieves the same end without it.)"""
    if not getattr(sys, "frozen", False) or platform.system() != "Windows":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def ensure_autostart(cfg):
    """Register the agent to launch at login (HKCU\\…\\Run) so it comes back on
    every reboot — no manual Startup shortcut. Per-user, no admin needed."""
    if not getattr(sys, "frozen", False) or platform.system() != "Windows":
        return
    if not cfg.get("autostart", True) or winreg is None:
        return
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, "NetMonAgent", 0, winreg.REG_SZ, f'"{sys.executable}"')
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[bootstrap] autostart registration skipped: {e}", flush=True)


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
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return resp.read().decode("utf-8", "ignore")


def http_post(cfg, path, body, timeout=20):
    req = urllib.request.Request(cfg["server_url"] + path,
                                 data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
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
    _hide_console()
    _redirect_logs()
    print(f"[bootstrap v{BOOTSTRAP_VERSION}] starting", flush=True)
    cfg = load_config()
    ensure_autostart(cfg)
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
