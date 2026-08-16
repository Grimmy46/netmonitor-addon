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
import hashlib
import json
import os
import platform
import re
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

# NOTE: only import stdlib modules the BOOTSTRAPPER already bundles into the .exe
# (see kiosk-agent/netmon_agent.py import list). The payload is exec'd inside the
# frozen runtime, so an import it needs that the exe didn't bundle crashes the
# agent. `threading` is bundled; `concurrent.futures` is NOT — hence the manual
# thread pool below instead of ThreadPoolExecutor.
PAYLOAD_VERSION = "2026.08.16.2"

SYSTEM = platform.system()
_CTX = None  # set in main(); carries bootstrap_version + worker_exe for reporting
_PRINTER_STATUS = None  # latest ticket-printer reading, attached to each report
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


def _clean_env():
    """A child environment WITHOUT PyInstaller's one-file bootloader variables
    (_MEIPASS2 / _PYI_*). When our one-file exe launches a copy of itself — as a
    self-update relaunch or a device worker — inheriting these points the child's
    bootloader at OUR unpacked temp dir instead of extracting its own, which
    corrupts the child (worker exits rc=1; a relaunch can fail outright). Strip
    them so every spawned exe unpacks fresh."""
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("_MEI") or k.startswith("_PYI"))}


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
    """Report samples; RETURNS the server's response dict (which may carry
    pending remote commands for this agent)."""
    payload = json.dumps({
        "target": cfg["target"],
        "gateway": gw_ip,
        "hostname": hostname,
        "os": os_str,
        "agent_version": PAYLOAD_VERSION,
        "bootstrap_version": _CTX.get("bootstrap_version") if _CTX else None,
        "printer": _PRINTER_STATUS,
        "samples": samples,
    }).encode("utf-8")
    req = urllib.request.Request(
        ctx["server_url"].rstrip("/") + "/agents/report",
        data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Agent-Token": ctx["token"]})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
        try:
            return json.loads(resp.read().decode("utf-8", "ignore"))
        except Exception:
            return {}


# ── Remote commands (Phase 3): closed allow-list, never a shell ──────────────
# The server queues a command; it arrives in the report response; we execute
# the ONE matching handler and post the result back. Unknown kinds are refused
# loudly (reported as errors) — there is no generic execution path.

def _cmd_printer_status(_args):
    """Read-only: what does Windows say about every installed printer?
    This is the KPM180H reconnaissance step — DetectedErrorState is where a
    bidirectional driver reports paper-out / jam / door-open."""
    if SYSTEM != "Windows":
        return {"printers": [], "note": f"not windows ({SYSTEM})"}
    ps = (
        "Get-CimInstance Win32_Printer | Select-Object Name,DriverName,PortName,"
        "Default,PrinterStatus,DetectedErrorState,ExtendedDetectedErrorState,"
        "PrinterState,WorkOffline,Local | ConvertTo-Json -Depth 3"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        **_no_window_kwargs())
    out = proc.stdout.decode("utf-8", "ignore").strip()
    if proc.returncode != 0 or not out:
        return {"printers": [], "error": proc.stderr.decode("utf-8", "ignore")[:500]}
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]  # single printer → PS emits an object, not a list
    return {"printers": data}


def _cmd_printer_probe(_args):
    """Read-only reconnaissance: how is the KPM180H addressable on THIS kiosk?
    Enumerates installed printers (name/driver/port), active serial COM ports
    (registry SERIALCOMM — the reliable source), and any Custom/POS/printer PnP
    devices. This tells us which rung of the plan we're on: a COM port means a
    clean bidirectional status channel; USB-only means the harder path."""
    if SYSTEM != "Windows":
        return {"note": f"not windows ({SYSTEM})"}
    ps = r'''
$out = [ordered]@{}
try { $out.printers = Get-CimInstance Win32_Printer |
    Select-Object Name,DriverName,PortName,Default,Local } catch { $out.printers=@() }
try {
  $sc = Get-ItemProperty 'HKLM:\HARDWARE\DEVICEMAP\SERIALCOMM' -ErrorAction Stop
  $out.serial_ports = $sc.PSObject.Properties |
    Where-Object { $_.Name -notlike 'PS*' } |
    ForEach-Object { [pscustomobject]@{ device=$_.Name; port=$_.Value } }
} catch { $out.serial_ports=@() }
try { $out.serial_named = Get-CimInstance Win32_SerialPort |
    Select-Object DeviceID,Name,Description } catch { $out.serial_named=@() }
try { $out.pnp = Get-PnpDevice -PresentOnly |
    Where-Object { $_.FriendlyName -match 'Custom|KPM|POS|Printer|Receipt|Ticket' } |
    Select-Object Class,FriendlyName,InstanceId,Status } catch { $out.pnp=@() }
$out | ConvertTo-Json -Depth 4
'''
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40, **_no_window_kwargs())
    out = proc.stdout.decode("utf-8", "ignore").strip()
    result = {}
    if out:
        try:
            result = json.loads(out)
        except Exception:
            result = {"raw": out[:4000]}
    # USBPRINT device-interface paths (the real-time bidirectional channel for a
    # USB-attached KPM180H — this is what USB001-port printers need).
    try:
        result["usb_paths"] = _usbprint_paths()
    except Exception as e:
        result["usb_paths_error"] = str(e)[:300]
    return result


def _usbprint_paths():
    """Enumerate USBPRINT device-interface paths (\\\\?\\usb#…#{guid}) via SetupAPI.
    These can be opened with CreateFile for real-time bidirectional status on a
    USB printer-class device — no driver change, no admin."""
    import ctypes
    from ctypes import wintypes

    setup = ctypes.windll.setupapi

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class SP_DID(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                    ("Flags", wintypes.DWORD), ("Reserved", ctypes.POINTER(ctypes.c_ulong))]

    # Declare arg/return types so the 64-bit HDEVINFO handle is passed full-width
    # (ctypes defaults an untyped handle arg to 32-bit int → truncation → the
    # enumeration silently fails and finds nothing).
    setup.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setup.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                           wintypes.HWND, wintypes.DWORD]
    setup.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
        ctypes.POINTER(SP_DID)]
    setup.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(SP_DID), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    setup.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setup.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

    # GUID_DEVINTERFACE_USBPRINT
    g = GUID(0x28D78FAD, 0x5A12, 0x11D1,
             (ctypes.c_ubyte * 8)(0xAE, 0x5B, 0x00, 0x00, 0xF8, 0x03, 0xA8, 0xC2))
    DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x2, 0x10
    hdev = setup.SetupDiGetClassDevsW(ctypes.byref(g), None, None,
                                      DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev or hdev == wintypes.HANDLE(-1).value:
        return []

    paths, i = [], 0
    try:
        while True:
            ifd = SP_DID()
            ifd.cbSize = ctypes.sizeof(SP_DID)
            if not setup.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(g), i, ctypes.byref(ifd)):
                break
            i += 1
            req = wintypes.DWORD(0)
            setup.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), None, 0, ctypes.byref(req), None)
            if not req.value:
                continue
            buf = ctypes.create_string_buffer(req.value)
            cb = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6  # documented cbSize quirk
            ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(cb)), 4)
            if setup.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), buf, req.value, None, None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        setup.SetupDiDestroyDeviceInfoList(hdev)
    return paths


def _serial_txn(target, data, read_to, read_max, mode):
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    raw = k32.CreateFileW("\\\\.\\" + target, 0xC0000000, 0, None, 3, 0, None)
    if not raw or raw == wintypes.HANDLE(-1).value:
        return {"error": f"could not open {target} (err {ctypes.get_last_error()})"}
    # Wrap as a HANDLE instance so every subsequent (untyped) call passes it
    # full-width instead of ctypes truncating it to a 32-bit int.
    h = wintypes.HANDLE(raw)
    try:
        if mode:
            class DCB(ctypes.Structure):
                _fields_ = [("DCBlength", wintypes.DWORD), ("BaudRate", wintypes.DWORD),
                            ("fFlags", wintypes.DWORD), ("wReserved", wintypes.WORD),
                            ("XonLim", wintypes.WORD), ("XoffLim", wintypes.WORD),
                            ("ByteSize", ctypes.c_byte), ("Parity", ctypes.c_byte),
                            ("StopBits", ctypes.c_byte), ("XonChar", ctypes.c_char),
                            ("XoffChar", ctypes.c_char), ("ErrorChar", ctypes.c_char),
                            ("EofChar", ctypes.c_char), ("EvtChar", ctypes.c_char),
                            ("wReserved1", wintypes.WORD)]
            dcb = DCB(); dcb.DCBlength = ctypes.sizeof(DCB)
            if k32.BuildCommDCBW(str(mode), ctypes.byref(dcb)):
                k32.SetCommState(h, ctypes.byref(dcb))

        class COMMTIMEOUTS(ctypes.Structure):
            _fields_ = [("ReadIntervalTimeout", wintypes.DWORD),
                        ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                        ("ReadTotalTimeoutConstant", wintypes.DWORD),
                        ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                        ("WriteTotalTimeoutConstant", wintypes.DWORD)]
        k32.SetCommTimeouts(h, ctypes.byref(COMMTIMEOUTS(50, 0, read_to, 0, 1000)))
        k32.PurgeComm(h, 0x000F)
        written = wintypes.DWORD(0)
        k32.WriteFile(h, data, len(data), ctypes.byref(written), None)
        time.sleep(0.05)
        buf = ctypes.create_string_buffer(read_max)
        nread = wintypes.DWORD(0)
        k32.ReadFile(h, buf, read_max, ctypes.byref(nread), None)
        return {"target": target, "transport": "serial", "wrote": int(written.value),
                "read_hex": buf.raw[: nread.value].hex(), "read_len": int(nread.value)}
    finally:
        k32.CloseHandle(h)


def _trace(step):
    """Append a breadcrumb to the worker's trace file. If the NEXT native call
    hard-crashes the worker process (a ctypes access violation Python can't
    catch), the parent reads this file and reports exactly how far we got.
    No-op outside the worker (NETMON_DEVIO unset) or on any error."""
    p = os.environ.get("NETMON_DEVIO")
    if not p:
        return
    try:
        with open(p + ".trace", "a", encoding="utf-8") as f:
            f.write(str(step) + "\n")
            f.flush()
    except Exception:
        pass


def _usb_txn(path, data, read_to, read_max):
    """Overlapped read/write to a USBPRINT device-interface path, with a real
    read timeout (usbprint has no COM-style timeouts, so we use OVERLAPPED I/O
    + WaitForSingleObject + CancelIo).

    Every native call is fully argtype/restype-declared: an untyped ctypes call
    marshals a 64-bit handle/pointer as a 32-bit int, which either fails silently
    or dereferences garbage (an access violation that kills the process). Typed
    calls pass every pointer full-width. Each risky step drops a breadcrumb so a
    hard crash is still localizable from the parent."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]
    LPOVERLAPPED = ctypes.POINTER(OVERLAPPED)

    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                wintypes.HANDLE]
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                 wintypes.LPCWSTR]
    k32.WriteFile.restype = wintypes.BOOL
    k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), LPOVERLAPPED]
    k32.ReadFile.restype = wintypes.BOOL
    k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), LPOVERLAPPED]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetOverlappedResult.restype = wintypes.BOOL
    k32.GetOverlappedResult.argtypes = [wintypes.HANDLE, LPOVERLAPPED,
                                        ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    k32.CancelIo.argtypes = [wintypes.HANDLE]
    k32.CancelIo.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

    _trace(f"usb: CreateFileW {path[-40:]}")
    INVALID = wintypes.HANDLE(-1).value
    raw = k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0x40000000, None)  # OVERLAPPED
    if not raw or raw == INVALID:
        return {"error": f"could not open USB device (err {ctypes.get_last_error()})"}
    h = wintypes.HANDLE(raw)  # full-width for every subsequent call
    _trace("usb: opened")

    def io(func, name, buf, length, timeout=read_to):
        ov = OVERLAPPED()
        ev_raw = k32.CreateEventW(None, True, False, None)
        ov.hEvent = ev_raw  # stored full-width in the HANDLE struct field
        ev = wintypes.HANDLE(ev_raw)
        n = wintypes.DWORD(0)
        try:
            _trace(f"usb: {name} start len={length}")
            ok = func(h, buf, length, ctypes.byref(n), ctypes.byref(ov))
            if not ok:
                err = ctypes.get_last_error()
                if err != 997:  # not ERROR_IO_PENDING
                    _trace(f"usb: {name} failed err={err}")
                    return 0
                _trace(f"usb: {name} pending")
                if k32.WaitForSingleObject(ev, timeout) != 0:  # timeout / abandoned
                    k32.CancelIo(h)
                    _trace(f"usb: {name} timeout")
                    return 0
                k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(n), False)
            _trace(f"usb: {name} done n={int(n.value)}")
            return int(n.value)
        finally:
            k32.CloseHandle(ev)

    try:
        # Drain any stale bytes the endpoint still holds from a PRIOR transaction
        # (e.g. a lifetime-cut-count string), so each read returns THIS command's
        # reply, not a lagged/leftover one. Short per-read timeout; stop when empty.
        _trace("usb: drain")
        dbuf = ctypes.create_string_buffer(256)
        for _ in range(8):
            if io(k32.ReadFile, "drain", dbuf, 256, 60) <= 0:
                break
        wrote = io(k32.WriteFile, "write", data, len(data))
        time.sleep(0.05)
        rbuf = ctypes.create_string_buffer(read_max)
        rn = io(k32.ReadFile, "read", rbuf, read_max)
        _trace("usb: complete")
        return {"target": path[-48:], "transport": "usb", "wrote": wrote,
                "read_hex": rbuf.raw[:rn].hex(), "read_len": rn}
    finally:
        k32.CloseHandle(h)


def _spool_txn(printer_name, data, read_max):
    """Write RAW to a printer via the spooler and ReadPrinter the bidi reply.
    Cooperates with the POS (both go through the spooler) — lowest-risk channel,
    though the spooler may buffer, so real-time status can lag vs the USB path."""
    import ctypes
    from ctypes import wintypes
    ws = ctypes.WinDLL("winspool.drv")
    ws.OpenPrinterW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p]
    h = wintypes.HANDLE()
    if not ws.OpenPrinterW(printer_name, ctypes.byref(h), None):
        return {"error": f"OpenPrinter('{printer_name}') failed ({ctypes.get_last_error()})"}

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR),
                    ("pDatatype", wintypes.LPWSTR)]
    try:
        di = DOC_INFO_1("NetMonitor status", None, "RAW")
        ws.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
        ws.StartDocPrinterW(h, 1, ctypes.byref(di))
        ws.StartPagePrinter(h)
        written = wintypes.DWORD(0)
        ws.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        ws.WritePrinter(h, data, len(data), ctypes.byref(written))
        ws.EndPagePrinter(h)
        ws.EndDocPrinter(h)
        time.sleep(0.1)
        rbuf = ctypes.create_string_buffer(read_max)
        rn = wintypes.DWORD(0)
        ws.ReadPrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        ok = ws.ReadPrinter(h, rbuf, read_max, ctypes.byref(rn))
        reply = rbuf.raw[: rn.value] if ok else b""
        return {"target": f"SPOOL:{printer_name}", "transport": "spool",
                "wrote": int(written.value), "read_hex": reply.hex(), "read_len": len(reply)}
    finally:
        ws.ClosePrinter(h)


def _cmd_printer_raw(args):
    """Bidirectional transaction with the printer — the bytes AND the transport
    both come from the server, so all protocol experimentation happens from the
    dashboard with NO redeploys, NO driver, NO admin.

    args: {"target": ..., "write_hex": "10 04 01", "read_timeout_ms": 600,
           "read_max": 64, "mode": "baud=115200 …"(serial only)}
    target forms:
      "COM3"                → serial port
      "SPOOL:<printer name>"→ via the Windows spooler (ReadPrinter/WritePrinter)
      "usb"                 → the first enumerated USBPRINT interface
      "\\\\?\\usb#…"        → a specific USBPRINT interface path
    """
    if SYSTEM != "Windows":
        return {"note": f"not windows ({SYSTEM})"}
    target = str(args.get("target") or "").strip()
    write_hex = re.sub(r"[^0-9a-fA-F]", "", str(args.get("write_hex") or ""))
    if len(write_hex) % 2 != 0:
        return {"error": "write_hex must be whole bytes"}
    data = bytes.fromhex(write_hex)
    read_max = min(int(args.get("read_max") or 64), 1024)
    read_to = min(int(args.get("read_timeout_ms") or 600), 5000)

    if re.fullmatch(r"(?i)COM\d+", target):
        return _serial_txn(target, data, read_to, read_max, args.get("mode"))
    if target.upper().startswith("SPOOL:"):
        return _spool_txn(target[6:], data, read_max)
    if target.lower() == "usb":
        paths = _usbprint_paths()
        if not paths:
            return {"error": "no USBPRINT interface found"}
        return _usb_txn(paths[0], data, read_to, read_max)
    if target.startswith("\\\\?\\") or target.lower().startswith("\\\\?\\usb"):
        return _usb_txn(target, data, read_to, read_max)
    return {"error": f"unrecognized target {target!r} (use COMx, SPOOL:<name>, or usb)"}


def _cmd_printer_monitor(args):
    """Auto-find the USB ticket printer and read its real-time status (DLE EOT 2,
    the 'offline cause' byte, calibrated on the KPM180H: 0x12 healthy, 0x72 paper
    out). Returns a normalized {present, state, raw, detail} for the agent's
    background poller. Runs in the crash-isolated worker (native USB I/O)."""
    if SYSTEM != "Windows":
        return {"present": False, "note": f"not windows ({SYSTEM})"}
    try:
        paths = _usbprint_paths()
    except Exception as e:  # noqa: BLE001 — a bad enumerate must not raise
        return {"present": False, "error": f"enumerate failed: {e}"}
    if not paths:
        return {"present": False}
    # Prefer the Custom S.p.A. KPM180H (VID 0DD4); else the only USB printer iface.
    path = next((p for p in paths if re.search(r"(?i)vid_0dd4|kpm|custom", p)), paths[0])
    res = _usb_txn(path, b"\x10\x04\x02", 600, 8)  # DLE EOT 2
    raw = (res or {}).get("read_hex") or ""
    if not raw:
        return {"present": True, "state": "unknown", "raw": "",
                "detail": (res or {}).get("error") or "no reply"}
    b = bytes.fromhex(raw)[0]
    if b & 0x20:            # bit5 — roll-paper end (paper out)
        state, detail = "paper_out", "out of paper"
    elif b & 0x04:         # bit2 — cover / paper-door open
        state, detail = "cover_open", "cover / paper door open"
    elif b & 0x40:         # bit6 — printer error
        state, detail = "error", "printer error"
    else:
        state, detail = "ok", "paper present, cover closed"
    return {"present": True, "state": state, "raw": f"{b:02x}", "detail": detail}


def _build_test_ticket(label, cut="full"):
    """ESC/POS test ticket for the KPM180H: a title, the station + local time, and
    a feed + cut so it dispenses like a real ticket."""
    ESC, GS = b"\x1b", b"\x1d"
    parts = [
        ESC + b"@",            # initialize
        ESC + b"a\x01",        # center
        ESC + b"!\x30",        # double width + height
        b"NetMonitor\n",
        ESC + b"!\x00",        # back to normal
        b"TEST PRINT\n",
        b"------------------------\n",
    ]
    if label:
        parts.append(("Station: %s\n" % label).encode("ascii", "replace"))
    parts.append(("Time: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode("ascii", "replace"))
    parts.append(b"If you can read this, the\n")
    parts.append(b"ticket printer is working.\n")
    parts.append(ESC + b"d\x04")  # feed 4 lines so text clears the cutter
    if cut == "full":
        parts.append(GS + b"V\x00")   # full cut
    elif cut == "partial":
        parts.append(GS + b"V\x01")   # partial cut
    # cut == "none": leave the paper attached
    return b"".join(parts)


def _cmd_printer_test(args):
    """Send a small ESC/POS test ticket to the KPM180H and confirm the printer
    accepted the whole job AND is healthy right afterwards (DLE EOT status).
    Runs in the crash-isolated worker (native USB I/O). args: {label, cut}."""
    if SYSTEM != "Windows":
        return {"ok": False, "note": f"not windows ({SYSTEM})"}
    try:
        paths = _usbprint_paths()
    except Exception as e:  # noqa: BLE001 — a bad enumerate must not raise
        return {"ok": False, "error": f"enumerate failed: {e}"}
    if not paths:
        return {"ok": False, "error": "no USB printer found"}
    # Prefer the Custom S.p.A. KPM180H (VID 0DD4); else the only USB printer iface.
    path = next((p for p in paths if re.search(r"(?i)vid_0dd4|kpm|custom", p)), paths[0])
    cut = str(args.get("cut") or "full").lower()
    data = _build_test_ticket(args.get("label"), cut)

    res = _usb_txn(path, data, 800, 8) or {}
    if res.get("error"):
        return {"ok": False, "error": res["error"]}
    wrote = int(res.get("wrote") or 0)
    if wrote < len(data):
        return {"ok": False, "printed_bytes": wrote, "expected_bytes": len(data),
                "error": "printer did not accept the full job"}

    # Confirm the printer is healthy right after printing.
    time.sleep(0.3)
    st = _usb_txn(path, b"\x10\x04\x02", 600, 8) or {}   # DLE EOT 2
    raw = st.get("read_hex") or ""
    state, detail = "ok", "printed; printer online"
    if raw:
        b = bytes.fromhex(raw)[0]
        if b & 0x20:
            state, detail = "paper_out", "ticket sent, but paper is now out"
        elif b & 0x04:
            state, detail = "cover_open", "ticket sent, but the cover / paper door is open"
        elif b & 0x40:
            state, detail = "error", "ticket sent, but the printer reports an error"
        else:
            state, detail = "ok", "ticket printed — paper present, cover closed"
    else:
        detail = "ticket sent; printer gave no status reply"
    return {"ok": state == "ok", "printed_bytes": wrote, "state": state,
            "raw": raw, "detail": detail, "cut": cut}


_COMMAND_HANDLERS = {
    "printer-status": _cmd_printer_status,
    "printer-probe": _cmd_printer_probe,
    "printer-raw": _cmd_printer_raw,
    "printer-monitor": _cmd_printer_monitor,
    "printer-test": _cmd_printer_test,
}

# Kinds whose native device I/O could crash the process — run them in a SEPARATE
# worker process (the exe re-launched in NETMON_DEVIO mode) so a crash can never
# take the agent down. Requires bootstrap ≥ 2.5 (the server gates delivery).
_ISOLATED_KINDS = {"printer-probe", "printer-raw", "printer-monitor", "printer-test"}


def device_exec(kind, args):
    """Called INSIDE the crash-isolated worker process (see the bootstrapper's
    _device_worker). Runs one command handler and returns its result dict."""
    handler = _COMMAND_HANDLERS.get(kind)
    if handler is None:
        return {"error": f"unknown kind '{kind}'"}
    return handler(args or {})


def _spawn_device_worker(kind, args, timeout=60):
    """Run a device command in a throwaway subprocess (the agent exe re-launched
    as a worker). Returns the result dict; a crash/timeout becomes an error dict
    instead of killing this process."""
    exe = (_CTX or {}).get("worker_exe") or sys.executable
    fd, req = tempfile.mkstemp(suffix=".nmdevio.json")
    os.close(fd)
    out = req + ".out"
    try:
        with open(req, "w", encoding="utf-8") as f:
            json.dump({"kind": kind, "args": args}, f)
        env = _clean_env()  # strip PyInstaller vars so the worker exe unpacks fresh
        env["NETMON_DEVIO"] = req
        try:
            proc = subprocess.Popen([exe], env=env, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, **_no_window_kwargs())
        except Exception as e:
            return {"error": f"could not launch device worker: {e}"}
        def _last_step():
            try:
                with open(req + ".trace", encoding="utf-8") as f:
                    steps = [ln.strip() for ln in f if ln.strip()]
                return steps[-1] if steps else ""
            except Exception:
                return ""
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            step = _last_step()
            tail = f" — last step: {step}" if step else ""
            return {"error": f"device worker timed out ({timeout}s){tail}"}
        if os.path.exists(out):
            try:
                with open(out, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                return {"error": f"unreadable worker result: {e}"}
        step = _last_step()
        tail = f" — crashed after: {step}" if step else " (likely crashed)"
        return {"error": f"device worker exited rc={rc} with no result{tail}"}
    finally:
        for f in (req, out, req + ".trace"):
            try:
                os.remove(f)
            except OSError:
                pass


def _post_command_result(ctx, cmd_id, ok, result):
    data = json.dumps({"id": cmd_id, "ok": ok, "result": result}).encode("utf-8")
    req = urllib.request.Request(
        ctx["server_url"].rstrip("/") + "/agents/command-result",
        data=data, method="POST",
        headers={"Content-Type": "application/json", "X-Agent-Token": ctx["token"]})
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as resp:
        resp.read()


def _run_commands(ctx, commands):
    """Execute delivered commands, each in its own thread (a slow printer query
    must never stall the ping loop). Crash-isolated kinds run in a separate
    process; everything is reported, never raised."""
    for cmd in commands or []:
        def _one(c=cmd):
            cid = str(c.get("id") or "")
            kind = c.get("kind") or ""
            args = c.get("args") or {}
            try:
                if kind in _ISOLATED_KINDS:
                    result = _spawn_device_worker(kind, args)
                    ok = "error" not in result
                else:
                    handler = _COMMAND_HANDLERS.get(kind)
                    if handler is None:
                        _post_command_result(ctx, cid, False, {"error": f"unknown kind '{kind}'"})
                        return
                    result, ok = handler(args), True
                _post_command_result(ctx, cid, ok, result)
                print(f"[netmon-payload] command {kind} {'ok' if ok else 'error'}", flush=True)
            except Exception as e:
                try:
                    _post_command_result(ctx, cid, False, {"error": str(e)[:500]})
                except Exception:
                    pass
                print(f"[netmon-payload] command {kind} failed: {e}", flush=True)
        threading.Thread(target=_one, daemon=True).start()


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


# ── Live landing-page probe (designated kiosk only) ─────────────────────────
# Every agent asks /agents/live-config every ~60s. Only the ONE kiosk the
# dashboard designates gets enabled=true + a target list; for everyone else
# this whole feature is a single tiny GET per minute and nothing more.

def _fetch_live_config(ctx):
    req = urllib.request.Request(
        ctx["server_url"].rstrip("/") + "/agents/live-config",
        headers={"X-Agent-Token": ctx["token"]})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _post_probe_report(ctx, samples):
    data = json.dumps({"samples": samples}).encode("utf-8")
    req = urllib.request.Request(
        ctx["server_url"].rstrip("/") + "/agents/probe-report",
        data=data, method="POST",
        headers={"Content-Type": "application/json", "X-Agent-Token": ctx["token"]})
    with urllib.request.urlopen(req, timeout=20, context=SSL_CTX) as resp:
        resp.read()


def _http_time_once(url, timeout_s):
    """Time an HTTPS GET. ANY http response (even 4xx/5xx) counts as reachable —
    we're measuring 'is the service there and how fast', not correctness."""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NetMonitorAgent"})
        with urllib.request.urlopen(req, timeout=timeout_s, context=SSL_CTX) as resp:
            resp.read(1024)
        return round((time.time() - t0) * 1000.0, 2)
    except urllib.error.HTTPError:
        return round((time.time() - t0) * 1000.0, 2)  # server answered — it's up
    except Exception:
        return None


def _live_probe_worker(ctx, stop):
    """Runs as a daemon thread. Pings ping-targets every ~2s, times http
    targets every ~10s, posts batches every ~10s — only while this kiosk is
    the designated Live probe. `stop` is set when the payload hands over to a
    newer version, so old workers never linger."""
    info = None
    buffer = []
    last_cfg = 0.0
    last_http = 0.0
    last_post = time.time()
    gw = {"ip": "", "at": 0.0}
    while not stop.is_set():
        now = time.time()
        if info is None or now - last_cfg >= 60.0:
            last_cfg = now
            try:
                info = _fetch_live_config(ctx)
            except Exception:
                info = info or {"enabled": False}
        if not info.get("enabled"):
            stop.wait(30.0)
            continue

        targets = info.get("targets") or []
        ping_iv = max(1.0, float(info.get("ping_interval", 2.0)))
        http_iv = max(5.0, float(info.get("http_interval", 10.0)))
        post_iv = max(5.0, float(info.get("post_interval", 10.0)))

        t0 = time.time()
        do_http = t0 - last_http >= http_iv
        if do_http:
            last_http = t0

        lock = threading.Lock()

        def _probe(t):
            kind = t.get("kind") or "ping"
            tgt = (t.get("target") or "").strip()
            if kind == "http":
                ms = _http_time_once(tgt, 8.0)
            else:
                host = tgt
                if host.lower() == "gateway":
                    if not gw["ip"] or time.time() - gw["at"] > 300.0:
                        gw["ip"], gw["at"] = detect_gateway(), time.time()
                    host = gw["ip"]
                ms = ping_once(host, 2.0) if host else None
            with lock:
                buffer.append({"target_id": t.get("id"), "ts": round(time.time(), 3), "ms": ms})

        threads = []
        for t in targets:
            if (t.get("kind") or "ping") == "http" and not do_http:
                continue
            th = threading.Thread(target=_probe, args=(t,), daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join(12.0)

        if time.time() - last_post >= post_iv and buffer:
            try:
                _post_probe_report(ctx, buffer[:2000])
                buffer = []
            except Exception as e:
                print(f"[netmon-payload] probe-report failed, buffering {len(buffer)}: {e}", flush=True)
                buffer = buffer[-4000:]
            last_post = time.time()

        elapsed = time.time() - t0
        stop.wait(max(0.2, ping_iv - elapsed))


def _server_version(ctx):
    try:
        req = urllib.request.Request(
            ctx["server_url"].rstrip("/") + "/agents/version",
            headers={"X-Agent-Token": ctx["token"]})
        with urllib.request.urlopen(req, timeout=10, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore")).get("version")
    except Exception:
        return None


# ── resilience: heartbeat, watchdog, and hash-verified exe self-update ───────
_HB_NAME = ".netmon_hb"
_PROBATION_NAME = ".netmon_probation"
_WATCHDOG_VBS = "netmon_watchdog.vbs"

# WSH watchdog: relaunches the agent if its heartbeat goes stale, and rolls a
# bad exe swap back to the backup. VBScript takes backslashes literally, so the
# install dir is substituted as-is (no escaping). Payload-delivered → whole fleet.
_WATCHDOG_TEMPLATE = r'''Option Explicit
' NetMonAgent watchdog (installed by the agent payload). Keeps the agent alive
' and rolls back a bad exe self-update. Runs hidden via wscript //B.
Dim fso, sh, d, exe, hb, prob, bak, wdhb, alive, ts
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
d = "{{DIR}}\"
exe = d & "{{EXE}}"
hb = d & ".netmon_hb"
prob = d & ".netmon_probation"
bak = exe & ".bak"
wdhb = d & ".netmon_wd_hb"
' Single instance: if another watchdog is already heartbeating, exit quietly.
If fso.FileExists(wdhb) Then
  If DateDiff("s", fso.GetFile(wdhb).DateLastModified, Now) < 90 Then WScript.Quit
End If
Do
  ' Stamp our own heartbeat so the agent doesn't spawn a duplicate watchdog.
  On Error Resume Next
  Set ts = fso.CreateTextFile(wdhb, True) : ts.WriteLine Now : ts.Close
  On Error Goto 0
  alive = False
  If fso.FileExists(hb) Then
    If DateDiff("s", fso.GetFile(hb).DateLastModified, Now) < 120 Then alive = True
  End If
  If Not alive Then
    ' A swap on probation whose new exe never heartbeat (>180s): restore backup.
    If fso.FileExists(prob) And fso.FileExists(bak) Then
      If DateDiff("s", fso.GetFile(prob).DateLastModified, Now) > 180 Then
        On Error Resume Next
        fso.MoveFile exe, exe & ".bad"
        fso.MoveFile bak, exe
        fso.DeleteFile prob, True
        On Error Goto 0
      End If
    End If
    If fso.FileExists(exe) Then sh.Run "cmd /c start """" """ & exe & """", 0, False
  End If
  WScript.Sleep 30000
Loop
'''


def _install_dir():
    try:
        return os.path.dirname(os.path.abspath(sys.executable))
    except Exception:
        return None


def _frozen():
    return SYSTEM == "Windows" and getattr(sys, "frozen", False)


def _touch_heartbeat():
    """Bump the heartbeat file the watchdog polls (proof the agent is alive)."""
    d = _install_dir()
    if not d:
        return
    try:
        with open(os.path.join(d, _HB_NAME), "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


def _commit_exe_update():
    """We're running and posting → any exe swap took. Clear the probation marker
    so the watchdog won't roll back."""
    d = _install_dir()
    if not d:
        return
    try:
        os.remove(os.path.join(d, _PROBATION_NAME))
    except OSError:
        pass


def _install_watchdog():
    """Write the WSH watchdog next to the agent and register it at login (HKCU
    Run). Idempotent, no admin. Delivered by the payload so every station gets
    it — updated exe or not."""
    if not _frozen():
        return
    d = _install_dir()
    if not d:
        return
    vbs_path = os.path.join(d, _WATCHDOG_VBS)
    vbs = _WATCHDOG_TEMPLATE.replace("{{DIR}}", d).replace(
        "{{EXE}}", os.path.basename(sys.executable))
    try:
        old = ""
        if os.path.exists(vbs_path):
            with open(vbs_path, "r", encoding="utf-8", errors="ignore") as f:
                old = f.read()
        if old != vbs:
            with open(vbs_path, "w", encoding="utf-8") as f:
                f.write(vbs)
    except Exception as e:
        print(f"[watchdog] write failed: {e}", flush=True)
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "NetMonAgentWatchdog", 0, winreg.REG_SZ,
                          f'wscript.exe //B //Nologo "{vbs_path}"')
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[watchdog] autostart registration failed: {e}", flush=True)
    # Start it NOW (not just at next login) so a crash/bad-swap self-heals mid-day.
    # Guard: only launch if no watchdog is currently heartbeating (the .vbs also
    # self-dedups on start), so payload updates don't stack up watchdogs.
    wd_hb = os.path.join(d, ".netmon_wd_hb")
    try:
        live = os.path.exists(wd_hb) and (time.time() - os.path.getmtime(wd_hb)) < 90
    except OSError:
        live = False
    if not live:
        try:
            subprocess.Popen(["wscript.exe", "//B", "//Nologo", vbs_path],
                             env=_clean_env(), **_no_window_kwargs())
            print("[watchdog] started in-session", flush=True)
        except Exception as e:
            print(f"[watchdog] launch failed: {e}", flush=True)


def _self_update(ctx):
    """If this station is opted into the rollout, download the target exe, verify
    its sha256, back up the current exe, swap, relaunch the new one, and return
    True (the caller then hard-exits this old process). ANY failure aborts the
    swap and returns False — it must never brick. The watchdog + probation marker
    recover a swap that doesn't come up."""
    if not _frozen():
        return False
    my_ver = (ctx or {}).get("bootstrap_version")
    try:
        req = urllib.request.Request(ctx["server_url"].rstrip("/") + "/agents/agent-update",
                                     headers={"X-Agent-Token": ctx["token"]})
        with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as resp:
            info = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        return False
    if not info or not info.get("update"):
        return False
    want_sha = (info.get("sha256") or "").lower()
    ver = str(info.get("version") or "")
    if not want_sha or not ver:
        return False
    if my_ver and str(my_ver) == ver:
        return False  # already on the target — never re-swap (prevents a loop)
    try:
        req = urllib.request.Request(ctx["server_url"].rstrip("/") + "/agents/agent-exe/download",
                                     headers={"X-Agent-Token": ctx["token"]})
        with urllib.request.urlopen(req, timeout=180, context=SSL_CTX) as resp:
            data = resp.read()
    except Exception as e:
        print(f"[self-update] download failed: {e}", flush=True)
        return False
    if hashlib.sha256(data).hexdigest() != want_sha:
        print("[self-update] sha256 mismatch — aborting", flush=True)
        return False
    exe = os.path.abspath(sys.executable)
    new, bak = exe + ".new", exe + ".bak"
    prob = os.path.join(_install_dir() or ".", _PROBATION_NAME)
    try:
        with open(new, "wb") as f:
            f.write(data)
        if os.path.exists(bak):
            try: os.remove(bak)
            except OSError: pass
        os.replace(exe, bak)   # rename the running exe out of the way (OK on Windows)
        os.replace(new, exe)   # drop the verified new exe into place
        with open(prob, "w") as f:
            f.write(ver)        # probation: watchdog restores .bak if new never heartbeats
        # Clean env so the NEW exe unpacks its own one-file bundle instead of
        # inheriting our _MEIPASS2 (which corrupts the relaunched agent).
        subprocess.Popen([exe], env=_clean_env(), **_no_window_kwargs())
        print(f"[self-update] swapped exe -> {ver}; relaunching", flush=True)
        return True
    except Exception as e:
        print(f"[self-update] swap failed ({e}); restoring", flush=True)
        try:
            if not os.path.exists(exe) and os.path.exists(bak):
                os.replace(bak, exe)
        except Exception:
            pass
        try:
            if os.path.exists(new):
                os.remove(new)
        except OSError:
            pass
        return False


def _supports_worker(ctx):
    """True only if the running .exe (bootstrapper) can execute a crash-isolated
    device worker (bootstrap ≥ 2.5). On an older exe, re-launching it with
    NETMON_DEVIO would spawn a SECOND full agent, so the poller must stay off."""
    bv = (ctx or {}).get("bootstrap_version")
    if not bv:
        return False
    try:
        parts = [int(x) for x in str(bv).split(".")[:2]]
    except (ValueError, AttributeError):
        return False
    return parts >= [2, 5]


def _printer_monitor_worker(ctx, stop):
    """Poll the ticket printer's status in the crash-isolated worker and stash the
    latest reading for the next report. Polls often when a printer is present,
    backs off to ~10 min when none is found. Windows-only; requires worker mode."""
    global _PRINTER_STATUS
    if SYSTEM != "Windows" or not _supports_worker(ctx):
        return
    if stop.wait(20):  # let enrollment + first ping settle
        return
    while not stop.is_set():
        try:
            res = _spawn_device_worker("printer-monitor", {}, timeout=30)
        except Exception as e:  # noqa: BLE001 — a poll must never kill the loop
            res = {"present": False, "error": str(e)}
        if isinstance(res, dict) and "present" in res:
            _PRINTER_STATUS = res
        present = isinstance(res, dict) and res.get("present")
        if stop.wait(60.0 if present else 600.0):
            return


def main(cfg, ctx):
    global _CTX
    _CTX = ctx  # carries bootstrap_version + worker_exe for reporting/spawning
    _touch_heartbeat()   # earliest possible, so the watchdog never double-launches us
    _install_watchdog()  # resilience first — a bad self-update swap can still recover
    if _self_update(ctx):  # opted-in kiosks swap their exe here, then hard-exit
        os._exit(0)        # new exe is already launched; don't let the bootstrapper loop
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

    # Live landing-page probe: daemon thread, active ONLY if the server says
    # this kiosk is the designated probe. Stopped explicitly on payload
    # handover so an old worker never outlives its version.
    live_stop = threading.Event()
    threading.Thread(target=_live_probe_worker, args=(ctx, live_stop), daemon=True).start()

    # Ticket-printer status poller: crash-isolated USB read, reported each cycle.
    # Gated on worker-capable exes so it can never spawn a rogue second agent.
    printer_stop = threading.Event()
    threading.Thread(target=_printer_monitor_worker, args=(ctx, printer_stop), daemon=True).start()

    buffer = []
    last_post = time.time()
    last_ver_check = time.time()
    committed = False  # clear the exe-swap probation marker after our first post
    # First LAN sweep ~15s after start so devices populate quickly.
    last_probe = time.time() - probe_every + 15.0
    while True:
        t = time.time()
        _touch_heartbeat()  # every loop (~1s): the watchdog's proof we're alive
        rtt = ping_once(cfg["target"], to)
        sample = {"ts": round(t, 3), "rtt": rtt}
        if gw_ip:
            sample["gw"] = ping_once(gw_ip, to)
        buffer.append(sample)

        if time.time() - last_post >= post_every and buffer:
            try:
                resp = _post(ctx, cfg, gw_ip, hostname, os_str, buffer)
                buffer = []
                if not committed:
                    _commit_exe_update(); committed = True  # a good post = swap succeeded
                _run_commands(ctx, (resp or {}).get("commands"))
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
        # to the bootstrapper to fetch + run it. Also honour an exe rollout that
        # was flipped on mid-run (swap + relaunch without waiting for a restart).
        if time.time() - last_ver_check >= ver_check_every:
            last_ver_check = time.time()
            if _self_update(ctx):
                live_stop.set(); printer_stop.set()
                os._exit(0)  # new exe launched; hard-exit so we don't run twice
            sv = _server_version(ctx)
            if sv and sv != ctx.get("running_version"):
                print(f"[netmon-payload] newer version {sv} available — updating", flush=True)
                live_stop.set()
                printer_stop.set()
                return

        remaining = interval - (time.time() - t)
        if remaining > 0:
            time.sleep(remaining)
