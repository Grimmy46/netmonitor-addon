#!/usr/bin/env python3
"""
NetMonitor v3
- Ping, HTTP, API, Traceroute, Bandwidth tests
- Per-target custom intervals
- Latency/loss thresholds with toast popups + Warnings tab
- Uptime % tracking (24h rolling)
- Historical trend graphs (Chart.js)
- Failures dropdown with full details on each card
- Fixed bandwidth with reliable fallback URLs + progress %
- HA sidebar embed support
"""

import subprocess, time, csv, json, os, threading, urllib.request, urllib.error
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── FILES ───────────────────────────────────────────────────────────────────
CONFIG_FILE   = "targets.json"
CSV_FILE      = "network_results.csv"
RESULTS_FILE  = "latest_results.json"
WARNINGS_FILE = "warnings.json"
DEVICES_FILE  = "devices.json"
MAP_FILE      = "map_positions.json"
MAP_IMAGE     = "map_image.png"
MAPS_FILE     = "saved_maps.json"
WEB_PORT      = 8088

BW_URLS = [
    "https://speed.cloudflare.com/__down?bytes=1000000",
    "http://proof.ovh.net/files/1Mb.dat",
    "http://speedtest.tele2.net/5MB.bin",
]

DEFAULT_CONFIG = {
    "ping": [
        {"name":"Google DNS",     "host":"8.8.8.8",    "interval":5,"warn_rtt_ms":100,"warn_loss_pct":10},
        {"name":"Cloudflare DNS", "host":"1.1.1.1",    "interval":5,"warn_rtt_ms":100,"warn_loss_pct":10},
        {"name":"Google",         "host":"google.com", "interval":5,"warn_rtt_ms":150,"warn_loss_pct":10},
    ],
    "http": [
        {"name":"Google",    "url":"https://www.google.com",    "interval":5,"warn_latency_ms":500},
        {"name":"Cloudflare","url":"https://www.cloudflare.com","interval":5,"warn_latency_ms":500},
        {"name":"GitHub",    "url":"https://api.github.com",    "interval":5,"warn_latency_ms":800},
    ],
    "api": [
        {"name":"GitHub API","url":"https://api.github.com","method":"GET",
         "headers":{"Accept":"application/vnd.github.v3+json"},"body":"",
         "expected_status":200,"expected_body":"","interval":10,"warn_latency_ms":1000},
        {"name":"JSONPlaceholder","url":"https://jsonplaceholder.typicode.com/posts/1",
         "method":"GET","headers":{},"body":"","expected_status":200,"expected_body":"userId",
         "interval":10,"warn_latency_ms":1000},
    ],
    "traceroute":[
        {"name":"Google DNS","host":"8.8.8.8","interval":30},
        {"name":"Cloudflare","host":"1.1.1.1","interval":30},
    ],
    "bandwidth_url":BW_URLS[0],
    "bandwidth_interval":60,
    "warn_speed_mbps":10,
}

# ─── UPTIME ───────────────────────────────────────────────────────────────────
_uptime = {}
_uptime_lock = threading.Lock()

def record_uptime(name, ok):
    now = datetime.now()
    cutoff = now - timedelta(hours=24)
    with _uptime_lock:
        if name not in _uptime:
            _uptime[name] = []
        _uptime[name].append((now, ok))
        _uptime[name] = [(t,v) for t,v in _uptime[name] if t > cutoff]

def get_uptime_pct(name):
    with _uptime_lock:
        data = _uptime.get(name, [])
    if not data:
        return None
    return round(sum(1 for _,v in data if v) / len(data) * 100, 1)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            for k,v in DEFAULT_CONFIG.items():
                cfg.setdefault(k,v)
            return cfg
        except Exception:
            pass
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE,"w") as f:
        json.dump(cfg,f,indent=2)
    # Purge removed targets from results history so ghost cards disappear
    _purge_stale_results(cfg)

def _purge_stale_results(cfg):
    """Remove any result keys not in the current config."""
    if not os.path.exists(RESULTS_FILE):
        return
    try:
        with open(RESULTS_FILE) as f:
            history = json.load(f)
    except Exception:
        return
    # Build set of valid names from current config
    valid = set()
    for t in cfg.get("ping",[]): valid.add(t["name"])
    for t in cfg.get("http",[]): valid.add(t["name"])
    for t in cfg.get("api",[]): valid.add(t["name"])
    for t in cfg.get("traceroute",[]): valid.add(t["name"])
    valid.add("Download Speed")
    # Remove any keys not in valid set
    stale = [k for k in history if k not in valid]
    for k in stale:
        del history[k]
    if stale:
        with open(RESULTS_FILE,"w") as f:
            json.dump(history,f)
        print(f"  Purged stale results: {stale}")

# ─── WARNINGS ─────────────────────────────────────────────────────────────────
_warnings = []
_warn_lock = threading.Lock()

def add_warning(name, wtype, message, value=None):
    entry = {"timestamp":datetime.now().isoformat(),"name":name,
             "type":wtype,"message":message,"value":value,"acknowledged":False}
    with _warn_lock:
        _warnings.insert(0, entry)
        del _warnings[200:]
    _flush_warnings()
    return entry

def _flush_warnings():
    with _warn_lock:
        data = list(_warnings)
    with open(WARNINGS_FILE,"w") as f:
        json.dump(data,f)

def load_warnings():
    global _warnings
    if os.path.exists(WARNINGS_FILE):
        try:
            with open(WARNINGS_FILE) as f:
                loaded = json.load(f)
            with _warn_lock:
                _warnings = loaded
        except Exception:
            pass

def ack_warning(idx):
    with _warn_lock:
        if 0 <= idx < len(_warnings):
            _warnings[idx]["acknowledged"] = True
    _flush_warnings()

def ack_all():
    with _warn_lock:
        for w in _warnings:
            w["acknowledged"] = True
    _flush_warnings()

# ─── TESTS ────────────────────────────────────────────────────────────────────
def ping_test(host, count=4):
    try:
        r = subprocess.run(["ping","-c",str(count),"-W","2",host],
                           capture_output=True,text=True,timeout=30)
        loss, rtt_avg = 100.0, None
        for line in r.stdout.splitlines():
            if "packet loss" in line:
                for p in line.split(","):
                    if "packet loss" in p:
                        try: loss = float(p.strip().split("%")[0])
                        except: pass
            if "rtt" in line or "round-trip" in line:
                nums = line.split("=")[-1].strip().split("/")
                if len(nums) >= 2:
                    try: rtt_avg = float(nums[1])
                    except: pass
        return {"type":"ping","target":host,
                "status":"OK" if loss<50 else "FAIL",
                "packet_loss_pct":loss,"rtt_avg_ms":rtt_avg,"error":None}
    except Exception as e:
        return {"type":"ping","target":host,"status":"ERROR",
                "packet_loss_pct":100,"rtt_avg_ms":None,"error":str(e)}

def http_test(url):
    try:
        start = time.time()
        req = urllib.request.Request(url,headers={"User-Agent":"NetTester/3.0"})
        with urllib.request.urlopen(req,timeout=10) as resp:
            code = resp.getcode()
            elapsed = (time.time()-start)*1000
        return {"type":"http","target":url,
                "status":"OK" if 200<=code<400 else "FAIL",
                "http_code":code,"latency_ms":round(elapsed,2),"error":None}
    except urllib.error.HTTPError as e:
        return {"type":"http","target":url,"status":"FAIL",
                "http_code":e.code,"latency_ms":None,"error":str(e)}
    except Exception as e:
        return {"type":"http","target":url,"status":"ERROR",
                "http_code":None,"latency_ms":None,"error":str(e)}

def api_test(cfg_entry):
    url       = cfg_entry["url"]
    method    = cfg_entry.get("method","GET").upper()
    headers   = dict(cfg_entry.get("headers") or {})
    body      = cfg_entry.get("body","") or ""
    exp_st    = cfg_entry.get("expected_status")
    exp_body  = cfg_entry.get("expected_body","") or ""
    headers.setdefault("User-Agent","NetTester/3.0")
    start = time.time()
    try:
        data = body.encode() if body else None
        req  = urllib.request.Request(url,data=data,headers=headers,method=method)
        with urllib.request.urlopen(req,timeout=15) as resp:
            code      = resp.getcode()
            resp_body = resp.read().decode(errors="replace")
            elapsed   = (time.time()-start)*1000
        bm = (exp_body in resp_body) if exp_body else True
        sm = (code==exp_st) if exp_st else (200<=code<400)
        status = "OK" if (sm and bm) else ("FAIL" if not sm else "WARN")
        return {"type":"api","target":url,"status":status,
                "http_code":code,"latency_ms":round(elapsed,2),
                "method":method,"body_match":bm,
                "resp_snippet":resp_body[:300],"error":None}
    except urllib.error.HTTPError as e:
        return {"type":"api","target":url,"status":"FAIL",
                "http_code":e.code,"latency_ms":round((time.time()-start)*1000,2),
                "method":method,"body_match":False,"resp_snippet":"","error":str(e)}
    except Exception as e:
        return {"type":"api","target":url,"status":"ERROR",
                "http_code":None,"latency_ms":None,"method":method,
                "body_match":False,"resp_snippet":"","error":str(e)}

def _is_float(s):
    try: float(s); return True
    except: return False

def traceroute_test(host, max_hops=20):
    def parse_tr(out):
        hops=[]
        for line in out.strip().splitlines()[1:]:
            parts=line.split()
            if not parts: continue
            try: hop=int(parts[0])
            except: continue
            ip = parts[1] if len(parts)>1 else "*"
            rtts=[float(p) for p in parts[2:] if _is_float(p)]
            hops.append({"hop":hop,"ip":ip,"rtt_avg_ms":round(sum(rtts)/len(rtts),2) if rtts else None})
        return hops
    def parse_tp(out):
        hops=[]
        for line in out.strip().splitlines():
            parts=line.split()
            if not parts: continue
            try: hop=int(parts[0].rstrip(":?"))
            except: continue
            ip = parts[1] if len(parts)>1 else "*"
            rtts=[float(p[:-2]) for p in parts if p.endswith("ms") and _is_float(p[:-2])]
            hops.append({"hop":hop,"ip":ip,"rtt_avg_ms":round(sum(rtts)/len(rtts),2) if rtts else None})
        return hops
    for cmd,parser in [
        (["traceroute","-m",str(max_hops),"-w","2","-n",host], parse_tr),
        (["tracepath","-n",host], parse_tp),
    ]:
        try:
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
            hops=parser(r.stdout)
            return {"type":"traceroute","target":host,
                    "status":"OK" if hops else "FAIL",
                    "hops":hops,"hop_count":len(hops),"error":None}
        except FileNotFoundError: continue
        except Exception as e:
            return {"type":"traceroute","target":host,"status":"ERROR",
                    "hops":[],"hop_count":0,"error":str(e)}
    return {"type":"traceroute","target":host,"status":"ERROR",
            "hops":[],"hop_count":0,"error":"traceroute not found"}

def bandwidth_test(url):
    try:
        req   = urllib.request.Request(url,headers={"User-Agent":"NetTester/3.0"})
        start = time.time()
        chunks, total = [], 0
        content_length = None
        with urllib.request.urlopen(req,timeout=30) as resp:
            cl = resp.headers.get("Content-Length")
            if cl:
                content_length = int(cl)
            limit = 5*1024*1024
            while True:
                chunk = resp.read(65536)
                if not chunk: break
                chunks.append(chunk)
                total += len(chunk)
                if total >= limit: break
        elapsed    = max(time.time()-start, 0.001)
        speed_mbps = round((total*8)/(elapsed*1_000_000), 2)
        progress   = round(total/content_length*100,1) if content_length else 100.0
        return {"type":"bandwidth","target":url,"status":"OK",
                "speed_mbps":speed_mbps,"bytes_downloaded":total,
                "duration_s":round(elapsed,2),"progress_pct":progress,"error":None}
    except Exception as e:
        return {"type":"bandwidth","target":url,"status":"ERROR",
                "speed_mbps":None,"bytes_downloaded":None,
                "duration_s":None,"progress_pct":0,"error":str(e)}

def bandwidth_with_fallback():
    cfg = load_config()
    primary = cfg.get("bandwidth_url", BW_URLS[0])
    urls = [primary] + [u for u in BW_URLS if u != primary]
    for url in urls:
        r = bandwidth_test(url)
        if r["status"] == "OK":
            return r
    return r

# ─── THRESHOLD CHECKS ─────────────────────────────────────────────────────────
_last_status = {}

def check_thresholds(result, cfg_entry):
    name   = result.get("name","?")
    status = result.get("status")
    new_w  = []

    prev = _last_status.get(name)
    if prev == "OK" and status in ("FAIL","ERROR"):
        new_w.append(add_warning(name,"down",
            f"{name} is DOWN — {result.get('error') or status}",status))
    elif prev in ("FAIL","ERROR") and status == "OK":
        new_w.append(add_warning(name,"up",f"{name} is back UP",status))
    _last_status[name] = status

    if status != "OK":
        return new_w

    lat = result.get("latency_ms") or result.get("rtt_avg_ms")
    warn_lat = cfg_entry.get("warn_latency_ms") or cfg_entry.get("warn_rtt_ms")
    if lat and warn_lat and lat > warn_lat:
        new_w.append(add_warning(name,"latency",
            f"{name} high latency: {lat}ms (threshold {warn_lat}ms)",lat))

    loss = result.get("packet_loss_pct")
    warn_loss = cfg_entry.get("warn_loss_pct")
    if loss is not None and warn_loss is not None and loss > warn_loss:
        new_w.append(add_warning(name,"loss",
            f"{name} packet loss: {loss}% (threshold {warn_loss}%)",loss))

    if result.get("type") == "bandwidth":
        cfg = load_config()
        warn_spd = cfg.get("warn_speed_mbps")
        spd = result.get("speed_mbps")
        if spd and warn_spd and spd < warn_spd:
            new_w.append(add_warning(name,"speed",
                f"Download speed low: {spd} Mbps (threshold {warn_spd} Mbps)",spd))
    return new_w

# ─── RUN PROGRESS ─────────────────────────────────────────────────────────────
_run_progress = {"running": False, "stage": "", "done": 0, "total": 0, "pct": 0}
_ping_progress = {"running": False, "done": 0, "total": 0, "pct": 0, "current": ""}
_prog_lock = threading.Lock()

def set_run_progress(stage, done, total):
    with _prog_lock:
        _run_progress["running"] = True
        _run_progress["stage"] = stage
        _run_progress["done"] = done
        _run_progress["total"] = total
        _run_progress["pct"] = int(done/total*100) if total else 0

def clear_run_progress():
    with _prog_lock:
        _run_progress["running"] = False
        _run_progress["pct"] = 100
        _run_progress["stage"] = "Done"

# ─── RUN ALL TESTS ────────────────────────────────────────────────────────────
def run_all_tests():
    cfg       = load_config()
    timestamp = datetime.now().isoformat()
    print(f"\n[{timestamp}] Running tests...")
    results   = []

    ping_targets = cfg.get("ping",[])
    http_targets = cfg.get("http",[])
    api_targets  = cfg.get("api",[])
    tr_targets   = cfg.get("traceroute",[])
    total_steps  = len(ping_targets)+len(http_targets)+len(api_targets)+len(tr_targets)+1
    done = 0

    def process(r, cfg_entry):
        r["timestamp"] = timestamp
        record_uptime(r["name"], r["status"]=="OK")
        r["uptime_pct"] = get_uptime_pct(r["name"])
        check_thresholds(r, cfg_entry)
        results.append(r)

    set_run_progress("Ping", done, total_steps)
    for t in ping_targets:
        r = ping_test(t["host"]); r["name"] = t["name"]
        process(r,t)
        done += 1
        set_run_progress(f"Ping: {t['name']}", done, total_steps)
        print(f"  PING {t['name']:22s}| {r['status']} loss={r['packet_loss_pct']}% rtt={r['rtt_avg_ms']}ms")

    set_run_progress("HTTP", done, total_steps)
    for t in http_targets:
        r = http_test(t["url"]); r["name"] = t["name"]
        process(r,t)
        done += 1
        set_run_progress(f"HTTP: {t['name']}", done, total_steps)
        print(f"  HTTP {t['name']:22s}| {r['status']} {r['http_code']} {r['latency_ms']}ms")

    set_run_progress("API", done, total_steps)
    for t in api_targets:
        r = api_test(t); r["name"] = t["name"]
        process(r,t)
        done += 1
        set_run_progress(f"API: {t['name']}", done, total_steps)
        print(f"  API  {t['name']:22s}| {r['status']} {r['http_code']} {r['latency_ms']}ms")

    set_run_progress("Traceroute", done, total_steps)
    for t in tr_targets:
        r = traceroute_test(t["host"]); r["name"] = t["name"]
        process(r,t)
        done += 1
        set_run_progress(f"Traceroute: {t['name']}", done, total_steps)
        print(f"  TR   {t['name']:22s}| {r['status']} {r['hop_count']} hops")

    set_run_progress("Bandwidth", done, total_steps)
    r = bandwidth_with_fallback(); r["name"] = "Download Speed"
    process(r,{"warn_speed_mbps": cfg.get("warn_speed_mbps",10)})
    print(f"  BW   Download Speed          | {r['status']} {r['speed_mbps']} Mbps ({r['progress_pct']}%)")

    save_csv(results)
    save_json(results)
    clear_run_progress()
    print(f"  -> Done. {len(results)} results.")
    return results

# ─── STORAGE ──────────────────────────────────────────────────────────────────
def save_csv(results):
    exists = os.path.exists(CSV_FILE)
    fields = ["timestamp","name","type","target","status",
              "packet_loss_pct","rtt_avg_ms","http_code","latency_ms",
              "method","body_match","hop_count","speed_mbps","bytes_downloaded",
              "duration_s","progress_pct","uptime_pct","error"]
    with open(CSV_FILE,"a",newline="") as f:
        w = csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        if not exists: w.writeheader()
        w.writerows(results)

def save_json(results):
    history={}
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE) as f: history=json.load(f)
        except: history={}
    for r in results:
        k=r["name"]
        history.setdefault(k,[]).append(r)
        history[k]=history[k][-200:]
    with open(RESULTS_FILE,"w") as f:
        json.dump(history,f)

# ─── DEVICES ──────────────────────────────────────────────────────────────────
def load_devices():
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE) as f: return json.load(f)
        except: pass
    return {"switches":[],"aps":[]}

def save_devices(data):
    with open(DEVICES_FILE,"w") as f:
        json.dump(data,f,indent=2)

def ping_device(ip):
    try:
        r = subprocess.run(["ping","-c","1","-W","1",ip],capture_output=True,timeout=3)
        return r.returncode == 0
    except: return False

# ─── MAP ──────────────────────────────────────────────────────────────────────
def load_map_positions():
    if os.path.exists(MAP_FILE):
        try:
            with open(MAP_FILE) as f:
                data = json.load(f)
                # Support both old format {key:{x,y}} and new {positions:{}, connections:[]}
                if "positions" in data:
                    return data
                else:
                    return {"positions": data, "connections": []}
        except: pass
    return {"positions": {}, "connections": []}

def save_map_positions(data):
    with open(MAP_FILE,"w") as f:
        json.dump(data, f, indent=2)

def load_saved_maps():
    if os.path.exists(MAPS_FILE):
        try:
            with open(MAPS_FILE) as f: return json.load(f)
        except: pass
    return []

def save_saved_maps(maps):
    with open(MAPS_FILE,"w") as f:
        json.dump(maps, f, indent=2)

    devs = load_devices()
    all_devs = []
    for section in ["switches","aps"]:
        for d in devs.get(section,[]):
            if d.get("ip","").strip():
                all_devs.append((section, d))
    total = len(all_devs)
    with _prog_lock:
        _ping_progress["running"] = True
        _ping_progress["done"] = 0
        _ping_progress["total"] = total
        _ping_progress["pct"] = 0
        _ping_progress["current"] = ""
    for i, (section, d) in enumerate(all_devs):
        with _prog_lock:
            _ping_progress["done"] = i
            _ping_progress["current"] = d.get("name","")
            _ping_progress["pct"] = int(i/total*100) if total else 0
        d["online"] = ping_device(d["ip"])
        d["last_checked"] = datetime.now().isoformat()
    save_devices(devs)
    with _prog_lock:
        _ping_progress["running"] = False
        _ping_progress["done"] = total
        _ping_progress["pct"] = 100
        _ping_progress["current"] = "Done"

# ─── STAGGERED MAP PING SCHEDULER ─────────────────────────────────────────────
# Reads map_positions.json, spreads pings evenly across 30s window
# so there is always ICMP traffic but never a burst flood
def start_map_ping_scheduler():
    """Continuously pings all mapped devices staggered across a 30s window."""
    print("  Staggered map ping scheduler running")
    while True:
        try:
            map_data  = load_map_positions()
            positions = map_data.get("positions", {})
            if not positions:
                time.sleep(15)
                continue
            devs = load_devices()
            # Build list of (key, device) for mapped devices that have IPs
            targets = []
            for key, pos in positions.items():
                section = pos.get("section","")
                idx     = pos.get("idx", -1)
                dev_list = devs.get(section, [])
                if 0 <= idx < len(dev_list):
                    d = dev_list[idx]
                    if d.get("ip","").strip():
                        targets.append((key, section, idx, d))
            if not targets:
                time.sleep(15)
                continue
            # Spread pings across 15 seconds — each device gets pinged ~4x per minute
            window   = 15.0
            interval = window / len(targets)
            for key, section, idx, d in targets:
                d["online"]       = ping_device(d["ip"])
                d["last_checked"] = datetime.now().isoformat()
                # Re-save devices each ping so frontend gets live updates
                devs = load_devices()
                dev_list = devs.get(section, [])
                if 0 <= idx < len(dev_list):
                    dev_list[idx]["online"]       = d["online"]
                    dev_list[idx]["last_checked"] = d["last_checked"]
                    save_devices(devs)
                time.sleep(max(0.5, interval))
        except Exception as e:
            print(f"  Map ping scheduler error: {e}")
            time.sleep(30)

# ─── SCHEDULER ────────────────────────────────────────────────────────────────
def start_scheduler():
    last_run = {}
    print("  Scheduler running")
    while True:
        time.sleep(30)
        now = datetime.now()
        cfg = load_config()
        needs_run = False
        for section in ["ping","http","api","traceroute"]:
            for t in cfg.get(section,[]):
                interval = t.get("interval",5)*60
                key      = f"{section}:{t.get('name','')}"
                lr       = last_run.get(key)
                if lr is None or (now-lr).total_seconds() >= interval:
                    needs_run = True; break
        bw_iv = cfg.get("bandwidth_interval",60)*60
        lr_bw = last_run.get("bw")
        if lr_bw is None or (now-lr_bw).total_seconds() >= bw_iv:
            needs_run = True
        if needs_run:
            run_all_tests()
            for section in ["ping","http","api","traceroute"]:
                for t in cfg.get(section,[]):
                    last_run[f"{section}:{t.get('name','')}"] = now
            last_run["bw"] = now

# ─── HTML ─────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NetMonitor v5.6</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@300;500;600;800&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#050a0f;--panel:#0a1520;--panel2:#0d1e30;--border:#0d2d4a;
  --accent:#00d4ff;--accent2:#00ff9d;--warn:#ffcc00;--danger:#ff2d55;
  --text:#c8e6f5;--dim:#4a7a99;--api:#b36bff;--trace:#ff9d3b;--ap:#b36bff;}
/* ── DEVICES TAB ── */
.dev-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:.6rem;}
.dev-filters{display:flex;gap:.4rem;flex-wrap:wrap;}
.df-btn{font-family:'Share Tech Mono';font-size:.65rem;padding:.25rem .7rem;border-radius:4px;
  border:1px solid var(--border);color:var(--dim);background:var(--panel2);cursor:pointer;transition:all .15s;}
.df-btn.active{border-color:var(--accent);color:var(--accent);background:rgba(0,212,255,.07);}
.dev-stats{display:flex;gap:.8rem;margin-bottom:1rem;flex-wrap:wrap;}
.dev-stat{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:.6rem 1rem;font-size:.7rem;
  cursor:pointer;transition:border-color .15s,background .15s;}
.dev-stat:hover{background:rgba(0,212,255,.06);border-color:var(--accent);}
.dev-stat.active-filter{border-color:var(--accent);background:rgba(0,212,255,.09);}
.dev-stat span{font-weight:800;font-size:1.1rem;display:block;}
.dev-stat span.online-num{color:var(--accent2);}
.dev-stat span.offline-num{color:var(--danger);}
.dev-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:.9rem 1rem;
  position:relative;transition:border-color .2s;}
.dev-card.online{border-left:3px solid var(--accent2);}
.dev-card.offline{border-left:3px solid var(--danger);}
.dev-card.unknown{border-left:3px solid var(--dim);}
.dev-card-hdr{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:.5rem;}
.dev-name{font-weight:700;font-size:.82rem;color:var(--text);line-height:1.3;}
.dev-badge{font-family:'Share Tech Mono';font-size:.58rem;padding:.18rem .5rem;border-radius:4px;white-space:nowrap;}
.dev-badge.online{background:rgba(0,255,157,.1);color:var(--accent2);border:1px solid var(--accent2);}
.dev-badge.offline{background:rgba(255,45,85,.1);color:var(--danger);border:1px solid var(--danger);}
.dev-badge.unknown{background:rgba(74,122,153,.1);color:var(--dim);border:1px solid var(--dim);}
.dev-meta{display:grid;grid-template-columns:auto 1fr;gap:.2rem .6rem;font-size:.68rem;}
.dev-meta .lbl{color:var(--dim);font-family:'Share Tech Mono';}
.dev-meta .val{color:var(--text);}
.dev-notes{font-size:.65rem;color:var(--dim);margin-top:.4rem;font-style:italic;}
.dev-actions{display:flex;gap:.4rem;margin-top:.6rem;}
.dev-act{font-size:.6rem;padding:.18rem .5rem;border-radius:4px;cursor:pointer;border:1px solid var(--border);
  background:transparent;color:var(--dim);transition:all .15s;}
.dev-act:hover{color:var(--text);border-color:var(--text);}
.dev-act.del:hover{color:var(--danger);border-color:var(--danger);}
.dev-tag{display:inline-block;font-size:.58rem;padding:.1rem .35rem;border-radius:3px;margin:.1rem .1rem 0 0;
  background:rgba(0,212,255,.06);border:1px solid rgba(0,212,255,.2);color:var(--dim);}
.dev-tag.tb{background:rgba(0,212,255,.1);color:var(--accent);border-color:var(--accent);}
.dev-tag.kiosk{background:rgba(179,107,255,.1);color:var(--ap);border-color:var(--ap);}
.dev-tag.sb{background:rgba(255,157,59,.1);color:var(--trace);border-color:var(--trace);}
.dev-tag.office{background:rgba(0,255,157,.1);color:var(--accent2);border-color:var(--accent2);}
.dev-tag.food{background:rgba(255,204,0,.1);color:var(--warn);border-color:var(--warn);}
.dev-tag.zzz{background:rgba(74,122,153,.1);color:var(--dim);border-color:var(--dim);}
.dev-tag.yyy{background:rgba(74,122,153,.1);color:var(--dim);border-color:var(--dim);}
/* ── PROGRESS BARS ── */
.progress-wrap{display:none;position:fixed;bottom:1.2rem;right:1.2rem;z-index:500;
  background:var(--panel);border:1px solid var(--border);border-radius:10px;
  padding:.8rem 1rem;min-width:260px;box-shadow:0 4px 24px rgba(0,0,0,.5);}
.progress-wrap.visible{display:block;}
.progress-title{font-size:.7rem;font-weight:700;letter-spacing:.08em;margin-bottom:.4rem;display:flex;justify-content:space-between;}
.progress-bar-bg{background:var(--panel2);border-radius:99px;height:8px;overflow:hidden;margin-bottom:.35rem;}
.progress-bar-fill{height:100%;border-radius:99px;transition:width .3s ease;background:linear-gradient(90deg,var(--accent),var(--accent2));}
.progress-stage{font-family:'Share Tech Mono';font-size:.6rem;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
/* ── ZOOM SLIDER ── */
.zoom-bar{display:flex;align-items:center;gap:.6rem;padding:.4rem .8rem;background:var(--panel2);
  border-bottom:1px solid var(--border);font-size:.65rem;color:var(--dim);overflow:visible;position:relative;z-index:100;}
.zoom-bar label{white-space:nowrap;font-family:'Share Tech Mono';}
.zoom-bar input[type=range]{flex:1;max-width:140px;accent-color:var(--accent);cursor:pointer;}
.zoom-bar span{font-family:'Share Tech Mono';font-size:.6rem;color:var(--accent);min-width:28px;}
/* ── COMPACT LEVELS ── */
body.zoom-xs .test-grid{grid-template-columns:repeat(auto-fill,minmax(min(175px,22%),1fr));}
body.zoom-xs .card{padding:.5rem .6rem;}
body.zoom-xs .card-name{font-size:.72rem;}
body.zoom-xs .meta{font-size:.6rem;}
body.zoom-xs .uptime-bar-wrap{margin:.25rem 0;}
body.zoom-xs canvas{height:55px!important;}
body.zoom-sm .test-grid{grid-template-columns:repeat(auto-fill,minmax(min(220px,28%),1fr));}
body.zoom-sm .card{padding:.6rem .7rem;}
body.zoom-sm canvas{height:65px!important;}
body.zoom-lg .test-grid{grid-template-columns:repeat(auto-fill,minmax(min(340px,32%),1fr));}
body.zoom-lg canvas{height:100px!important;}
body.zoom-xl .test-grid{grid-template-columns:repeat(auto-fill,minmax(min(420px,40%),1fr));}
body.zoom-xl canvas{height:120px!important;}
body.zoom-xs .dev-card,body.zoom-sm .dev-card{padding:.5rem .6rem;}
body.zoom-xs .dev-name{font-size:.7rem;}
body.zoom-xs .test-grid.tr-grid{grid-template-columns:repeat(auto-fill,minmax(280px,1fr));}
body.zoom-lg .test-grid.tr-grid,body.zoom-xl .test-grid.tr-grid{grid-template-columns:repeat(auto-fill,minmax(500px,1fr));}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'Exo 2',sans-serif;min-height:100vh;
  background-image:linear-gradient(rgba(0,212,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(0,212,255,.025) 1px,transparent 1px);
  background-size:40px 40px;}
header{display:flex;align-items:center;justify-content:space-between;padding:.35rem 1rem;
  border-bottom:1px solid var(--border);background:rgba(10,21,32,.97);backdrop-filter:blur(10px);
  position:sticky;top:0;z-index:300;}
.logo{display:flex;align-items:center;gap:.45rem;}
.logo-dot{width:20px;height:20px;border:2px solid var(--accent);border-radius:6px;
  display:flex;align-items:center;justify-content:center;box-shadow:0 0 8px rgba(0,212,255,.3);}
.logo-dot::before{content:'';width:6px;height:6px;background:var(--accent);border-radius:50%;
  box-shadow:0 0 5px var(--accent);animation:pulse 2s infinite;}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1);}50%{opacity:.3;transform:scale(.6);}}
.logo h1{font-size:.78rem;font-weight:800;letter-spacing:.12em;color:var(--accent);}
.logo small{font-family:'Share Tech Mono';font-size:.52rem;color:var(--dim);display:block;}
.hdr-r{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;}
.pill{font-family:'Share Tech Mono';font-size:.6rem;padding:.2rem .55rem;border-radius:4px;
  border:1px solid var(--accent2);color:var(--accent2);background:rgba(0,255,157,.05);}
.btn{font-family:'Exo 2';font-weight:600;font-size:.72rem;padding:.3rem .75rem;
  border-radius:6px;cursor:pointer;transition:all .2s;letter-spacing:.04em;border:none;}
.btn-blue{border:1px solid var(--accent)!important;color:var(--accent);background:rgba(0,212,255,.07);}
.btn-blue:hover{background:rgba(0,212,255,.18);box-shadow:0 0 12px rgba(0,212,255,.25);}
.btn-green{border:1px solid var(--accent2)!important;color:var(--accent2);background:rgba(0,255,157,.06);}
.btn-green:hover{background:rgba(0,255,157,.15);}
.btn-warn{border:1px solid var(--warn)!important;color:var(--warn);background:rgba(255,204,0,.06);position:relative;}
.btn-warn:hover{background:rgba(255,204,0,.14);}
.btn:disabled{opacity:.5;cursor:not-allowed;}
a.btn{text-decoration:none;display:inline-block;}
.warn-badge{position:absolute;top:-7px;right:-7px;background:var(--danger);color:#fff;
  font-size:.56rem;font-weight:800;width:16px;height:16px;border-radius:50%;
  display:none;align-items:center;justify-content:center;font-family:'Exo 2';}
.tabs{display:flex;border-bottom:1px solid var(--border);background:rgba(10,21,32,.9);
  padding:0 1.5rem;position:sticky;top:46px;z-index:200;}
.tab{font-family:'Exo 2';font-size:.75rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
  padding:.7rem 1.2rem;cursor:pointer;color:var(--dim);border-bottom:2px solid transparent;transition:all .2s;}
.tab:hover{color:var(--text);}.tab.active{color:var(--accent);border-bottom-color:var(--accent);}
.tab.tab-warn{color:rgba(255,204,0,.6);}.tab.tab-warn.active{color:var(--warn);border-bottom-color:var(--warn);}
main{padding:1.2rem 1.5rem;max-width:1600px;margin:0 auto;}
.page{display:none;}.page.active{display:block;}
/* Map page breaks out of main padding */
#page-map{margin:-1.2rem -1.5rem;}
/* Summary stats - compact horizontal bar */
.summary-grid{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:1rem;}
.sc{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:.5rem .9rem;
  position:relative;overflow:hidden;display:flex;align-items:center;gap:.7rem;flex:1;min-width:140px;}
.sc::before{content:'';position:absolute;top:0;left:0;bottom:0;width:2px;background:linear-gradient(180deg,var(--accent),var(--accent2));}
.sc .lbl{font-size:.58rem;letter-spacing:.12em;color:var(--dim);text-transform:uppercase;white-space:nowrap;}
.sc .val{font-size:1.35rem;font-weight:800;line-height:1;}
.sc .sub{font-family:'Share Tech Mono';font-size:.58rem;color:var(--dim);}
.sc-text{display:flex;flex-direction:column;gap:.1rem;}
.sc.clickable{cursor:pointer;transition:border-color .2s;}.sc.clickable:hover{border-color:var(--danger);}
.section{margin-bottom:1.4rem;}
.sec-hdr{display:flex;align-items:center;gap:.5rem;margin-bottom:.6rem;padding-bottom:.4rem;border-bottom:1px solid var(--border);}
.sec-hdr h2{font-size:.78rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;}
.cnt{font-family:'Share Tech Mono';font-size:.62rem;padding:.12rem .45rem;border-radius:99px;border:1px solid var(--border);background:rgba(255,255,255,.03);}
/* ── NOC STATUS BANNER ── */
.noc-banner{display:flex;align-items:center;gap:1.2rem;padding:.75rem 1.2rem;
  border-radius:10px;margin-bottom:1rem;border:2px solid transparent;
  transition:all .5s ease;position:relative;overflow:hidden;}
.noc-banner::before{content:'';position:absolute;inset:0;opacity:.07;
  background:radial-gradient(ellipse at left,currentColor,transparent 70%);}
.noc-banner.ok{border-color:var(--accent2);color:var(--accent2);background:rgba(0,255,157,.05);}
.noc-banner.warn{border-color:var(--warn);color:var(--warn);background:rgba(255,204,0,.05);}
.noc-banner.fail{border-color:var(--danger);color:var(--danger);background:rgba(255,45,85,.05);
  animation:noc-pulse 1.8s ease-in-out infinite;}
@keyframes noc-pulse{0%,100%{box-shadow:0 0 0 0 rgba(255,45,85,0);}50%{box-shadow:0 0 24px 4px rgba(255,45,85,.25);}}
.noc-indicator{width:18px;height:18px;border-radius:50%;flex-shrink:0;
  box-shadow:0 0 12px currentColor;background:currentColor;animation:noc-dot 2s ease-in-out infinite;}
@keyframes noc-dot{0%,100%{opacity:1;}50%{opacity:.4;}}
.noc-banner.fail .noc-indicator{animation:noc-dot-fail .6s ease-in-out infinite;}
@keyframes noc-dot-fail{0%,100%{opacity:1;transform:scale(1);}50%{opacity:.3;transform:scale(.7);}}
.noc-text{font-family:'Exo 2';font-size:1.4rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase;line-height:1;}
.noc-detail{font-family:'Share Tech Mono';font-size:.72rem;opacity:.8;flex:1;}
.noc-time{font-family:'Share Tech Mono';font-size:.62rem;opacity:.5;white-space:nowrap;}
/* ── SMART CARD LAYOUT ── */
.test-grid.count-1{grid-template-columns:1fr;}
.test-grid.count-2{grid-template-columns:1fr 1fr;}
.test-grid.count-1 .tc,.test-grid.count-2 .tc{max-width:600px;}
/* Force minimum 3 columns on test grid */
.test-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(280px,30%),1fr));gap:.7rem;}
.test-grid.tr-grid{grid-template-columns:repeat(auto-fill,minmax(min(400px,45%),1fr));}
.tc{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:.9rem;
  transition:border-color .2s,box-shadow .2s;min-width:0;overflow:hidden;}
.tc:hover{border-color:rgba(0,212,255,.35);box-shadow:0 0 16px rgba(0,212,255,.07);}
.tc.ok{border-left:3px solid var(--accent2);}.tc.fail{border-left:3px solid var(--danger);}
.tc.warn{border-left:3px solid var(--warn);}.tc.error{border-left:3px solid #ff6b35;}
.tc.unknown{border-left:3px solid var(--dim);}
.tc-hdr{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.6rem;}
.tc-name{font-weight:600;font-size:.88rem;}
.tc-tgt{font-family:'Share Tech Mono';font-size:.58rem;color:var(--dim);margin-top:.15rem;word-break:break-all;}
.badge{font-family:'Share Tech Mono';font-size:.6rem;font-weight:700;padding:.15rem .5rem;border-radius:4px;letter-spacing:.08em;white-space:nowrap;}
.badge.ok{background:rgba(0,255,157,.1);color:var(--accent2);border:1px solid rgba(0,255,157,.3);}
.badge.fail{background:rgba(255,45,85,.1);color:var(--danger);border:1px solid rgba(255,45,85,.3);}
.badge.warn{background:rgba(255,204,0,.1);color:var(--warn);border:1px solid rgba(255,204,0,.3);}
.badge.error{background:rgba(255,107,53,.1);color:#ff6b35;border:1px solid rgba(255,107,53,.3);}
.badge.unknown{background:rgba(74,122,153,.1);color:var(--dim);border:1px solid var(--border);}
.metrics{display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:.45rem;}
.metric .ml{font-size:.56rem;color:var(--dim);text-transform:uppercase;letter-spacing:.1em;}
.metric .mv{font-family:'Share Tech Mono';font-size:.85rem;}
.mv.warn-val{color:var(--warn);}.mv.ok-val{color:var(--accent2);}
.uptime-row{display:flex;align-items:center;gap:.5rem;margin-bottom:.45rem;}
.uptime-label{font-family:'Share Tech Mono';font-size:.58rem;color:var(--dim);white-space:nowrap;}
.uptime-bar-bg{flex:1;height:4px;background:rgba(255,255,255,.07);border-radius:3px;overflow:hidden;}
.uptime-bar-fill{height:100%;border-radius:3px;transition:width .5s;}
.uptime-pct{font-family:'Share Tech Mono';font-size:.6rem;white-space:nowrap;min-width:36px;text-align:right;}
.chart-wrap{height:52px;position:relative;margin-top:.4rem;}
.chart-wrap canvas{width:100%!important;}
.fail-toggle{margin-top:.5rem;font-family:'Share Tech Mono';font-size:.63rem;color:var(--dim);
  cursor:pointer;display:flex;align-items:center;gap:.3rem;user-select:none;padding:.2rem 0;}
.fail-toggle:hover{color:var(--danger);}
.fail-arrow{transition:transform .2s;display:inline-block;}
.fail-toggle.open .fail-arrow{transform:rotate(90deg);}
.fail-list{display:none;margin-top:.3rem;border-top:1px solid var(--border);padding-top:.4rem;}
.fail-list.open{display:block;}
.fail-item{font-family:'Share Tech Mono';font-size:.6rem;padding:.28rem .45rem;
  border-left:2px solid var(--danger);background:rgba(255,45,85,.04);margin-bottom:.28rem;border-radius:0 4px 4px 0;}
.fail-ts{color:var(--dim);font-size:.56rem;}
.fail-msg{color:#ffb3c0;margin-top:.1rem;word-break:break-word;}
.bw-prog-wrap{margin-top:.4rem;}
.bw-prog-lbl{font-family:'Share Tech Mono';font-size:.58rem;color:var(--dim);margin-bottom:.2rem;}
.bw-prog-bar{height:5px;background:rgba(255,255,255,.07);border-radius:3px;overflow:hidden;}
.bw-prog-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--accent),var(--accent2));}
.hops-wrap{overflow-x:auto;margin-top:.6rem;max-width:100%;}
.hops-table{width:100%;border-collapse:collapse;font-family:'Share Tech Mono';font-size:.68rem;table-layout:fixed;}
.hops-table th{color:var(--dim);font-weight:400;text-align:left;padding:.25rem .4rem;border-bottom:1px solid var(--border);white-space:nowrap;}
.hops-table td{padding:.25rem .4rem;border-bottom:1px solid rgba(13,45,74,.3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.hops-table tr:last-child td{border:none;}
.hop-bar{height:4px;background:var(--trace);border-radius:2px;min-width:2px;opacity:.7;}
.warn-toolbar{display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:.5rem;}
.warn-filters{display:flex;gap:.35rem;flex-wrap:wrap;}
.wf-btn{font-family:'Share Tech Mono';font-size:.63rem;padding:.22rem .6rem;border-radius:4px;
  border:1px solid var(--border);color:var(--dim);background:var(--panel2);cursor:pointer;transition:all .15s;}
.wf-btn.active{border-color:var(--warn);color:var(--warn);background:rgba(255,204,0,.07);}
.warn-list{display:flex;flex-direction:column;gap:.45rem;}
.warn-item{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:.75rem 1rem;
  display:flex;align-items:flex-start;gap:.75rem;}
.warn-item.acked{opacity:.4;}
.warn-item.type-down{border-left:3px solid var(--danger);}
.warn-item.type-up{border-left:3px solid var(--accent2);}
.warn-item.type-latency,.warn-item.type-loss,.warn-item.type-speed{border-left:3px solid var(--warn);}
.wi-icon{font-size:.95rem;margin-top:.1rem;flex-shrink:0;}
.wi-body{flex:1;min-width:0;}
.wi-msg{font-size:.8rem;font-weight:600;}
.wi-meta{font-family:'Share Tech Mono';font-size:.6rem;color:var(--dim);margin-top:.18rem;}
.wi-ack{font-family:'Share Tech Mono';font-size:.58rem;padding:.18rem .45rem;
  border:1px solid var(--border);color:var(--dim);background:none;border-radius:4px;
  cursor:pointer;flex-shrink:0;transition:all .15s;white-space:nowrap;align-self:center;}
.wi-ack:hover{border-color:var(--accent2);color:var(--accent2);}
.no-warns{text-align:center;padding:3rem;font-family:'Share Tech Mono';color:var(--dim);}
.mgr-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(305px,1fr));gap:1rem;}
.mgr-card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:1.1rem;}
.mgr-card h3{font-size:.75rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  margin-bottom:.9rem;padding-bottom:.5rem;border-bottom:1px solid var(--border);}
.target-list{display:flex;flex-direction:column;gap:.38rem;margin-bottom:.8rem;min-height:30px;}
.ti{display:flex;align-items:center;justify-content:space-between;background:var(--panel2);
  border:1px solid var(--border);border-radius:6px;padding:.38rem .6rem;}
.ti-name{font-family:'Share Tech Mono';font-size:.68rem;color:var(--text);font-weight:700;}
.ti-val{font-family:'Share Tech Mono';font-size:.58rem;color:var(--dim);}
.btn-del{background:none;border:none;color:var(--dim);cursor:pointer;font-size:.78rem;padding:.1rem .3rem;border-radius:4px;transition:color .15s;}
.btn-del:hover{color:var(--danger);}
.add-form{display:flex;flex-direction:column;gap:.38rem;}
.add-form input,.add-form select,.add-form textarea{
  background:var(--panel2);border:1px solid var(--border);color:var(--text);
  font-family:'Share Tech Mono';font-size:.7rem;padding:.36rem .58rem;border-radius:6px;
  outline:none;transition:border-color .2s;width:100%;}
.add-form input:focus,.add-form select:focus,.add-form textarea:focus{border-color:var(--accent);}
.add-form textarea{resize:vertical;min-height:50px;}
.add-form .row{display:flex;gap:.38rem;}.add-form .row>*{flex:1;min-width:0;}
.add-form label{font-size:.57rem;color:var(--dim);letter-spacing:.08em;text-transform:uppercase;display:block;margin-bottom:.12rem;}
.form-group{display:flex;flex-direction:column;}
.save-notice{font-family:'Share Tech Mono';font-size:.62rem;color:var(--accent2);margin-top:.38rem;opacity:0;transition:opacity .4s;}
.save-notice.show{opacity:1;}
.last-upd{font-family:'Share Tech Mono';font-size:.63rem;color:var(--dim);text-align:right;margin-bottom:.8rem;}
.empty{text-align:center;padding:3rem;color:var(--dim);font-family:'Share Tech Mono';}
.toast-stack{position:fixed;bottom:1.5rem;right:1.5rem;display:flex;flex-direction:column-reverse;gap:.4rem;z-index:999;pointer-events:none;max-width:320px;}
.toast{background:var(--panel2);font-family:'Share Tech Mono';font-size:.73rem;padding:.55rem .9rem;
  border-radius:7px;box-shadow:0 4px 20px rgba(0,0,0,.5);opacity:0;transform:translateY(10px);
  transition:all .3s;border-left:3px solid var(--accent);}
.toast.show{opacity:1;transform:translateY(0);}
.toast.t-warn{border-left-color:var(--warn);color:var(--warn);}
.toast.t-danger{border-left-color:var(--danger);color:var(--danger);}
.toast.t-ok{border-left-color:var(--accent2);color:var(--accent2);}
.toast.t-info{border-left-color:var(--accent);color:var(--accent);}
.type-ping{color:var(--accent2);}.type-http{color:var(--accent);}
.type-api{color:var(--api);}.type-traceroute{color:var(--trace);}.type-bandwidth{color:var(--warn);}
/* ── MAP TAB ── */
/* ── MAP TAB ── */
.map-container{display:flex;height:calc(100vh - 122px);min-height:400px;border:none;overflow:hidden;}
.map-viewport{flex:1;overflow:auto;background:#0a1218;position:relative;}
.map-world{position:relative;display:inline-block;}
#map-bg{display:block;pointer-events:none;user-select:none;}
#map-grid-canvas{display:none;pointer-events:none;position:absolute;top:0;left:0;}
.map-world.blank #map-bg{display:none;}
.map-world.blank #map-grid-canvas{display:block;}
.map-world.drag-over{outline:2px dashed var(--accent);outline-offset:-3px;}
.map-sidebar{width:230px;background:var(--panel);border-left:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;flex-shrink:0;}
.map-sidebar-hdr{padding:.7rem .8rem .4rem;border-bottom:1px solid var(--border);}
.map-sidebar-filters{display:flex;gap:.3rem;padding:.4rem .8rem;border-bottom:1px solid var(--border);flex-wrap:wrap;}
#map-sidebar-list{flex:1;overflow-y:auto;padding:.4rem;}
.map-dev-item{padding:.4rem .5rem;border-radius:6px;border:1px solid var(--border);margin-bottom:.3rem;
  cursor:grab;transition:all .15s;display:flex;align-items:center;gap:.5rem;background:var(--panel2);}
.map-dev-item:hover{border-color:var(--accent);background:rgba(0,212,255,.07);}
.map-dev-item.dragging{opacity:.35;}
.map-dev-item.mapped{border-color:rgba(0,255,157,.25);}
.map-dev-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;}
.map-dev-dot.online{background:var(--accent2);box-shadow:0 0 4px var(--accent2);}
.map-dev-dot.offline{background:var(--danger);box-shadow:0 0 4px var(--danger);}
.map-dev-dot.unknown{background:var(--dim);}
.map-dev-name{font-size:.65rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.map-dev-ip{font-family:'Share Tech Mono';font-size:.57rem;color:var(--dim);}
.map-node{position:absolute;transform:translate(-50%,-50%);cursor:pointer;z-index:10;transition:transform .15s;display:flex;align-items:center;}
.map-node:hover{transform:translate(-50%,-50%) scale(1.25);z-index:20;}
.map-node-icon{width:22px;height:22px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.map-node.online  .map-node-icon{animation:map-on-icon 2.5s ease-in-out infinite;}
.map-node.offline .map-node-icon{animation:map-off-icon .8s ease-in-out infinite;}
@keyframes map-on-icon{0%,100%{opacity:1;filter:drop-shadow(0 0 4px currentColor);}50%{opacity:.75;filter:drop-shadow(0 0 10px currentColor);}}
@keyframes map-off-icon{0%,100%{opacity:1;}50%{opacity:.2;}}
.map-node-label{position:absolute;left:26px;top:50%;transform:translateY(-50%);font-size:.58rem;font-weight:600;
  white-space:nowrap;background:rgba(5,10,15,.88);padding:.1rem .35rem;border-radius:3px;
  pointer-events:none;border:1px solid rgba(255,255,255,.08);}
.map-node.connect-src .map-node-icon{outline:2px solid var(--accent);border-radius:4px;outline-offset:2px;}
.map-tooltip{position:fixed;z-index:999;background:var(--panel);border:1px solid var(--border);
  border-radius:8px;padding:.7rem .9rem;min-width:180px;box-shadow:0 4px 24px rgba(0,0,0,.6);pointer-events:none;}
.map-tooltip.pinned{pointer-events:auto;}
.map-tt-name{font-weight:700;font-size:.8rem;margin-bottom:.25rem;}
.map-tt-ip{font-family:'Share Tech Mono';font-size:.65rem;color:var(--dim);margin-bottom:.2rem;}
.map-tt-status{font-size:.68rem;font-weight:700;margin-bottom:.2rem;}
.map-tt-time{font-family:'Share Tech Mono';font-size:.58rem;color:var(--dim);margin-bottom:.5rem;}
.map-tt-remove{font-size:.6rem;padding:.2rem .5rem;border-radius:4px;cursor:pointer;
  border:1px solid var(--danger);color:var(--danger);background:transparent;display:none;}
.map-tooltip.pinned .map-tt-remove{display:block;}
.map-saved-item{padding:.5rem .6rem;border-radius:6px;border:1px solid var(--border);margin-bottom:.3rem;
  background:var(--panel2);cursor:pointer;transition:border-color .15s;}
.map-saved-item:hover{border-color:var(--accent);}
.map-saved-item.active-map{border-color:var(--accent2);background:rgba(0,255,157,.05);}
.map-saved-name{font-size:.68rem;font-weight:600;margin-bottom:.15rem;}
.map-saved-meta{font-family:'Share Tech Mono';font-size:.57rem;color:var(--dim);}
.map-saved-actions{display:flex;gap:.3rem;margin-top:.3rem;}

.map-menu-item{padding:.55rem .9rem;font-size:.72rem;cursor:pointer;color:var(--text);
  transition:background .15s;border-bottom:1px solid var(--border);}
.map-menu-item:last-child{border-bottom:none;}
.map-menu-item:hover{background:rgba(0,212,255,.08);color:var(--accent);}
.map-menu-item.active{color:var(--accent2);background:rgba(0,255,157,.06);}
#map-edit-menu{z-index:1000!important;}
/* Sidebar collapse transition */
.map-sidebar{transition:width .2s ease;}
.map-sidebar.collapsed{width:0;overflow:hidden;border:none;}
#map-sidebar-tab{display:none;}
#map-sidebar-tab.visible{display:flex!important;}
/* Map ping status dot on button */
#map-ping-btn.pinging{animation:ping-btn-pulse .7s ease-in-out infinite;}
@keyframes ping-btn-pulse{0%,100%{opacity:1;}50%{opacity:.5;}}

/* ── LEGEND CHECKBOXES ── */
.leg-item{display:flex;align-items:center;gap:.25rem;cursor:pointer;font-size:.6rem;user-select:none;}
.leg-item input[type=checkbox]{width:11px;height:11px;accent-color:var(--accent);cursor:pointer;margin:0;}
.leg-item span{white-space:nowrap;}

/* ── MOBILE HAMBURGER ── */
.mob-menu-btn{display:none;background:none;border:1px solid var(--border);border-radius:6px;
  color:var(--accent);font-size:1.1rem;padding:.2rem .5rem;cursor:pointer;line-height:1;}
.mob-nav{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(5,10,20,.97);
  z-index:2000;flex-direction:column;padding:1.5rem;}
.mob-nav.open{display:flex;}
.mob-nav-close{align-self:flex-end;background:none;border:1px solid var(--border);border-radius:6px;
  color:var(--dim);font-size:1.2rem;padding:.3rem .7rem;cursor:pointer;margin-bottom:1.5rem;}
.mob-nav-item{padding:1rem 1.2rem;font-family:'Exo 2';font-size:1.1rem;font-weight:700;
  letter-spacing:.1em;text-transform:uppercase;color:var(--dim);border-bottom:1px solid var(--border);
  cursor:pointer;display:flex;align-items:center;gap:.8rem;border-radius:6px;margin-bottom:.3rem;}
.mob-nav-item:hover,.mob-nav-item.active{color:var(--accent);background:rgba(0,212,255,.07);}
.mob-nav-item .mob-icon{font-size:1.3rem;width:2rem;text-align:center;}
.mob-nav-actions{display:flex;gap:.6rem;margin-top:1.2rem;flex-wrap:wrap;}

/* ── MOBILE RESPONSIVE ── */
@media(max-width:768px){
  /* Header */
  header{padding:.3rem .7rem;}
  .logo h1{font-size:.7rem;}
  .logo small{font-size:.48rem;}
  .logo-dot{width:16px;height:16px;}
  .hdr-r .btn,.hdr-r .pill{display:none;}
  .mob-menu-btn{display:block;}

  /* Tabs — hidden on mobile, replaced by hamburger */
  .tabs{display:none;}

  /* Main padding */
  main{padding:.6rem .5rem;}
  #page-map{margin:-.6rem -.5rem;}

  /* Summary cards — 2 per row on mobile */
  .summary-grid{gap:.4rem;}
  .sc{min-width:calc(50% - .2rem);padding:.4rem .6rem;}
  .sc .val{font-size:1.1rem;}

  /* NOC banner compact */
  .noc-banner{padding:.5rem .7rem;gap:.6rem;flex-wrap:wrap;}
  .noc-banner .noc-label{font-size:.7rem;}

  /* Test cards — single column */
  .test-grid{grid-template-columns:1fr!important;}

  /* Map toolbar wrap */
  #zoom-bar-map{flex-wrap:wrap;gap:.3rem;padding:.3rem .5rem;}
  #zoom-bar-map>div:first-child{flex:1;min-width:0;}
  #zoom-bar-map>div:last-child{flex-wrap:wrap;gap:.3rem;}

  /* Map container full height */
  .map-container{height:calc(100vh - 96px);}

  /* Map sidebar full width overlay on mobile */
  .map-sidebar{position:absolute;right:0;top:0;bottom:0;z-index:100;width:80vw;max-width:280px;
    box-shadow:-4px 0 20px rgba(0,0,0,.6);}

  /* Legend wraps on mobile */
  #map-legend{bottom:.4rem;left:.4rem;font-size:.55rem;gap:.3rem .5rem;max-width:calc(100vw - 2rem);}

  /* Device cards 1 col */
  .dev-grid,.dev-cards{grid-template-columns:1fr!important;}

  /* Zoom bars */
  #zoom-bar-dashboard,#zoom-bar-devices{padding:.3rem .5rem;}

  /* Warnings table */
  .warn-table th:nth-child(3),.warn-table td:nth-child(3){display:none;}
}

@media(max-width:480px){
  .sc{min-width:calc(50% - .2rem);}
  .map-sidebar{width:90vw;}
  #map-legend .leg-item span{font-size:.55rem;}
}

/* Landscape phone */
@media(max-height:500px) and (max-width:900px){
  header{padding:.2rem .6rem;}
  .map-container{height:calc(100vh - 80px);}
  #zoom-bar-map{padding:.2rem .5rem;}
}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="logo-dot"></div>
    <div><h1>NETMONITOR</h1><small>AUTOMATED NETWORK DIAGNOSTICS v5.5</small></div>
  </div>
  <div class="hdr-r">
    <span class="pill">&#9201; SCHEDULED</span>
    <a class="btn btn-green" href="javascript:void(0)" onclick="fetch(api('/download-csv')).then(r=>r.blob()).then(b=>{const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='network_results.csv';a.click()})">&#8595; CSV</a>
    <button class="btn btn-warn" onclick="showTab('warnings',document.querySelector('[data-tab=warnings]'))">
      &#9888; Warnings<span class="warn-badge" id="warn-badge">0</span>
    </button>
    <button class="btn btn-blue" id="run-btn" onclick="runTests()">&#9654; RUN NOW</button>
    <button class="mob-menu-btn" onclick="openMobNav()" title="Menu">&#9776;</button>
  </div>
</header>

<!-- Mobile nav overlay -->
<div class="mob-nav" id="mob-nav">
  <button class="mob-nav-close" onclick="closeMobNav()">&#10005;</button>
  <div class="mob-nav-item active" data-tab="dashboard" onclick="mobNavGo('dashboard')">
    <span class="mob-icon">&#128187;</span> Dashboard
  </div>
  <div class="mob-nav-item" data-tab="map" onclick="mobNavGo('map')">
    <span class="mob-icon">&#127757;</span> Site Map
  </div>
  <div class="mob-nav-item" data-tab="devices" onclick="mobNavGo('devices')">
    <span class="mob-icon">&#128246;</span> Network Devices
  </div>
  <div class="mob-nav-item" data-tab="targets" onclick="mobNavGo('targets')">
    <span class="mob-icon">&#9881;</span> Targets
  </div>
  <div class="mob-nav-item tab-warn" data-tab="warnings" onclick="mobNavGo('warnings')">
    <span class="mob-icon">&#9888;</span> Warnings <span class="warn-badge" id="warn-badge-mob">0</span>
  </div>
  <div class="mob-nav-actions">
    <button class="btn btn-blue" onclick="runTests();closeMobNav()">&#9654; Run Now</button>
    <button class="btn btn-warn" onclick="mobNavGo('warnings')">&#9888; Warnings</button>
    <button class="btn btn-green" onclick="fetch(api('/download-csv')).then(r=>r.blob()).then(b=>{const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='network_results.csv';a.click()})">&#8595; CSV</button>
  </div>
</div>
<div class="tabs">
  <div class="tab active"   data-tab="dashboard" onclick="showTab('dashboard',this)">Dashboard</div>
  <div class="tab"          data-tab="map"       onclick="showTab('map',this)">&#127757; Site Map</div>
  <div class="tab"          data-tab="devices"   onclick="showTab('devices',this)">&#128246; Network Devices</div>
  <div class="tab"          data-tab="targets"   onclick="showTab('targets',this)">&#9881; Targets</div>
  <div class="tab tab-warn" data-tab="warnings"  onclick="showTab('warnings',this)">&#9888; Warnings</div>
</div>
<!-- Zoom bars per tab -->
<div class="zoom-bar" id="zoom-bar-dashboard">
  <label>&#128269; Card Size:</label>
  <input type="range" min="1" max="5" value="3" id="zoom-dash" oninput="applyZoom('dash',this.value)">
  <span id="zoom-dash-lbl">M</span>
</div>
<div class="zoom-bar" id="zoom-bar-devices" style="display:none;">
  <label>&#128269; Card Size:</label>
  <input type="range" min="1" max="5" value="3" id="zoom-dev" oninput="applyZoom('dev',this.value)">
  <span id="zoom-dev-lbl">M</span>
</div>
<div class="zoom-bar" id="zoom-bar-map" style="display:none;justify-content:space-between;">
  <div style="display:flex;align-items:center;gap:.6rem;">
    <label>&#128269; Map Zoom:</label>
    <input type="range" min="10" max="200" value="100" id="map-zoom-slider" oninput="setMapZoom(this.value)">
    <span id="map-zoom-lbl" style="font-family:'Share Tech Mono';font-size:.6rem;color:var(--accent);min-width:36px;">100%</span>
  </div>
  <div style="display:flex;gap:.5rem;align-items:center;">
    <!-- Pencil edit toggle + drop-up menu -->
    <div style="position:relative;" id="map-edit-wrap">
      <button class="btn" id="map-pencil-btn" onclick="toggleMapEdit()"
        title="Edit Mode" style="font-size:1rem;padding:.25rem .6rem;border:1px solid var(--border);">&#9998;</button>
      <!-- Drop-up menu — visible only in edit mode -->
      <div id="map-edit-menu" style="display:none;position:absolute;top:calc(100% + 6px);right:0;
        background:var(--panel);border:1px solid var(--border);border-radius:8px;
        box-shadow:0 -4px 20px rgba(0,0,0,.5);min-width:180px;z-index:200;overflow:hidden;">
        <div class="map-menu-item" id="map-connect-btn" onclick="toggleConnectMode()">&#9135; Connect Nodes</div>
        <div class="map-menu-item" id="map-bg-toggle-btn" onclick="toggleMapBackground()">&#11036; Blank Canvas</div>
        <div class="map-menu-item" onclick="document.getElementById('map-upload-input').click()">&#8659; Upload Map Image</div>
        <div class="map-menu-item" onclick="clearMapImage()" style="color:var(--danger);">&#10005; Clear Map Image</div>
        <input type="file" id="map-upload-input" accept="image/*" style="display:none" onchange="uploadMapImage(this)">
      </div>
    </div>
    <!-- Ping mapped nodes button -->
    <button class="btn btn-blue" id="map-ping-btn" onclick="pingMappedNodes()" title="Ping all nodes on map">
      &#9654; Ping Map
    </button>
    <button class="btn" id="map-meraki-sync-btn" onclick="merakiSyncNames()"
      title="Sync device names from Meraki"
      style="border:1px solid #00a651;color:#00a651;background:rgba(0,166,81,.07);font-size:.68rem;">
      &#8635; Meraki Sync
    </button>
    <span style="font-family:'Share Tech Mono';font-size:.6rem;color:var(--dim);" id="map-edit-hint">View mode</span>
    <!-- Live update indicator -->
    <span id="map-live-indicator" style="display:flex;align-items:center;gap:.3rem;font-family:'Share Tech Mono';font-size:.58rem;color:var(--accent2);">
      <span id="map-live-dot" style="width:7px;height:7px;border-radius:50%;background:var(--accent2);display:inline-block;animation:map-on-icon 2.5s ease-in-out infinite;"></span>
      LIVE · <span id="map-live-time">--</span>
    </span>
  </div>
</div>
<!-- Progress overlays -->
<div class="progress-wrap" id="run-progress-box">
  <div class="progress-title"><span>&#9654; RUN NOW</span><span id="run-pct">0%</span></div>
  <div class="progress-bar-bg"><div class="progress-bar-fill" id="run-bar" style="width:0%"></div></div>
  <div class="progress-stage" id="run-stage">Starting...</div>
</div>
<div class="progress-wrap" id="ping-progress-box">
  <div class="progress-title"><span>&#9654; PING ALL</span><span id="ping-pct">0%</span></div>
  <div class="progress-bar-bg"><div class="progress-bar-fill" id="ping-bar" style="width:0%;background:linear-gradient(90deg,var(--accent2),#00ff9d);"></div></div>
  <div class="progress-stage" id="ping-stage">Starting...</div>
</div>
<main>
  <div class="page active" id="page-dashboard">
    <div class="noc-banner" id="noc-banner">
      <div class="noc-indicator" id="noc-indicator"></div>
      <div class="noc-text" id="noc-text">LOADING...</div>
      <div class="noc-detail" id="noc-detail"></div>
      <div class="noc-time" id="noc-time"></div>
    </div>
    <div class="last-upd" id="last-updated">Loading...</div>
    <div class="summary-grid" id="summary"></div>
    <div id="sections"></div>
  </div>
  <div class="page" id="page-map">
    <div class="map-container" id="map-container">
      <div class="map-viewport" id="map-viewport">
        <div class="map-world" id="map-world">
          <!-- Background: either image or blank grid canvas -->
          <img id="map-bg" src="/map-image" alt="Site Map"
               onerror="this.src='';this.style.display='none';">
          <canvas id="map-grid-canvas"></canvas>
          <!-- SVG layer for connection lines -->
          <svg id="map-lines" style="position:absolute;inset:0;pointer-events:none;overflow:visible;"></svg>
          <!-- Node layer -->
          <div id="map-nodes"></div>
        </div>
      </div>
      <!-- Device/Maps sidebar -->
      <div class="map-sidebar" id="map-sidebar">
        <!-- Tab switcher -->
        <div style="display:flex;border-bottom:1px solid var(--border);">
          <div id="msb-tab-devices" onclick="switchMapSidebarTab('devices')"
            style="flex:1;padding:.45rem;text-align:center;font-size:.65rem;font-weight:700;letter-spacing:.08em;
            color:var(--accent);border-bottom:2px solid var(--accent);cursor:pointer;">DEVICES</div>
          <div id="msb-tab-maps" onclick="switchMapSidebarTab('maps')"
            style="flex:1;padding:.45rem;text-align:center;font-size:.65rem;font-weight:700;letter-spacing:.08em;
            color:var(--dim);border-bottom:2px solid transparent;cursor:pointer;">MAPS</div>
          <button onclick="toggleMapSidebar()" id="map-sidebar-collapse" title="Collapse"
            style="background:none;border:none;border-left:1px solid var(--border);color:var(--dim);
            cursor:pointer;font-size:.75rem;padding:0 .5rem;">&#9654;</button>
        </div>

        <!-- DEVICES panel -->
        <div id="msb-devices-panel">
          <div style="padding:.5rem .8rem .3rem;">
            <input id="map-dev-search" placeholder="Search..." oninput="renderMapSidebar()"
              style="font-size:.65rem;padding:.2rem .5rem;background:var(--panel2);border:1px solid var(--border);
              border-radius:4px;color:var(--text);width:100%;">
          </div>
          <div class="map-sidebar-filters">
            <button class="df-btn active" onclick="mapDevFilter='all';renderMapSidebar();setMapFilterActive(this)">All</button>
            <button class="df-btn" onclick="mapDevFilter='switches';renderMapSidebar();setMapFilterActive(this)">&#128268;</button>
            <button class="df-btn" onclick="mapDevFilter='aps';renderMapSidebar();setMapFilterActive(this)">&#128246;</button>
            <button class="df-btn" onclick="mapDevFilter='unmapped';renderMapSidebar();setMapFilterActive(this)">Unmapped</button>
          </div>
          <div id="map-sidebar-list" style="flex:1;overflow-y:auto;padding:.4rem;"></div>
        </div>

        <!-- MAPS panel -->
        <div id="msb-maps-panel" style="display:none;flex:1;overflow:hidden;display:none;flex-direction:column;">
          <div style="padding:.6rem .8rem;border-bottom:1px solid var(--border);display:flex;gap:.4rem;">
            <input id="map-name-input" placeholder="Map name..." maxlength="40"
              style="flex:1;font-size:.65rem;padding:.25rem .5rem;background:var(--panel2);
              border:1px solid var(--border);border-radius:4px;color:var(--text);">
            <button class="btn btn-green" style="font-size:.6rem;padding:.2rem .5rem;" onclick="saveCurrentMap()">Save</button>
          </div>
          <div id="map-saved-list" style="flex:1;overflow-y:auto;padding:.4rem;"></div>
        </div>
      </div>
      <!-- Collapsed sidebar tab -->
      <div id="map-sidebar-tab" style="display:none;width:24px;background:var(--panel);border-left:1px solid var(--border);
        cursor:pointer;align-items:center;justify-content:center;flex-direction:column;gap:.4rem;"
        onclick="toggleMapSidebar()" title="Expand sidebar">
        <span style="font-size:.7rem;color:var(--accent);">&#9664;</span>
        <span style="writing-mode:vertical-rl;font-size:.6rem;color:var(--dim);letter-spacing:.1em;">PANEL</span>
      </div>
    </div>
    <!-- Node tooltip -->
    <div class="map-tooltip" id="map-tooltip" style="display:none;">
      <div class="map-tt-name" id="map-tt-name"></div>
      <div class="map-tt-ip" id="map-tt-ip"></div>
      <div class="map-tt-status" id="map-tt-status"></div>
      <div class="map-tt-time" id="map-tt-time"></div>
      <button class="map-tt-remove" id="map-tt-remove" onclick="removeMapNode()">&#10005; Remove from map</button>
    </div>
    <!-- Icon legend with filter checkboxes -->
    <div id="map-legend" style="position:absolute;bottom:.6rem;left:.6rem;background:rgba(5,10,15,.92);border:1px solid var(--border);
      border-radius:8px;padding:.4rem .6rem;display:flex;flex-wrap:wrap;gap:.4rem .7rem;align-items:center;z-index:50;max-width:calc(100% - 1.2rem);">
      <span style="font-family:'Share Tech Mono';font-size:.55rem;color:var(--dim);">LEGEND</span>
      <label class="leg-item" data-type="switch">  <input type="checkbox" checked onchange="toggleMapType('switch',this.checked)"><span style="color:#00d4ff">&#9632; Switch</span></label>
      <label class="leg-item" data-type="tb">      <input type="checkbox" checked onchange="toggleMapType('tb',this.checked)"><span style="color:#ffd700">&#9632; Ticket Box</span></label>
      <label class="leg-item" data-type="kiosk">   <input type="checkbox" checked onchange="toggleMapType('kiosk',this.checked)"><span style="color:#c084fc">&#9632; Kiosk</span></label>
      <label class="leg-item" data-type="ap">      <input type="checkbox" checked onchange="toggleMapType('ap',this.checked)"><span style="color:#00ff9d">&#9632; AP</span></label>
      <label class="leg-item" data-type="office">  <input type="checkbox" checked onchange="toggleMapType('office',this.checked)"><span style="color:#4ade80">&#9632; Office</span></label>
      <label class="leg-item" data-type="sb">      <input type="checkbox" checked onchange="toggleMapType('sb',this.checked)"><span style="color:#fb923c">&#9632; Switch Box</span></label>
      <label class="leg-item" data-type="offline"> <input type="checkbox" checked onchange="toggleMapType('offline',this.checked)"><span style="color:#ff2d55">&#9632; Offline</span></label>
    </div>
  </div>
  <div class="page" id="page-devices">
    <div class="dev-toolbar">
      <div class="dev-filters">
        <button class="df-btn active" onclick="filterDev('all',this)">All Devices</button>
        <button class="df-btn" onclick="filterDev('switch',this)">&#128268; Switches</button>
        <button class="df-btn" onclick="filterDev('ap',this)">&#128246; Access Points</button>
        <button class="df-btn" onclick="filterDev('online',this)">&#128994; Online</button>
        <button class="df-btn" onclick="filterDev('offline',this)">&#128308; Offline</button>
      </div>
      <div style="display:flex;gap:.5rem;align-items:center;">
        <input id="dev-search" placeholder="&#128269; Search devices..." style="font-family:'Exo 2';font-size:.75rem;padding:.35rem .7rem;background:var(--panel2);border:1px solid var(--border);border-radius:6px;color:var(--text);width:200px;" oninput="renderDevices()">
        <button class="btn btn-blue" onclick="pingAllDevices()">&#9654; Ping All</button>
        <button class="btn" style="border:1px solid #00a651!important;color:#00a651;background:rgba(0,166,81,.07);" onclick="showMerakiImport()">&#8659; Import Meraki</button>
        <button class="btn btn-green" onclick="showAddDevice()">+ Add Device</button>
      </div>
    </div>
    <div class="dev-stats" id="dev-stats"></div>
    <div id="dev-add-form" style="display:none;" class="mgr-card" style="margin-bottom:1rem;">
      <h3 style="color:var(--accent2);font-size:.8rem;letter-spacing:.1em;margin-bottom:.8rem;">&#43; ADD DEVICE</h3>
      <div class="row">
        <div class="form-group"><label>Section</label>
          <select id="new-dev-section"><option value="switches">Switches</option><option value="aps">Access Points</option><option value="other">Other Devices</option></select>
        </div>
        <div class="form-group"><label>Device Type (free text)</label><input id="new-dev-type" placeholder="Camera, Server, Router..."></div>
        <div class="form-group"><label>Name</label><input id="new-dev-name" placeholder="TB 01 Switch"></div>
      </div>
      <div class="row">
        <div class="form-group"><label>IP Address</label><input id="new-dev-ip" placeholder="10.44.6.50"></div>
        <div class="form-group"><label>Model</label><input id="new-dev-model" placeholder="MS125-24P"></div>
        <div class="form-group"><label>Location</label><input id="new-dev-location" placeholder="Ticket Box 01"></div>
      </div>
      <div class="form-group"><label>Notes</label><input id="new-dev-notes" placeholder="VLAN 60, Asset Tag: 1234"></div>
      <div style="display:flex;gap:.5rem;">
        <button class="btn btn-green" onclick="addDevice()">+ Add Device</button>
        <button class="btn btn-blue" onclick="document.getElementById('dev-add-form').style.display='none'">Cancel</button>
      </div>
    </div>
    <div id="meraki-import-form" style="display:none;" class="mgr-card">
      <h3 style="color:#00a651;font-size:.8rem;letter-spacing:.1em;margin-bottom:.8rem;">&#8659; IMPORT FROM MERAKI</h3>
      <p style="font-size:.75rem;color:var(--dim);margin-bottom:.8rem;">Enter your Meraki API key and Org ID to auto-import all switches and APs.</p>
      <div class="row">
        <div class="form-group"><label>API Key</label><input id="meraki-key" type="password" placeholder="Your Meraki API key"></div>
        <div class="form-group"><label>Organisation ID</label><input id="meraki-org" placeholder="762234236932456611" value="762234236932456611"></div>
      </div>
      <div style="display:flex;gap:.5rem;align-items:center;">
        <button class="btn" style="border:1px solid #00a651!important;color:#00a651;background:rgba(0,166,81,.1);" onclick="importMeraki()">&#8659; Import Now</button>
        <button class="btn btn-blue" onclick="document.getElementById('meraki-import-form').style.display='none'">Cancel</button>
        <span id="meraki-status" style="font-size:.75rem;color:var(--dim);"></span>
      </div>
    </div>
    <div class="section">
      <div class="sec-hdr">
        <span style="color:var(--accent);">&#128268;</span>
        <h2 style="color:var(--accent);">Switches</h2>
        <span class="cnt" id="sw-count">0</span>
        <span class="cnt" id="sw-online" style="color:var(--accent2);border-color:var(--accent2);">0 online</span>
      </div>
      <div class="test-grid" id="sw-grid"></div>
    </div>
    <div class="section">
      <div class="sec-hdr">
        <span style="color:var(--ap);">&#128246;</span>
        <h2 style="color:var(--ap);">Access Points</h2>
        <span class="cnt" id="ap-count">0</span>
        <span class="cnt" id="ap-online" style="color:var(--accent2);border-color:var(--accent2);">0 online</span>
      </div>
      <div class="test-grid" id="ap-grid"></div>
    </div>
    <div class="section" id="other-section" style="display:none;">
      <div class="sec-hdr">
        <span style="color:var(--warn);">&#9881;</span>
        <h2 style="color:var(--warn);">Other Devices</h2>
        <span class="cnt" id="other-count">0</span>
        <span class="cnt" id="other-online" style="color:var(--accent2);border-color:var(--accent2);">0 online</span>
      </div>
      <div class="test-grid" id="other-grid"></div>
    </div>
  </div>
  <div class="page" id="page-warnings">
    <div class="warn-toolbar">
      <div class="warn-filters">
        <button class="wf-btn active" onclick="filterW('all',this)">All</button>
        <button class="wf-btn" onclick="filterW('down',this)">&#128308; Down</button>
        <button class="wf-btn" onclick="filterW('up',this)">&#128994; Recovered</button>
        <button class="wf-btn" onclick="filterW('latency',this)">&#9203; Latency</button>
        <button class="wf-btn" onclick="filterW('speed',this)">&#8681; Speed</button>
        <button class="wf-btn" onclick="filterW('unacked',this)">Unread only</button>
      </div>
      <button class="btn btn-blue" onclick="ackAllW()" style="font-size:.7rem;padding:.32rem .75rem">Mark all read</button>
    </div>
    <div class="warn-list" id="warn-list"></div>
  </div>
  <div class="page" id="page-targets">
    <div style="margin-bottom:1rem;display:flex;align-items:center;justify-content:space-between;">
      <span style="font-size:.72rem;color:var(--dim);font-family:'Share Tech Mono'">Changes save instantly. Set intervals and thresholds per target.</span>
      <button class="btn btn-blue" onclick="runTests()" style="font-size:.7rem">&#9654; Run Now</button>
    </div>
    <div class="mgr-grid">
      <div class="mgr-card">
        <h3 class="type-ping">&#11044; Ping Targets</h3>
        <div class="target-list" id="list-ping"></div>
        <div class="add-form">
          <div class="row">
            <div class="form-group"><label>Name</label><input id="ping-name" placeholder="My Server"></div>
            <div class="form-group"><label>Host / IP</label><input id="ping-host" placeholder="192.168.1.1"></div>
          </div>
          <div class="row">
            <div class="form-group"><label>Interval (min)</label><input id="ping-interval" type="number" value="5" min="1"></div>
            <div class="form-group"><label>Warn RTT &gt; ms</label><input id="ping-warn-rtt" type="number" placeholder="100"></div>
            <div class="form-group"><label>Warn Loss &gt; %</label><input id="ping-warn-loss" type="number" placeholder="10"></div>
          </div>
          <button class="btn btn-blue" onclick="addTarget('ping')">+ Add Ping</button>
        </div>
        <div class="save-notice" id="notice-ping">&#10003; Saved</div>
      </div>
      <div class="mgr-card">
        <h3 class="type-http">&#11044; HTTP Targets</h3>
        <div class="target-list" id="list-http"></div>
        <div class="add-form">
          <div class="row">
            <div class="form-group"><label>Name</label><input id="http-name" placeholder="My Site"></div>
            <div class="form-group"><label>URL</label><input id="http-url" placeholder="https://example.com"></div>
          </div>
          <div class="row">
            <div class="form-group"><label>Interval (min)</label><input id="http-interval" type="number" value="5" min="1"></div>
            <div class="form-group"><label>Warn Latency &gt; ms</label><input id="http-warn-lat" type="number" placeholder="500"></div>
          </div>
          <button class="btn btn-blue" onclick="addTarget('http')">+ Add HTTP</button>
        </div>
        <div class="save-notice" id="notice-http">&#10003; Saved</div>
      </div>
      <div class="mgr-card">
        <h3 class="type-api">&#11044; API Targets</h3>
        <div class="target-list" id="list-api"></div>
        <div class="add-form">
          <div class="row">
            <div class="form-group"><label>Name</label><input id="api-name" placeholder="My API"></div>
            <div class="form-group"><label>Method</label>
              <select id="api-method"><option>GET</option><option>POST</option><option>PUT</option><option>PATCH</option><option>DELETE</option></select>
            </div>
          </div>
          <div class="form-group"><label>URL</label><input id="api-url" placeholder="https://api.example.com/health"></div>
          <div class="row">
            <div class="form-group"><label>Interval (min)</label><input id="api-interval" type="number" value="10" min="1"></div>
            <div class="form-group"><label>Expected Status</label><input id="api-status" type="number" placeholder="200"></div>
            <div class="form-group"><label>Warn Latency ms</label><input id="api-warn-lat" type="number" placeholder="1000"></div>
          </div>
          <div class="row">
            <div class="form-group"><label>Body Must Contain</label><input id="api-body-check" placeholder="ok"></div>
            <div class="form-group"><label>Headers JSON</label><input id="api-headers" placeholder='{"Auth":"Bearer X"}'></div>
          </div>
          <div class="form-group"><label>Request Body</label><textarea id="api-body" placeholder='{"key":"value"}'></textarea></div>
          <button class="btn btn-blue" onclick="addTarget('api')">+ Add API</button>
        </div>
        <div class="save-notice" id="notice-api">&#10003; Saved</div>
      </div>
      <div class="mgr-card">
        <h3 class="type-traceroute">&#11044; Traceroute Targets</h3>
        <div class="target-list" id="list-traceroute"></div>
        <div class="add-form">
          <div class="row">
            <div class="form-group"><label>Name</label><input id="tr-name" placeholder="My Gateway"></div>
            <div class="form-group"><label>Host / IP</label><input id="tr-host" placeholder="10.0.0.1"></div>
          </div>
          <div class="form-group"><label>Interval (min)</label><input id="tr-interval" type="number" value="30" min="1"></div>
          <button class="btn btn-blue" onclick="addTarget('traceroute')">+ Add Traceroute</button>
        </div>
        <div class="save-notice" id="notice-traceroute">&#10003; Saved</div>
      </div>
      <div class="mgr-card">
        <h3 class="type-bandwidth">&#11044; Bandwidth &amp; Thresholds</h3>
        <div class="add-form">
          <div class="form-group"><label>Test File URL</label><input id="bw-url" placeholder="https://speed.cloudflare.com/__down?bytes=5000000"></div>
          <div class="row">
            <div class="form-group"><label>BW Interval (min)</label><input id="bw-interval" type="number" value="60" min="5">
              <small style="color:var(--warn);font-size:.6rem;">⚠ Each test downloads the file — keep interval high to avoid excess data usage</small>
            </div>
            <div class="form-group"><label>Warn if Speed &lt; Mbps</label><input id="bw-warn-speed" type="number" placeholder="10"></div>
          </div>
          <button class="btn btn-blue" onclick="saveBwSettings()">Save Settings</button>
        </div>
        <div class="save-notice" id="notice-bw">&#10003; Saved</div>
      </div>
    </div>
  </div>
</main>
<div class="toast-stack" id="toast-stack"></div>
<script>
// Ingress base path — injected server-side, empty string when running locally
const BASE = '__INGRESS_BASE__';
// Wrapper so all API calls work both locally and via Nabu Casa ingress
function api(path){ return BASE + path; }

let allData={},allConfig={},allWarnings=[],warnFilter='all';

function toast(msg,type='info'){
  const stack=document.getElementById('toast-stack');
  const t=document.createElement('div');
  t.className=`toast t-${type}`;
  t.textContent=msg;
  stack.appendChild(t);
  requestAnimationFrame(()=>requestAnimationFrame(()=>t.classList.add('show')));
  setTimeout(()=>{t.classList.remove('show');setTimeout(()=>t.remove(),350);},4500);
}

function sc(s){return(s||'unknown').toLowerCase();}

function uptimeBar(pct){
  if(pct==null) return '';
  const color=pct>=99?'var(--accent2)':pct>=90?'var(--warn)':'var(--danger)';
  return `<div class="uptime-row">
    <span class="uptime-label">24H UPTIME</span>
    <div class="uptime-bar-bg"><div class="uptime-bar-fill" style="width:${pct}%;background:${color}"></div></div>
    <span class="uptime-pct" style="color:${color}">${pct}%</span>
  </div>`;
}

function failDropdown(name,history){
  const fails=history.filter(r=>['FAIL','ERROR','WARN'].includes(r.status)).slice(-20).reverse();
  if(!fails.length) return '';
  const id='fd_'+name.replace(/\W/g,'_');
  return `<div class="fail-toggle" onclick="toggleFail('${id}')">
    <span class="fail-arrow">&#9654;</span>&nbsp;${fails.length} recent issue${fails.length>1?'s':''}
  </div>
  <div class="fail-list" id="${id}">
    ${fails.map(f=>`<div class="fail-item">
      <div class="fail-ts">${new Date(f.timestamp).toLocaleString()}</div>
      <div class="fail-msg">${f.status}${f.error?' &mdash; '+f.error:''}${f.http_code?' [HTTP '+f.http_code+']':''}${f.latency_ms?' latency: '+f.latency_ms+'ms':''}${f.packet_loss_pct!=null&&f.packet_loss_pct>0?' loss: '+f.packet_loss_pct+'%':''}</div>
    </div>`).join('')}
  </div>`;
}

function toggleFail(id){
  const el=document.getElementById(id);
  if(!el) return;
  el.classList.toggle('open');
  el.previousElementSibling.classList.toggle('open');
}

const _charts={};
function makeChart(id,labels,datasets,yLabel){
  if(_charts[id]) _charts[id].destroy();
  const ctx=document.getElementById(id);
  if(!ctx) return;
  _charts[id]=new Chart(ctx,{
    type:'line',
    data:{labels,datasets:datasets.map(d=>({
      label:d.label||'',data:d.data,borderColor:d.color||'rgba(0,212,255,.7)',
      backgroundColor:d.fill||'rgba(0,212,255,.05)',borderWidth:1.5,
      pointRadius:0,tension:.3,fill:!!d.fill
    }))},
    options:{animation:false,responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:datasets.length>1,labels:{color:'rgba(200,230,245,.45)',font:{size:9},boxWidth:10}}},
      scales:{
        x:{display:false,grid:{display:false}},
        y:{display:true,grid:{color:'rgba(13,45,74,.5)',drawBorder:false},
          ticks:{color:'rgba(74,122,153,.8)',font:{size:8},maxTicksLimit:4},
          title:{display:!!yLabel,text:yLabel,color:'rgba(74,122,153,.6)',font:{size:8}}}
      }}
  });
}

function updateNocBanner(ok, total, fails, ts){
  const banner = document.getElementById('noc-banner');
  const indicator = document.getElementById('noc-indicator');
  const text = document.getElementById('noc-text');
  const detail = document.getElementById('noc-detail');
  const timeEl = document.getElementById('noc-time');
  banner.classList.remove('ok','warn','fail');
  if(fails.length === 0){
    banner.classList.add('ok');
    text.textContent = '✓ ALL SYSTEMS OPERATIONAL';
    detail.textContent = `${ok}/${total} targets healthy`;
  } else {
    const crit = fails.filter(f=>f.status==='FAIL'||f.status==='ERROR');
    if(crit.length){
      banner.classList.add('fail');
      text.textContent = `⚠ ${crit.length} TARGET${crit.length>1?'S':''} DOWN`;
      detail.textContent = crit.map(f=>f.name).join(' · ');
    } else {
      banner.classList.add('warn');
      text.textContent = `△ ${fails.length} WARNING${fails.length>1?'S':''}`;
      detail.textContent = fails.map(f=>f.name).join(' · ');
    }
  }
  if(ts) timeEl.textContent = 'Updated '+new Date(ts).toLocaleTimeString();
}

function renderDashboard(data){
  allData=data;
  const all=Object.values(data).flat();
  if(!all.length){
    document.getElementById('summary').innerHTML='';
    document.getElementById('sections').innerHTML='<div class="empty">No data yet. Click RUN NOW.</div>';
    document.getElementById('last-updated').textContent='No data yet';
    return;
  }
  const latest={};
  for(const[n,rs] of Object.entries(data)) latest[n]=rs[rs.length-1];
  const arr=Object.values(latest);
  const ts=arr[0]?.timestamp;
  if(ts) document.getElementById('last-updated').textContent='Last updated: '+new Date(ts).toLocaleString();
  const total=arr.length,ok=arr.filter(r=>r.status==='OK').length;
  const fails=arr.filter(r=>['FAIL','ERROR'].includes(r.status));
  const warns=arr.filter(r=>r.status==='WARN');
  const lats=arr.filter(r=>r.latency_ms).map(r=>r.latency_ms);
  const avgLat=lats.length?(lats.reduce((a,b)=>a+b,0)/lats.length).toFixed(0):'&mdash;';
  const bw=arr.find(r=>r.type==='bandwidth');

  updateNocBanner(ok, total, [...fails,...warns], ts);

  document.getElementById('summary').innerHTML=`
    <div class="sc">
      <div class="sc-text"><div class="lbl">Online</div><div class="val" style="color:var(--accent2)">${ok}/${total}</div><div class="sub">targets OK</div></div>
    </div>
    <div class="sc clickable" onclick="showTab('warnings',document.querySelector('[data-tab=warnings]'))">
      <div class="sc-text"><div class="lbl">Failures</div><div class="val" style="color:${fails.length?'var(--danger)':'var(--dim)'}">${fails.length}</div>
      <div class="sub">${fails.length?'tap for details':'all clear'}</div></div>
    </div>
    <div class="sc">
      <div class="sc-text"><div class="lbl">Avg Latency</div><div class="val">${avgLat}</div><div class="sub">milliseconds</div></div>
    </div>
    <div class="sc">
      <div class="sc-text"><div class="lbl">Download</div>
      <div class="val">${bw?.speed_mbps??'&mdash;'}</div>
      <div class="sub">Mbps${bw?.progress_pct!=null?' &middot; '+bw.progress_pct+'% sampled':''}</div></div>
    </div>
  `;
  const byType={ping:[],http:[],api:[],traceroute:[],bandwidth:[]};
  for(const[name,history] of Object.entries(data)){
    const last=history[history.length-1];
    if(last&&byType[last.type]) byType[last.type].push({name,last,history});
  }
  const typeLabels={ping:'&#11044; Connectivity / Ping',http:'&#11044; HTTP Endpoints',
    api:'&#11044; API Tests',traceroute:'&#11044; Traceroute',bandwidth:'&#11044; Bandwidth'};
  let html='';
  const pendingCharts=[];

  for(const[type,items] of Object.entries(byType)){
    if(!items.length) continue;
    // Smart layout class based on item count
    const countClass = items.length===1?'count-1':items.length===2?'count-2':'';
    const trClass = type==='traceroute'?' tr-grid':'';
    html+=`<div class="section"><div class="sec-hdr">
      <h2 class="type-${type}">${typeLabels[type]}</h2>
      <span class="cnt">${items.length}</span></div>
      <div class="test-grid${trClass} ${countClass}">`;

    for(const{name,last,history} of items){
      const cls=sc(last.status);
      const cid='ch_'+name.replace(/\W/g,'_');
      let metrics='',extra='',chartCfg=null;

      if(type==='ping'){
        const rw=last.rtt_avg_ms&&last.rtt_avg_ms>150;
        const lw=last.packet_loss_pct>0;
        metrics=`
          <div class="metric"><div class="ml">Loss</div><div class="mv ${lw?'warn-val':''}">${last.packet_loss_pct??'&mdash;'}%</div></div>
          <div class="metric"><div class="ml">RTT Avg</div><div class="mv ${rw?'warn-val':''}">${last.rtt_avg_ms??'&mdash;'} ms</div></div>`;
        const vals=history.map(h=>h.rtt_avg_ms).filter(v=>v!=null);
        const tl=history.filter(h=>h.rtt_avg_ms!=null).map(h=>new Date(h.timestamp).toLocaleTimeString());
        extra=`<div class="chart-wrap"><canvas id="${cid}"></canvas></div>`;
        chartCfg={id:cid,labels:tl,datasets:[{label:'RTT ms',data:vals,color:'rgba(0,212,255,.8)',fill:'rgba(0,212,255,.05)'}],y:'ms'};
      } else if(type==='http'){
        const lw=last.latency_ms&&last.latency_ms>500;
        metrics=`
          <div class="metric"><div class="ml">Code</div><div class="mv">${last.http_code??'&mdash;'}</div></div>
          <div class="metric"><div class="ml">Latency</div><div class="mv ${lw?'warn-val':''}">${last.latency_ms??'&mdash;'} ms</div></div>`;
        const vals=history.map(h=>h.latency_ms).filter(v=>v!=null);
        const tl=history.filter(h=>h.latency_ms!=null).map(h=>new Date(h.timestamp).toLocaleTimeString());
        extra=`<div class="chart-wrap"><canvas id="${cid}"></canvas></div>`;
        chartCfg={id:cid,labels:tl,datasets:[{label:'ms',data:vals,color:'rgba(0,212,255,.8)',fill:'rgba(0,212,255,.05)'}],y:'ms'};
      } else if(type==='api'){
        const lw=last.latency_ms&&last.latency_ms>1000;
        const bm=last.body_match===true?'&#10003;':last.body_match===false?'&#10007;':'&mdash;';
        metrics=`
          <div class="metric"><div class="ml">Method</div><div class="mv">${last.method??'GET'}</div></div>
          <div class="metric"><div class="ml">Code</div><div class="mv">${last.http_code??'&mdash;'}</div></div>
          <div class="metric"><div class="ml">Latency</div><div class="mv ${lw?'warn-val':''}">${last.latency_ms??'&mdash;'} ms</div></div>
          <div class="metric"><div class="ml">Body</div><div class="mv">${bm}</div></div>`;
        const vals=history.map(h=>h.latency_ms).filter(v=>v!=null);
        const tl=history.filter(h=>h.latency_ms!=null).map(h=>new Date(h.timestamp).toLocaleTimeString());
        extra=`<div class="chart-wrap"><canvas id="${cid}"></canvas></div>`;
        chartCfg={id:cid,labels:tl,datasets:[{label:'ms',data:vals,color:'rgba(179,107,255,.8)',fill:'rgba(179,107,255,.05)'}],y:'ms'};
      } else if(type==='traceroute'){
        const hops=last.hops||[];
        const maxRtt=Math.max(...hops.map(h=>h.rtt_avg_ms||0),1);
        metrics=`<div class="metric"><div class="ml">Hops</div><div class="mv">${last.hop_count??'&mdash;'}</div></div>`;
        if(hops.length){
          extra=`<div class="hops-wrap"><table class="hops-table">
            <colgroup><col style="width:26px"/><col style="width:38%"/><col style="width:65px"/><col/></colgroup>
            <tr><th>#</th><th>IP</th><th>RTT</th><th></th></tr>`+
            hops.slice(0,15).map(h=>`<tr>
              <td>${h.hop}</td><td title="${h.ip}">${h.ip}</td>
              <td>${h.rtt_avg_ms!=null?h.rtt_avg_ms+'ms':'*'}</td>
              <td><div class="hop-bar" style="width:${h.rtt_avg_ms!=null?Math.max(2,Math.min(100,h.rtt_avg_ms/maxRtt*100)).toFixed(0):2}%"></div></td>
            </tr>`).join('')+
            (hops.length>15?`<tr><td colspan="4" style="color:var(--dim);font-size:.6rem">&hellip; ${hops.length-15} more</td></tr>`:'')+
            '</table></div>';
        }
      } else {
        const sw=last.speed_mbps&&last.speed_mbps<10;
        const prog=last.progress_pct??100;
        metrics=`
          <div class="metric"><div class="ml">Speed</div><div class="mv ${sw?'warn-val':''}">${last.speed_mbps??'&mdash;'} Mbps</div></div>
          <div class="metric"><div class="ml">Duration</div><div class="mv">${last.duration_s??'&mdash;'}s</div></div>
          <div class="metric"><div class="ml">Sampled</div><div class="mv">${prog}%</div></div>`;
        const vals=history.map(h=>h.speed_mbps).filter(v=>v!=null);
        const tl=history.filter(h=>h.speed_mbps!=null).map(h=>new Date(h.timestamp).toLocaleTimeString());
        extra=`<div class="bw-prog-wrap">
          <div class="bw-prog-lbl">Download sample: ${prog}%</div>
          <div class="bw-prog-bar"><div class="bw-prog-fill" style="width:${prog}%"></div></div>
        </div><div class="chart-wrap" style="margin-top:.5rem"><canvas id="${cid}"></canvas></div>`;
        chartCfg={id:cid,labels:tl,datasets:[{label:'Mbps',data:vals,color:'rgba(255,204,0,.8)',fill:'rgba(255,204,0,.05)'}],y:'Mbps'};
      }

      const ub = type!=='traceroute' ? uptimeBar(last.uptime_pct) : '';
      const fd = failDropdown(name,history);
      const err = last.error?`<div style="font-family:'Share Tech Mono';font-size:.58rem;color:#ff6b35;margin-top:.38rem;word-break:break-all">${last.error}</div>`:'';

      html+=`<div class="tc ${cls}">
        <div class="tc-hdr">
          <div><div class="tc-name">${name}</div><div class="tc-tgt">${last.target??''}</div></div>
          <span class="badge ${cls}">${last.status}</span>
        </div>
        <div class="metrics">${metrics}</div>
        ${ub}${extra}${fd}${err}
      </div>`;
      if(chartCfg) pendingCharts.push(chartCfg);
    }
    html+='</div></div>';
  }
  document.getElementById('sections').innerHTML=html||'<div class="empty">No results yet.</div>';
  requestAnimationFrame(()=>{ for(const c of pendingCharts) makeChart(c.id,c.labels,c.datasets,c.y); });
}

function renderWarnings(){
  const list=document.getElementById('warn-list');
  let filtered=allWarnings;
  if(warnFilter==='unacked') filtered=allWarnings.filter(w=>!w.acknowledged);
  else if(warnFilter!=='all') filtered=allWarnings.filter(w=>w.type===warnFilter);
  if(!filtered.length){
    list.innerHTML=`<div class="no-warns">${warnFilter==='unacked'?'&#10003; All caught up!':'No warnings yet.'}</div>`;
    return;
  }
  const icons={down:'&#128308;',up:'&#128994;',latency:'&#9203;',loss:'&#128246;',speed:'&#8681;'};
  list.innerHTML=filtered.map((w)=>`
    <div class="warn-item type-${w.type} ${w.acknowledged?'acked':''}">
      <span class="wi-icon">${icons[w.type]||'&#9888;'}</span>
      <div class="wi-body">
        <div class="wi-msg">${w.message}</div>
        <div class="wi-meta">${new Date(w.timestamp).toLocaleString()} &nbsp;&middot;&nbsp; ${w.name}</div>
      </div>
      <button class="wi-ack" onclick="ackW(${allWarnings.indexOf(w)})">${w.acknowledged?'Read':'Mark read'}</button>
    </div>`).join('');
}

function filterW(f,el){
  warnFilter=f;
  document.querySelectorAll('.wf-btn').forEach(b=>b.classList.remove('active'));
  el.classList.add('active');
  renderWarnings();
}

let _lastToasted=null;
async function fetchData(){
  try{renderDashboard(await(await fetch(api('/data'))).json());}catch(e){console.error(e);}
}
async function fetchWarnings(){
  try{
    allWarnings=await(await fetch(api('/warnings'))).json();
    const unacked=allWarnings.filter(w=>!w.acknowledged).length;
    const badge=document.getElementById('warn-badge');
    badge.textContent=unacked;
    badge.style.display=unacked?'flex':'none';
    const mobBadge=document.getElementById('warn-badge-mob');
    if(mobBadge){mobBadge.textContent=unacked;mobBadge.style.display=unacked?'flex':'none';}
    if(unacked>0){
      const newest=allWarnings.find(w=>!w.acknowledged);
      if(newest&&JSON.stringify(newest)!==_lastToasted){
        _lastToasted=JSON.stringify(newest);
        const typeMap={down:'danger',up:'ok',latency:'warn',loss:'warn',speed:'warn'};
        toast(newest.message,typeMap[newest.type]||'warn');
      }
    }
    if(document.getElementById('page-warnings').classList.contains('active')) renderWarnings();
  }catch(e){}
}
async function fetchConfig(){
  try{renderTargetManager(await(await fetch(api('/config'))).json());}catch(e){}
}
async function ackW(idx){
  await fetch(api('/warnings/ack'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:idx})});
  await fetchWarnings();
}
async function ackAllW(){
  await fetch(api('/warnings/ack-all'),{method:'POST'});
  await fetchWarnings();
  toast('All warnings marked as read','ok');
}

function renderTargetManager(cfg){
  allConfig=cfg;
  const rl=(type,items,lf)=>{
    const el=document.getElementById('list-'+type);
    if(!el) return;
    if(!items||!items.length){el.innerHTML='<div style="font-family:\'Share Tech Mono\';font-size:.66rem;color:var(--dim);padding:.25rem 0">No targets.</div>';return;}
    el.innerHTML=items.map((t,i)=>`<div class="ti">
      <div><div class="ti-name">${t.name}</div><div class="ti-val">${lf(t)}</div></div>
      <button class="btn-del" onclick="removeTarget('${type}',${i})">&#10005;</button></div>`).join('');
  };
  rl('ping',cfg.ping||[],t=>`${t.host} · every ${t.interval||5}min · warn >${t.warn_rtt_ms||'?'}ms`);
  rl('http',cfg.http||[],t=>`${t.url} · every ${t.interval||5}min`);
  rl('api', cfg.api||[], t=>`[${t.method||'GET'}] ${t.url}`);
  rl('traceroute',cfg.traceroute||[],t=>`${t.host} · every ${t.interval||30}min`);
  const bwEl=document.getElementById('bw-url');if(bwEl)bwEl.value=cfg.bandwidth_url||'';
  const biEl=document.getElementById('bw-interval');if(biEl)biEl.value=cfg.bandwidth_interval||60;
  const wsEl=document.getElementById('bw-warn-speed');if(wsEl)wsEl.value=cfg.warn_speed_mbps||10;
}

function addTarget(type){
  const cfg=JSON.parse(JSON.stringify(allConfig));
  let t;
  if(type==='ping'){
    const name=document.getElementById('ping-name').value.trim();
    const host=document.getElementById('ping-host').value.trim();
    if(!name||!host) return toast('Name and host required','danger');
    t={name,host,interval:parseInt(document.getElementById('ping-interval').value)||5,
       warn_rtt_ms:parseInt(document.getElementById('ping-warn-rtt').value)||null,
       warn_loss_pct:parseInt(document.getElementById('ping-warn-loss').value)||null};
    ['ping-name','ping-host'].forEach(id=>document.getElementById(id).value='');
  }else if(type==='http'){
    const name=document.getElementById('http-name').value.trim();
    const url=document.getElementById('http-url').value.trim();
    if(!name||!url) return toast('Name and URL required','danger');
    t={name,url,interval:parseInt(document.getElementById('http-interval').value)||5,
       warn_latency_ms:parseInt(document.getElementById('http-warn-lat').value)||null};
    ['http-name','http-url'].forEach(id=>document.getElementById(id).value='');
  }else if(type==='api'){
    const name=document.getElementById('api-name').value.trim();
    const url=document.getElementById('api-url').value.trim();
    if(!name||!url) return toast('Name and URL required','danger');
    let headers={};
    const hr=document.getElementById('api-headers').value.trim();
    if(hr){try{headers=JSON.parse(hr);}catch{return toast('Invalid JSON headers','danger');}}
    t={name,url,method:document.getElementById('api-method').value,headers,
       body:document.getElementById('api-body').value.trim(),
       expected_status:parseInt(document.getElementById('api-status').value)||null,
       expected_body:document.getElementById('api-body-check').value.trim(),
       interval:parseInt(document.getElementById('api-interval').value)||10,
       warn_latency_ms:parseInt(document.getElementById('api-warn-lat').value)||null};
    ['api-name','api-url','api-status','api-body-check','api-headers','api-body'].forEach(id=>document.getElementById(id).value='');
  }else if(type==='traceroute'){
    const name=document.getElementById('tr-name').value.trim();
    const host=document.getElementById('tr-host').value.trim();
    if(!name||!host) return toast('Name and host required','danger');
    t={name,host,interval:parseInt(document.getElementById('tr-interval').value)||30};
    ['tr-name','tr-host'].forEach(id=>document.getElementById(id).value='');
  }
  cfg[type]=cfg[type]||[];cfg[type].push(t);
  saveConfigRemote(cfg,type);
}
function removeTarget(type,idx){
  const cfg=JSON.parse(JSON.stringify(allConfig));
  cfg[type].splice(idx,1);saveConfigRemote(cfg,type);
}
function saveBwSettings(){
  const cfg=JSON.parse(JSON.stringify(allConfig));
  cfg.bandwidth_url=document.getElementById('bw-url').value.trim()||'https://speed.cloudflare.com/__down?bytes=5000000';
  cfg.bandwidth_interval=parseInt(document.getElementById('bw-interval').value)||60;
  cfg.warn_speed_mbps=parseFloat(document.getElementById('bw-warn-speed').value)||null;
  saveConfigRemote(cfg,'bw');
}
async function saveConfigRemote(cfg,noticeId){
  try{
    const r=await fetch(api('/config'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
    if(!r.ok) throw new Error();
    allConfig=cfg;renderTargetManager(cfg);
    const n=document.getElementById('notice-'+noticeId);
    if(n){n.classList.add('show');setTimeout(()=>n.classList.remove('show'),2500);}
    toast('Saved','ok');
  }catch{toast('Failed to save','danger');}
}

// ── DEVICES ──────────────────────────────────────────────────────────────────
let allDevices = {switches:[],aps:[]};
let devFilter = 'all';

async function fetchDevices(){
  try{
    const r = await fetch(api('/devices'));
    if(r.ok) allDevices = await r.json();
    renderDevices();
  }catch(e){}
}

function getDevTag(name){
  name = name.toUpperCase();
  if(name.includes('[ZZZ]')) return 'zzz';
  if(name.includes('[YYY]')) return 'yyy';
  if(name.includes('[TB]') || name.match(/\[TB\s*\d/)) return 'tb';
  if(name.includes('KIOSK')) return 'kiosk';
  if(name.includes('[SB]') || name.endsWith('][SB]')) return 'sb';
  if(name.includes('OFFICE') || name.includes('BEAST') || name.includes('DANIEL') || name.includes('DAWID')) return 'office';
  if(name.includes('FOOD') || name.includes('ENZO') || name.includes('SWEET') || name.includes('COOKIE') || name.includes('MUSTARD') || name.includes('FRIED')) return 'food';
  return '';
}

function getTagLabel(name){
  const t = getDevTag(name);
  const map = {tb:'TB',kiosk:'KIOSK',sb:'SB',office:'OFFICE',food:'FOOD',zzz:'DECOM',yyy:'SPARE','':`HLSR`};
  return map[t] || 'HLSR';
}

function filterDev(f,btn){
  devFilter = f;
  document.querySelectorAll('.df-btn').forEach(b=>b.classList.remove('active'));
  btn?.classList.add('active');
  // Clear any active stat card
  document.querySelectorAll('.dev-stat').forEach(s=>s.classList.remove('active-filter'));
  renderDevices();
}

function filterDevStat(f, card){
  devFilter = f;
  // Highlight the clicked stat card, clear toolbar buttons
  document.querySelectorAll('.dev-stat').forEach(s=>s.classList.remove('active-filter'));
  document.querySelectorAll('.df-btn').forEach(b=>b.classList.remove('active'));
  card.classList.add('active-filter');
  renderDevices();
}

function showAddDevice(){
  const f = document.getElementById('dev-add-form');
  f.style.display = f.style.display==='none'?'block':'none';
}
function showMerakiImport(){
  const f = document.getElementById('meraki-import-form');
  f.style.display = f.style.display==='none'?'block':'none';
}

async function removeDevice(section,idx){
  allDevices[section].splice(idx,1);
  await saveDevices();
  toast('Removed','ok');
}

async function saveDevices(){
  try{
    await fetch(api('/devices'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(allDevices)});
    renderDevices();
  }catch{toast('Save failed','danger');}
}

async function pingOne(section,idx){
  const d = allDevices[section][idx];
  if(!d) return;
  toast(`Pinging ${d.ip}...`,'ok');
  try{
    const r = await fetch(api('/ping-device'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ip:d.ip})});
    const data = await r.json();
    allDevices[section][idx].online = data.online;
    allDevices[section][idx].last_checked = new Date().toISOString();
    await saveDevices();
    toast(`${d.name}: ${data.online?'ONLINE':'OFFLINE'}`,(data.online?'ok':'danger'));
  }catch{toast('Ping failed','danger');}
}

async function importMeraki(){
  const key = document.getElementById('meraki-key').value.trim();
  const org = document.getElementById('meraki-org').value.trim();
  if(!key||!org){toast('API key and Org ID required','danger');return;}
  document.getElementById('meraki-status').textContent = 'Importing...';
  try{
    const r = await fetch(api('/meraki-import'),{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({api_key:key,org_id:org})});
    const data = await r.json();
    if(data.error){toast(data.error,'danger');document.getElementById('meraki-status').textContent=data.error;return;}
    document.getElementById('meraki-status').textContent = `Imported ${data.switches} switches, ${data.aps} APs`;
    document.getElementById('meraki-import-form').style.display='none';
    fetchDevices();
    toast(`Imported ${data.switches} switches + ${data.aps} APs`,'ok');
  }catch(e){toast('Import failed','danger');document.getElementById('meraki-status').textContent='Failed';}
}

// ── ZOOM ─────────────────────────────────────────────────────────────────────
const ZOOM_CLASSES = ['zoom-xs','zoom-sm','','zoom-lg','zoom-xl'];
const ZOOM_LABELS  = ['XS','S','M','L','XL'];
let zoomDash = parseInt(localStorage.getItem('nm_zoom_dash')||'3');
let zoomDev  = parseInt(localStorage.getItem('nm_zoom_dev') ||'3');

function applyZoom(which, val){
  val = parseInt(val);
  if(which==='dash'){
    zoomDash = val;
    localStorage.setItem('nm_zoom_dash', val);
    document.getElementById('zoom-dash-lbl').textContent = ZOOM_LABELS[val-1];
  } else {
    zoomDev = val;
    localStorage.setItem('nm_zoom_dev', val);
    document.getElementById('zoom-dev-lbl').textContent = ZOOM_LABELS[val-1];
  }
  // Remove all zoom classes then apply correct one
  ZOOM_CLASSES.forEach(c=>{ if(c) document.body.classList.remove(c); });
  const activeTab = document.querySelector('.tab.active')?.dataset?.tab;
  const z = (activeTab==='devices') ? zoomDev : zoomDash;
  const cls = ZOOM_CLASSES[z-1];
  if(cls) document.body.classList.add(cls);
}

function initZoom(){
  const dashEl = document.getElementById('zoom-dash');
  const devEl  = document.getElementById('zoom-dev');
  if(dashEl){ dashEl.value = zoomDash; document.getElementById('zoom-dash-lbl').textContent = ZOOM_LABELS[zoomDash-1]; }
  if(devEl){  devEl.value  = zoomDev;  document.getElementById('zoom-dev-lbl').textContent  = ZOOM_LABELS[zoomDev-1]; }
  applyZoom('dash', zoomDash);
}

// ── SHOW TAB ─────────────────────────────────────────────────────────────────
// ── MOBILE NAV ──
function openMobNav(){
  document.getElementById('mob-nav').classList.add('open');
}
function closeMobNav(){
  document.getElementById('mob-nav').classList.remove('open');
}
function mobNavGo(tab){
  closeMobNav();
  const el = document.querySelector(`[data-tab=${tab}]`);
  showTab(tab, el);
  // Update mob nav active state
  document.querySelectorAll('.mob-nav-item').forEach(i=>{
    i.classList.toggle('active', i.dataset.tab===tab);
  });
}

// ── MAP TYPE VISIBILITY ──
const hiddenTypes = new Set();
function toggleMapType(type, visible){
  if(visible) hiddenTypes.delete(type);
  else hiddenTypes.add(type);
  applyMapTypeVisibility();
}
function applyMapTypeVisibility(){
  document.querySelectorAll('.map-node').forEach(node=>{
    const isOffline = node.classList.contains('offline');
    // Check offline filter
    if(hiddenTypes.has('offline') && isOffline){
      node.style.opacity='0.05'; node.style.pointerEvents='none'; return;
    }
    // Check type filter
    let hidden = false;
    for(const t of hiddenTypes){
      if(t!=='offline' && node.classList.contains('ntype-'+t)){ hidden=true; break; }
    }
    node.style.opacity = hidden ? '0.05' : '';
    node.style.pointerEvents = hidden ? 'none' : '';
  });
}

function showTab(name, el){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('page-'+name)?.classList.add('active');
  el?.classList.add('active');
  // Zoom bars
  document.getElementById('zoom-bar-dashboard').style.display = name==='dashboard'?'flex':'none';
  document.getElementById('zoom-bar-devices').style.display   = name==='devices'  ?'flex':'none';
  document.getElementById('zoom-bar-map').style.display       = name==='map'      ?'flex':'none';
  // Apply correct zoom for this tab
  ZOOM_CLASSES.forEach(c=>{ if(c) document.body.classList.remove(c); });
  const z = (name==='devices') ? zoomDev : zoomDash;
  const cls = ZOOM_CLASSES[z-1];
  if(cls) document.body.classList.add(cls);
  if(name==='warnings') renderWarnings();
  if(name==='map'){
    // Full refresh every time map tab is selected
    fetchDevices().then(()=>{
      renderMapSidebar(); renderMapNodes(); renderMapLines();
    });
    // Also reload map positions in case another session changed them
    fetchMapPositions();
  }
}

// ── PROGRESS POLLING ─────────────────────────────────────────────────────────
let _runPolling = null;
let _pingPolling = null;

function startRunProgress(){
  document.getElementById('run-progress-box').classList.add('visible');
  _runPolling = setInterval(async()=>{
    try{
      const p = await(await fetch(api('/run-progress'))).json();
      document.getElementById('run-bar').style.width = p.pct+'%';
      document.getElementById('run-pct').textContent = p.pct+'%';
      document.getElementById('run-stage').textContent = p.stage||'';
      if(!p.running){
        clearInterval(_runPolling);_runPolling=null;
        setTimeout(()=>document.getElementById('run-progress-box').classList.remove('visible'),2000);
      }
    }catch{}
  },400);
}

function startPingProgress(){
  document.getElementById('ping-progress-box').classList.add('visible');
  _pingPolling = setInterval(async()=>{
    try{
      const p = await(await fetch(api('/ping-progress'))).json();
      document.getElementById('ping-bar').style.width = p.pct+'%';
      document.getElementById('ping-pct').textContent = `${p.done}/${p.total}`;
      document.getElementById('ping-stage').textContent = p.current ? `Pinging: ${p.current}` : 'Starting...';
      if(!p.running && p.done>0){
        clearInterval(_pingPolling);_pingPolling=null;
        setTimeout(()=>{
          document.getElementById('ping-progress-box').classList.remove('visible');
          fetchDevices();
        },2000);
      }
    }catch{}
  },500);
}

async function runTests(){
  const btn=document.getElementById('run-btn');
  btn.textContent='Running...';btn.disabled=true;
  startRunProgress();
  try{
    await fetch(api('/run'),{method:'POST'});
    toast('Tests complete','ok');
    await Promise.all([fetchData(),fetchWarnings()]);
  }catch{toast('Run failed','danger');}
  finally{btn.innerHTML='&#9654; RUN NOW';btn.disabled=false;}
}

async function pingAllDevices(){
  toast('Starting ping sweep...','ok');
  startPingProgress();
  try{
    await fetch(api('/ping-all'),{method:'POST'});
  }catch{toast('Failed','danger');}
}

// ── ADD DEVICE (updated for free-text type) ──────────────────────────────────
async function addDevice(){
  const section = document.getElementById('new-dev-section').value;
  const d = {
    name:     document.getElementById('new-dev-name').value.trim(),
    ip:       document.getElementById('new-dev-ip').value.trim(),
    model:    document.getElementById('new-dev-model').value.trim(),
    type:     document.getElementById('new-dev-type').value.trim(),
    location: document.getElementById('new-dev-location').value.trim(),
    notes:    document.getElementById('new-dev-notes').value.trim(),
    online:   null
  };
  if(!d.name||!d.ip){toast('Name and IP required','danger');return;}
  allDevices[section] = allDevices[section]||[];
  allDevices[section].push(d);
  await saveDevices();
  ['new-dev-name','new-dev-ip','new-dev-model','new-dev-type','new-dev-location','new-dev-notes']
    .forEach(id=>document.getElementById(id).value='');
  document.getElementById('dev-add-form').style.display='none';
  toast('Device added','ok');
}

// ── RENDER DEVICES (updated for other section) ────────────────────────────────
function renderDevices(){
  const search = (document.getElementById('dev-search')?.value||'').toLowerCase();
  const sections = ['switches','aps','other'];
  const grids    = {switches:'sw-grid', aps:'ap-grid', other:'other-grid'};
  const counts   = {switches:'sw-count',aps:'ap-count',other:'other-count'};
  const onlines  = {switches:'sw-online',aps:'ap-online',other:'other-online'};

  for(const section of sections){
    const grid    = document.getElementById(grids[section]);
    const countEl = document.getElementById(counts[section]);
    const onlineEl= document.getElementById(onlines[section]);
    if(!grid) continue;
    const items = allDevices[section]||[];

    let filtered = items.filter(d=>{
      const name = (d.name||'').toLowerCase();
      const ip   = (d.ip||'').toLowerCase();
      const loc  = (d.location||'').toLowerCase();
      const typ  = (d.type||'').toLowerCase();
      const matchSearch = !search || name.includes(search)||ip.includes(search)||loc.includes(search)||typ.includes(search);
      let matchFilter = true;
      if(devFilter==='online')           matchFilter = d.online===true;
      else if(devFilter==='offline')     matchFilter = d.online===false;
      else if(devFilter==='switch')      matchFilter = section==='switches';
      else if(devFilter==='ap')          matchFilter = section==='aps';
      else if(devFilter==='switch-online')  matchFilter = section==='switches' && d.online===true;
      else if(devFilter==='switch-offline') matchFilter = section==='switches' && d.online===false;
      else if(devFilter==='ap-online')      matchFilter = section==='aps'      && d.online===true;
      else if(devFilter==='ap-offline')     matchFilter = section==='aps'      && d.online===false;
      return matchSearch && matchFilter;
    });

    const onlineCount = filtered.filter(d=>d.online===true).length;
    if(countEl)  countEl.textContent  = filtered.length;
    if(onlineEl) onlineEl.textContent = `${onlineCount} online`;

    // Show/hide other section
    if(section==='other'){
      const sec = document.getElementById('other-section');
      if(sec) sec.style.display = (allDevices.other||[]).length ? 'block' : 'none';
    }

    grid.innerHTML = filtered.map((d)=>{
      const status = d.online===true?'online':d.online===false?'offline':'unknown';
      const tag = getDevTag(d.name||'');
      const tagLabel = getTagLabel(d.name||'');
      const realIdx = items.indexOf(d);
      const typeLabel = d.type ? `<span class="dev-tag" style="background:rgba(255,204,0,.08);color:var(--warn);border-color:var(--warn);">${d.type.toUpperCase()}</span>` : '';
      return `<div class="dev-card ${status}">
        <div class="dev-card-hdr">
          <div class="dev-name">${d.name||'Unnamed Device'}</div>
          <div class="dev-badge ${status}">${status==='online'?'&#9646; ONLINE':status==='offline'?'&#9646; OFFLINE':'&#9646; UNKNOWN'}</div>
        </div>
        <div style="margin-bottom:.4rem;">
          ${tag?`<span class="dev-tag ${tag}">${tagLabel}</span>`:''}
          ${typeLabel}
        </div>
        <div class="dev-meta">
          <span class="lbl">IP</span><span class="val">${d.ip||'—'}</span>
          <span class="lbl">MODEL</span><span class="val">${d.model||'—'}</span>
          ${d.location?`<span class="lbl">LOC</span><span class="val">${d.location}</span>`:''}
          ${d.last_checked?`<span class="lbl">CHECKED</span><span class="val" style="font-size:.6rem;">${new Date(d.last_checked).toLocaleTimeString()}</span>`:''}
        </div>
        ${d.notes?`<div class="dev-notes">${d.notes}</div>`:''}
        <div class="dev-actions">
          <button class="dev-act" onclick="pingOne('${section}',${realIdx})">&#9654; Ping</button>
          <button class="dev-act del" onclick="removeDevice('${section}',${realIdx})">&#10005; Remove</button>
        </div>
      </div>`;
    }).join('');
  }

  // Stats bar
  const allSw  = allDevices.switches||[];
  const allAp  = allDevices.aps||[];
  const allOth = allDevices.other||[];
  const swOn  = allSw.filter(d=>d.online===true).length;
  const apOn  = allAp.filter(d=>d.online===true).length;
  const swOff = allSw.filter(d=>d.online===false).length;
  const apOff = allAp.filter(d=>d.online===false).length;
  document.getElementById('dev-stats').innerHTML = `
    <div class="dev-stat" onclick="filterDevStat('switch-online',this)"><span class="online-num">${swOn}</span>Switches Online</div>
    <div class="dev-stat" onclick="filterDevStat('switch-offline',this)"><span class="offline-num">${swOff}</span>Switches Offline</div>
    <div class="dev-stat" onclick="filterDevStat('ap-online',this)"><span class="online-num">${apOn}</span>APs Online</div>
    <div class="dev-stat" onclick="filterDevStat('ap-offline',this)"><span class="offline-num">${apOff}</span>APs Offline</div>
    <div class="dev-stat" onclick="filterDevStat('all',this)"><span style="color:var(--accent);">${allSw.length+allAp.length+allOth.length}</span>Total Devices</div>`;
}

fetchDevices();
setInterval(fetchDevices,30000);

// ── LIVE MAP REFRESH ──
// Polls /devices every 5s when map tab is active, re-renders nodes+lines immediately
async function liveMapRefresh(){
  if(document.getElementById('page-map').classList.contains('active')){
    try{
      const r = await fetch(api('/devices'));
      if(r.ok){
        const fresh = await r.json();
        const changed = JSON.stringify(fresh) !== JSON.stringify(allDevices);
        if(changed){
          allDevices = fresh;
          renderMapNodes();
          renderMapLines();
          renderMapSidebar();
        }
        // Always update the live timestamp
        const t = new Date();
        const ts = t.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
        const el = document.getElementById('map-live-time');
        if(el) el.textContent = ts;
        // Flash the dot green on update
        const dot = document.getElementById('map-live-dot');
        if(dot && changed){
          dot.style.background = '#ffffff';
          setTimeout(()=>{ dot.style.background='var(--accent2)'; }, 300);
        }
      }
    }catch{
      const dot = document.getElementById('map-live-dot');
      if(dot) dot.style.background='var(--danger)';
    }
  }
}
setInterval(liveMapRefresh, 5000);
initZoom();
fetchData();fetchWarnings();fetchConfig();
setInterval(fetchData,15000);setInterval(fetchWarnings,8000);

// ── MAP ─────────────────────────────────────────────────────────────────────
// Fixed world size: nodes stored in px on a 2000×900 logical canvas
const MAP_W = 8000, MAP_H = 4000;
let mapPositions = {};   // { key: {x, y} }  — px on logical canvas
let mapConnections = []; // [ [keyA, keyB], ... ]
let mapEditMode  = false;
let mapBgMode    = 'image';
let mapDevFilter = 'all';
let activeTTNode = null;
let connectSrc   = null; // key of first node clicked in connect mode
let mapConnectMode = false;

// ── INIT ──
function initMap(){
  const world = document.getElementById('map-world');
  const bg    = document.getElementById('map-bg');
  // Set world size based on image natural size or fixed canvas
  bg.onload = ()=>{
    world.style.width  = bg.naturalWidth  + 'px';
    world.style.height = bg.naturalHeight + 'px';
    resizeMapSvgAndGrid();
    renderMapNodes(); renderMapLines();
  };
  bg.onerror = ()=>{
    // No image — use blank canvas mode automatically
    setBlankCanvas();
  };
  if(bg.complete && bg.naturalWidth) bg.onload();
  resizeMapSvgAndGrid();
  initMapDrop();
}

function resizeMapSvgAndGrid(){
  const world = document.getElementById('map-world');
  const svg   = document.getElementById('map-lines');
  const grid  = document.getElementById('map-grid-canvas');
  const w = world.offsetWidth  || MAP_W;
  const h = world.offsetHeight || MAP_H;
  svg.setAttribute('width',  w);
  svg.setAttribute('height', h);
  svg.style.width  = w+'px';
  svg.style.height = h+'px';
  grid.width  = w;
  grid.height = h;
  drawGrid(grid, w, h);
}

function drawGrid(canvas, w, h){
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle = 'rgba(0,212,255,0.07)';
  ctx.lineWidth = 1;
  const step = 60;
  for(let x=0;x<=w;x+=step){ ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,h);ctx.stroke(); }
  for(let y=0;y<=h;y+=step){ ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke(); }
}

function setBlankCanvas(){
  const world = document.getElementById('map-world');
  const btn   = document.getElementById('map-bg-toggle');
  world.classList.add('blank');
  world.style.width  = MAP_W+'px';
  world.style.height = MAP_H+'px';
  mapBgMode = 'blank';
  btn.textContent = '\uD83D\uDEF0 Aerial View';
  btn.style.cssText = 'border:1px solid var(--accent);color:var(--accent);';
  resizeMapSvgAndGrid();
  renderMapNodes(); renderMapLines();
}

function toggleMapBackground(){
  const world = document.getElementById('map-world');
  const bg    = document.getElementById('map-bg');
  const btn   = document.getElementById('map-bg-toggle');
  mapBgMode = mapBgMode==='image' ? 'blank' : 'image';
  if(mapBgMode==='blank'){
    world.classList.add('blank');
    world.style.width  = MAP_W+'px';
    world.style.height = MAP_H+'px';
    btn.textContent = '\uD83D\uDEF0 Aerial View';
    btn.style.cssText = 'border:1px solid var(--accent);color:var(--accent);';
  } else {
    world.classList.remove('blank');
    if(bg.naturalWidth){
      world.style.width  = bg.naturalWidth+'px';
      world.style.height = bg.naturalHeight+'px';
    }
    btn.textContent = '\u2B1C Blank Canvas';
    btn.style.cssText = 'border:1px solid var(--border);';
  }
  resizeMapSvgAndGrid();
  renderMapNodes(); renderMapLines();
}

// ── ZOOM ──
let mapScale = 1.0;
function setMapZoom(val){
  mapScale = parseInt(val)/100;
  document.getElementById('map-zoom-lbl').textContent = val+'%';
  document.getElementById('map-world').style.transform = `scale(${mapScale})`;
  document.getElementById('map-world').style.transformOrigin = 'top left';
}

// ── STORAGE ──
async function fetchMapPositions(){
  try{
    const r = await fetch(api('/map-positions'));
    if(r.ok){
      const d = await r.json();
      mapPositions   = d.positions  || {};
      mapConnections = d.connections|| [];
    }
  }catch{}
  renderMapNodes(); renderMapLines();
}

async function saveMapData(){
  try{
    await fetch(api('/map-positions'),{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({positions:mapPositions, connections:mapConnections})});
  }catch{}
}

// ── EDIT / CONNECT MODES ──
function toggleMapEdit(){
  mapEditMode = !mapEditMode;
  const pencil = document.getElementById('map-pencil-btn');
  const menu   = document.getElementById('map-edit-menu');
  const hint   = document.getElementById('map-edit-hint');
  if(mapEditMode){
    pencil.style.cssText='font-size:1rem;padding:.25rem .6rem;border:1px solid var(--accent2)!important;color:var(--accent2);background:rgba(0,255,157,.08);';
    menu.style.display='block';
    hint.textContent='Edit mode — drag to place, pencil to exit';
  } else {
    pencil.style.cssText='font-size:1rem;padding:.25rem .6rem;border:1px solid var(--border);';
    menu.style.display='none';
    hint.textContent='View mode';
    if(mapConnectMode) toggleConnectMode();
  }
  renderMapSidebar(); renderMapNodes();
}

function toggleConnectMode(){
  mapConnectMode = !mapConnectMode;
  connectSrc = null;
  const btn  = document.getElementById('map-connect-btn');
  const hint = document.getElementById('map-edit-hint');
  if(mapConnectMode){
    btn.classList.add('active');
    hint.textContent='Connect: click first node then second — click a line to delete it';
  } else {
    btn.classList.remove('active');
    hint.textContent='Edit mode — drag to place, pencil to exit';
  }
  renderMapNodes();
}

// ── SIDEBAR COLLAPSE ──
// ── SIDEBAR TAB SWITCHING ──
let mapSidebarTab = 'devices';
function switchMapSidebarTab(tab){
  mapSidebarTab = tab;
  document.getElementById('msb-devices-panel').style.display = tab==='devices' ? 'flex' : 'none';
  document.getElementById('msb-devices-panel').style.flexDirection = 'column';
  document.getElementById('msb-maps-panel').style.display   = tab==='maps'    ? 'flex' : 'none';
  document.getElementById('msb-maps-panel').style.flexDirection = 'column';
  const dt = document.getElementById('msb-tab-devices');
  const mt = document.getElementById('msb-tab-maps');
  dt.style.color = tab==='devices'?'var(--accent)':'var(--dim)';
  dt.style.borderBottomColor = tab==='devices'?'var(--accent)':'transparent';
  mt.style.color = tab==='maps'?'var(--accent)':'var(--dim)';
  mt.style.borderBottomColor = tab==='maps'?'var(--accent)':'transparent';
  if(tab==='maps') renderSavedMaps();
}

// ── SAVED MAPS ──
let savedMaps = [];
let activeMapId = null;

async function fetchSavedMaps(){
  try{
    const r = await fetch(api('/saved-maps'));
    if(r.ok) savedMaps = await r.json();
  }catch{}
}

async function saveCurrentMap(){
  const nameInput = document.getElementById('map-name-input');
  const name = nameInput.value.trim();
  if(!name){ toast('Enter a name for this map','warn'); return; }
  const now = new Date().toISOString();
  const existing = savedMaps.findIndex(m=>m.id===activeMapId);
  const mapData = {
    id: activeMapId || ('map_'+Date.now()),
    name,
    saved: now,
    positions: mapPositions,
    connections: mapConnections
  };
  if(existing>=0) savedMaps[existing] = mapData;
  else savedMaps.push(mapData);
  activeMapId = mapData.id;
  try{
    await fetch(api('/saved-maps'),{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(savedMaps)});
    toast(`Map "${name}" saved`,'ok');
    nameInput.value='';
    renderSavedMaps();
  }catch{ toast('Save failed','danger'); }
}

function loadSavedMap(id){
  const m = savedMaps.find(m=>m.id===id);
  if(!m) return;
  mapPositions   = m.positions   || {};
  mapConnections = m.connections || [];
  activeMapId = id;
  saveMapData();
  renderMapNodes(); renderMapLines(); renderMapSidebar(); renderSavedMaps();
  toast(`Loaded "${m.name}"`,'ok');
}

async function deleteSavedMap(id, e){
  e.stopPropagation();
  savedMaps = savedMaps.filter(m=>m.id!==id);
  if(activeMapId===id) activeMapId=null;
  await fetch(api('/saved-maps'),{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(savedMaps)});
  renderSavedMaps();
  toast('Map deleted','ok');
}

function renderSavedMaps(){
  const list = document.getElementById('map-saved-list');
  if(!list) return;
  if(!savedMaps.length){
    list.innerHTML='<div style="padding:.8rem;font-size:.65rem;color:var(--dim);text-align:center;">No saved maps yet.<br>Name and save the current layout above.</div>';
    return;
  }
  list.innerHTML = savedMaps.map(m=>`
    <div class="map-saved-item ${activeMapId===m.id?'active-map':''}" onclick="loadSavedMap('${m.id}')">
      <div class="map-saved-name">${m.name} ${activeMapId===m.id?'&#10003;':''}</div>
      <div class="map-saved-meta">${Object.keys(m.positions||{}).length} nodes · ${new Date(m.saved).toLocaleDateString()}</div>
      <div class="map-saved-actions">
        <button class="btn btn-blue" style="font-size:.55rem;padding:.15rem .4rem;"
          onclick="loadSavedMap('${m.id}')">Load</button>
        <button class="btn" style="font-size:.55rem;padding:.15rem .4rem;border:1px solid var(--danger);color:var(--danger);"
          onclick="deleteSavedMap('${m.id}',event)">Delete</button>
      </div>
    </div>`).join('');
}

let sidebarCollapsed = false;
function toggleMapSidebar(){
  sidebarCollapsed = !sidebarCollapsed;
  const sb  = document.getElementById('map-sidebar');
  const tab = document.getElementById('map-sidebar-tab');
  if(sidebarCollapsed){
    sb.classList.add('collapsed');
    tab.classList.add('visible');
  } else {
    sb.classList.remove('collapsed');
    tab.classList.remove('visible');
  }
}

// ── PING MAPPED NODES ──
async function pingMappedNodes(){
  const keys = Object.keys(mapPositions);
  if(!keys.length){ toast('No devices on map to ping','warn'); return; }
  const btn = document.getElementById('map-ping-btn');
  btn.classList.add('pinging');
  btn.textContent='\u23F3 Pinging...';
  btn.disabled=true;
  let done=0;
  for(const key of keys){
    const d=getDeviceByKey(key);
    if(!d?.ip){ done++; continue; }
    try{
      const r=await fetch(api('/ping-device'),{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ip:d.ip})});
      if(r.ok){
        const res=await r.json();
        const [section,idx]=key.split(':');
        if(allDevices[section]?.[parseInt(idx)])
          allDevices[section][parseInt(idx)].online=res.online;
      }
    }catch{}
    done++;
    btn.textContent=`\u23F3 ${done}/${keys.length}`;
    renderMapNodes(); renderMapLines();
    await new Promise(r=>setTimeout(r,120));
  }
  btn.classList.remove('pinging');
  btn.textContent='\u25BA Ping Map';
  btn.disabled=false;
  renderMapNodes(); renderMapLines();
  toast(`Pinged ${done} map devices`,'ok');
}

// ── SIDEBAR ──
function setMapFilterActive(btn){
  document.querySelectorAll('.map-sidebar-filters .df-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
}

function getDeviceByKey(key){
  if(!key) return null;
  const [section,idx] = key.split(':');
  return (allDevices[section]||[])[parseInt(idx)]||null;
}

function getNodeStatus(key){
  const d = getDeviceByKey(key); if(!d) return 'unknown';
  return d.online===true?'online':d.online===false?'offline':'unknown';
}

function renderMapSidebar(){
  const search=(document.getElementById('map-dev-search')?.value||'').toLowerCase();
  const list=document.getElementById('map-sidebar-list');
  if(!list) return;
  const mappedKeys=new Set(Object.keys(mapPositions));
  let items=[];
  for(const section of ['switches','aps']){
    (allDevices[section]||[]).forEach((d,i)=>{
      const key=`${section}:${i}`;
      const isMapped=mappedKeys.has(key);
      const matchSearch=!search||(d.name||'').toLowerCase().includes(search)||(d.ip||'').includes(search);
      let matchFilter=true;
      if(mapDevFilter==='switches') matchFilter=section==='switches';
      else if(mapDevFilter==='aps') matchFilter=section==='aps';
      else if(mapDevFilter==='unmapped') matchFilter=!isMapped;
      if(matchSearch&&matchFilter) items.push({key,d,isMapped,status:getNodeStatus(key)});
    });
  }
  list.innerHTML=items.map(({key,d,isMapped,status})=>`
    <div class="map-dev-item ${isMapped?'mapped':''}"
         draggable="${mapEditMode}"
         id="sidebar-${key.replace(':','_')}"
         ondragstart="onSidebarDragStart(event,'${key}')"
         ondragend="document.getElementById('sidebar-${key.replace(':','_')}')?.classList.remove('dragging')">
      <div class="map-dev-dot ${status}"></div>
      <div style="min-width:0;">
        <div class="map-dev-name">${d.name||'Unnamed'} ${isMapped?'&#10003;':''}</div>
        <div class="map-dev-ip">${d.ip||'—'}</div>
      </div>
    </div>`).join('');
}

// ── RENDER NODES ──
// ── NODE ICON HELPERS ──
function getNodeType(d, section){
  const name = (d.name||'').toUpperCase();
  if(section==='aps') return 'ap';
  if(name.includes('[TB]'))     return 'tb';
  if(name.includes('[KIOSK]'))  return 'kiosk';
  if(name.includes('[OFFICE]')) return 'office';
  if(name.includes('[SB]'))     return 'sb';
  return 'switch'; // default for switches
}

// SVG icons — all 22×22 viewBox, colored via currentColor
const NODE_ICONS = {
  // Switch box — rack unit with ports
  switch: `<svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="2" y="5" width="18" height="4" rx="1" stroke="currentColor" stroke-width="1.5"/>
    <rect x="2" y="11" width="18" height="4" rx="1" stroke="currentColor" stroke-width="1.5"/>
    <circle cx="17" cy="7" r="1.2" fill="currentColor"/>
    <circle cx="17" cy="13" r="1.2" fill="currentColor"/>
    <line x1="5" y1="7" x2="12" y2="7" stroke="currentColor" stroke-width="1.2"/>
    <line x1="5" y1="13" x2="12" y2="13" stroke="currentColor" stroke-width="1.2"/>
  </svg>`,
  // Ticket Box — booth with window/counter, "TB" label feel
  tb: `<svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="2" y="4" width="18" height="15" rx="1.5" stroke="currentColor" stroke-width="1.5"/>
    <rect x="5" y="7" width="12" height="6" rx="1" stroke="currentColor" stroke-width="1.2"/>
    <line x1="2" y1="13" x2="20" y2="13" stroke="currentColor" stroke-width="1"/>
    <line x1="8" y1="13" x2="8" y2="19" stroke="currentColor" stroke-width="1"/>
    <line x1="14" y1="13" x2="14" y2="19" stroke="currentColor" stroke-width="1"/>
    <line x1="7" y1="10" x2="15" y2="10" stroke="currentColor" stroke-width="1" stroke-dasharray="2,1.5"/>
    <line x1="11" y1="4" x2="11" y2="2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  </svg>`,
  // Kiosk — taller narrow booth with "K" sign on top
  kiosk: `<svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="5" y="5" width="12" height="15" rx="1.5" stroke="currentColor" stroke-width="1.5"/>
    <rect x="7" y="8" width="8" height="5" rx="1" stroke="currentColor" stroke-width="1.2"/>
    <line x1="5" y1="14" x2="17" y2="14" stroke="currentColor" stroke-width="1"/>
    <line x1="9" y1="14" x2="9" y2="20" stroke="currentColor" stroke-width="1"/>
    <line x1="13" y1="14" x2="13" y2="20" stroke="currentColor" stroke-width="1"/>
    <rect x="8" y="2" width="6" height="4" rx="1" stroke="currentColor" stroke-width="1.2"/>
    <line x1="10" y1="3.5" x2="10" y2="5.5" stroke="currentColor" stroke-width="1"/>
    <polyline points="10,4.5 12,3.5 12,5.5" stroke="currentColor" stroke-width="1" stroke-linejoin="round" fill="none"/>
  </svg>`,
  // Access Point — wifi arcs with dot below
  ap: `<svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M3.5 8.5 C3.5 4.9 6.9 2 11 2 C15.1 2 18.5 4.9 18.5 8.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>
    <path d="M6.5 11 C6.5 8.8 8.6 7 11 7 C13.4 7 15.5 8.8 15.5 11" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>
    <path d="M9.2 13.2 C9.2 12.1 10.0 11.2 11 11.2 C12.0 11.2 12.8 12.1 12.8 13.2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" fill="none"/>
    <circle cx="11" cy="16" r="1.8" fill="currentColor"/>
  </svg>`,
  // Office — building with windows
  office: `<svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="3" y="5" width="16" height="14" rx="1" stroke="currentColor" stroke-width="1.5"/>
    <line x1="3" y1="9" x2="19" y2="9" stroke="currentColor" stroke-width="1"/>
    <rect x="6" y="12" width="3" height="3" rx=".5" stroke="currentColor" stroke-width="1"/>
    <rect x="13" y="12" width="3" height="3" rx=".5" stroke="currentColor" stroke-width="1"/>
    <rect x="9" y="14" width="4" height="5" rx=".5" stroke="currentColor" stroke-width="1"/>
    <line x1="11" y1="2" x2="11" y2="5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  </svg>`,
  // Switch box — crate with lid
  sb: `<svg width="22" height="22" viewBox="0 0 22 22" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect x="3" y="7" width="16" height="12" rx="1.5" stroke="currentColor" stroke-width="1.5"/>
    <polyline points="3,7 11,2 19,7" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" fill="none"/>
    <line x1="11" y1="7" x2="11" y2="19" stroke="currentColor" stroke-width="1"/>
    <line x1="3" y1="13" x2="19" y2="13" stroke="currentColor" stroke-width="1"/>
  </svg>`
};

const NODE_COLORS = {
  switch: '#00d4ff',  // cyan
  tb:     '#ffd700',  // gold
  kiosk:  '#c084fc',  // purple
  ap:     '#00ff9d',  // green
  office: '#4ade80',  // light green
  sb:     '#fb923c',  // orange
};

function getNodeIcon(d, section, status){
  const type  = getNodeType(d, section);
  const base  = NODE_COLORS[type] || '#00d4ff';
  const color = status==='offline' ? '#ff2d55' : status==='unknown' ? '#4a7a99' : base;
  const svg   = NODE_ICONS[type] || NODE_ICONS.switch;
  return { svg, color, type };
}

function renderMapNodes(){
  const container=document.getElementById('map-nodes');
  if(!container) return;
  container.innerHTML='';
  for(const [key,pos] of Object.entries(mapPositions)){
    const d=getDeviceByKey(key); if(!d) continue;
    const [section] = key.split(':');
    const status=getNodeStatus(key);
    const isConnSrc = connectSrc===key;
    const {svg, color, type} = getNodeIcon(d, section, status);

    const node=document.createElement('div');
    node.className=`map-node ${status} ntype-${type}${isConnSrc?' connect-src':''}`;
    node.id=`mapnode-${key.replace(':','_')}`;
    node.style.left=pos.x+'px';
    node.style.top =pos.y+'px';
    node.innerHTML=`
      <div class="map-node-icon" style="color:${color};${status!=='offline'?`filter:drop-shadow(0 0 5px ${color}88);`:'filter:drop-shadow(0 0 6px #ff2d55);'}">${svg}</div>
      <div class="map-node-label" style="color:${color};">${d.name||'?'}</div>`;

    node.addEventListener('mouseenter',e=>{ if(!mapConnectMode) showMapTooltip(e,key,false); });
    node.addEventListener('mouseleave',()=>{ if(activeTTNode!==key) hideMapTooltip(); });
    node.addEventListener('click',e=>{
      e.stopPropagation();
      if(mapConnectMode){ handleConnectClick(key); return; }
      showMapTooltip(e,key,true);
    });
    if(mapEditMode && !mapConnectMode){
      node.draggable=true;
      node.addEventListener('dragstart',e=>{
        e.dataTransfer.setData('nodeKey',key);
        e.dataTransfer.setData('type','node');
      });
    }
    container.appendChild(node);
  }
  applyMapTypeVisibility();
}
function renderMapLines(){
  const svg=document.getElementById('map-lines');
  if(!svg) return;
  svg.innerHTML='';
  for(const [ka,kb] of mapConnections){
    const pa=mapPositions[ka], pb=mapPositions[kb];
    if(!pa||!pb) continue;
    const statusA=getNodeStatus(ka), statusB=getNodeStatus(kb);
    const bothOnline = statusA==='online' && statusB==='online';
    const anyOffline = statusA==='offline'|| statusB==='offline';
    const color = anyOffline?'rgba(255,45,85,.7)':bothOnline?'rgba(0,255,157,.5)':'rgba(100,140,160,.4)';
    const line=document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1',pa.x); line.setAttribute('y1',pa.y);
    line.setAttribute('x2',pb.x); line.setAttribute('y2',pb.y);
    line.setAttribute('stroke',color);
    line.setAttribute('stroke-width','2');
    line.setAttribute('stroke-dasharray', anyOffline?'6,4':'none');
    // Delete line on click in edit mode
    const lineKey=`${ka}|${kb}`;
    if(mapEditMode){
      line.style.cursor='pointer'; line.style.pointerEvents='stroke';
      line.setAttribute('stroke-width','6');
      line.setAttribute('stroke','rgba(255,255,255,.0)');
      const visLine=document.createElementNS('http://www.w3.org/2000/svg','line');
      visLine.setAttribute('x1',pa.x);visLine.setAttribute('y1',pa.y);
      visLine.setAttribute('x2',pb.x);visLine.setAttribute('y2',pb.y);
      visLine.setAttribute('stroke',color);visLine.setAttribute('stroke-width','2');
      if(anyOffline) visLine.setAttribute('stroke-dasharray','6,4');
      svg.appendChild(visLine);
      line.addEventListener('click',e=>{ e.stopPropagation(); removeConnection(ka,kb); });
    }
    svg.appendChild(line);
  }
}

// ── CONNECTION LOGIC ──
function handleConnectClick(key){
  if(!connectSrc){
    connectSrc=key;
    renderMapNodes();
    document.getElementById('map-edit-hint').textContent=`Now click the second device to connect to "${getDeviceByKey(key)?.name||key}"`;
  } else if(connectSrc===key){
    connectSrc=null;
    renderMapNodes();
    document.getElementById('map-edit-hint').textContent='Connect mode: click first node, then second';
  } else {
    // Add connection if not already exists
    const exists=mapConnections.some(([a,b])=>(a===connectSrc&&b===key)||(a===key&&b===connectSrc));
    if(!exists){ mapConnections.push([connectSrc,key]); saveMapData(); }
    connectSrc=null;
    renderMapNodes(); renderMapLines();
    document.getElementById('map-edit-hint').textContent='Connected! Click another node or click again to continue';
  }
}

function removeConnection(ka,kb){
  mapConnections=mapConnections.filter(([a,b])=>!((a===ka&&b===kb)||(a===kb&&b===ka)));
  saveMapData(); renderMapLines();
  toast('Connection removed','ok');
}

// ── TOOLTIP ──
function showMapTooltip(e,key,pin){
  const d=getDeviceByKey(key); if(!d) return;
  const status=getNodeStatus(key);
  const tt=document.getElementById('map-tooltip');
  document.getElementById('map-tt-name').textContent=d.name||'Unnamed';
  document.getElementById('map-tt-ip').textContent=d.ip||'—';
  document.getElementById('map-tt-status').textContent=status.toUpperCase();
  document.getElementById('map-tt-status').style.color=status==='online'?'var(--accent2)':status==='offline'?'var(--danger)':'var(--dim)';
  document.getElementById('map-tt-time').textContent=d.last_checked?'Checked: '+new Date(d.last_checked).toLocaleTimeString():'Not checked yet';
  tt.style.left=(e.clientX+14)+'px';
  tt.style.top=(e.clientY-10)+'px';
  tt.style.display='block';
  if(pin){ tt.classList.add('pinned'); activeTTNode=key; document.getElementById('map-tt-remove').setAttribute('data-key',key); }
}

function hideMapTooltip(){
  if(activeTTNode) return;
  const tt=document.getElementById('map-tooltip');
  tt.style.display='none'; tt.classList.remove('pinned');
}

function removeMapNode(){
  const key=document.getElementById('map-tt-remove').getAttribute('data-key');
  if(!key) return;
  delete mapPositions[key];
  mapConnections=mapConnections.filter(([a,b])=>a!==key&&b!==key);
  activeTTNode=null;
  const tt=document.getElementById('map-tooltip');
  tt.style.display='none'; tt.classList.remove('pinned');
  saveMapData(); renderMapNodes(); renderMapLines(); renderMapSidebar();
  toast('Removed from map','ok');
}

document.addEventListener('click',e=>{
  const tt=document.getElementById('map-tooltip');
  if(tt&&!tt.contains(e.target)&&!e.target.closest('.map-node')){
    activeTTNode=null; tt.style.display='none'; tt.classList.remove('pinned');
  }
});

// ── DRAG & DROP ──
function onSidebarDragStart(e,key){
  if(!mapEditMode){ e.preventDefault(); toast('Enable Edit Mode to place devices','warn'); return; }
  e.dataTransfer.setData('sidebarKey',key);
  e.dataTransfer.effectAllowed='copy';
  document.getElementById('sidebar-'+key.replace(':','_'))?.classList.add('dragging');
}

function initMapDrop(){
  const world=document.getElementById('map-world');
  if(!world) return;
  world.addEventListener('dragover',e=>{ e.preventDefault(); world.classList.add('drag-over'); });
  world.addEventListener('dragleave',()=>world.classList.remove('drag-over'));
  world.addEventListener('drop',e=>{
    e.preventDefault(); world.classList.remove('drag-over');
    const sKey=e.dataTransfer.getData('sidebarKey');
    const nKey=e.dataTransfer.getData('nodeKey');
    const key=sKey||nKey; if(!key) return;
    // Get coords relative to map-world accounting for scale
    const rect=world.getBoundingClientRect();
    const x=Math.round((e.clientX-rect.left)/mapScale);
    const y=Math.round((e.clientY-rect.top) /mapScale);
    const [section,idx]=key.split(':');
    mapPositions[key]={x,y,section,idx:parseInt(idx)};
    saveMapData(); renderMapNodes(); renderMapLines(); renderMapSidebar();
    toast('Device placed','ok');
  });
}

async function merakiSyncNames(){
  // Try to get creds from the import form first
  const keyEl = document.getElementById('meraki-key');
  const orgEl = document.getElementById('meraki-org');
  let apiKey = (keyEl&&keyEl.value.trim()) || (localStorage&&localStorage.getItem('meraki_api_key')) || '';
  let orgId  = (orgEl&&orgEl.value.trim()) || (localStorage&&localStorage.getItem('meraki_org_id'))  || '';
  if(!apiKey){
    const key = prompt('Enter Meraki API Key:'); if(!key) return;
    apiKey = key;
    try{ localStorage.setItem('meraki_api_key', key); }catch{}
  }
  if(!orgId){
    const org = prompt('Enter Meraki Org ID:'); if(!org) return;
    orgId = org;
    try{ localStorage.setItem('meraki_org_id', org); }catch{}
  }
  return merakiSyncNamesWithCreds(apiKey, orgId);
}

async function merakiSyncNamesWithCreds(apiKey, orgId){
  const btn = document.getElementById('map-meraki-sync-btn');
  if(btn){ btn.disabled=true; btn.textContent='⟳ Syncing...'; }
  try{
    const r = await fetch(api('/meraki-sync-names'),{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({api_key:apiKey,org_id:orgId})});
    const data = await r.json();
    if(data.error){ toast('Meraki sync failed: '+data.error,'danger'); return; }
    toast(`Meraki sync: ${data.updated} name${data.updated!==1?'s':''} updated`,'ok');
    // Refresh devices and re-render map immediately
    await fetchDevices();
    renderMapNodes(); renderMapSidebar();
  }catch(e){ toast('Meraki sync error','danger'); }
  finally{
    if(btn){ btn.disabled=false; btn.textContent='⇄ Meraki Sync'; }
  }
}

async function clearMapImage(){
  try{
    await fetch(api('/map-image/clear'),{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const bg=document.getElementById('map-bg');
    const world=document.getElementById('map-world');
    bg.src=''; bg.style.display='none';
    world.classList.add('blank');
    mapBgMode='blank';
    toast('Map image cleared','ok');
  }catch{ toast('Clear failed','danger'); }
}

async function uploadMapImage(input){
  const file=input.files[0]; if(!file) return;
  const reader=new FileReader();
  reader.onload=async e=>{
    const b64=e.target.result.split(',')[1];
    try{
      const r=await fetch(api('/map-image'),{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({image:b64,mime:file.type})});
      if(r.ok){
        const bg=document.getElementById('map-bg');
        const world=document.getElementById('map-world');
        bg.src='/map-image?t='+Date.now();
        bg.style.display='block';
        world.classList.remove('blank');
        mapBgMode='image';
        document.getElementById('map-bg-toggle').textContent='\u2B1C Blank Canvas';
        document.getElementById('map-bg-toggle').style.cssText='border:1px solid var(--border);';
        toast('Map image updated','ok');
      }
    }catch{ toast('Upload failed','danger'); }
  };
  reader.readAsDataURL(file);
}

fetchMapPositions();
fetchSavedMaps();
setTimeout(initMap, 400);
</script>
</body>
</html>"""

# ─── HTTP HANDLER ─────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass

    def get_path(self):
        """Strip ingress base path prefix if present."""
        raw = self.path.split('?')[0]
        ingress = os.environ.get('INGRESS_PATH','').rstrip('/')
        if ingress and raw.startswith(ingress):
            raw = raw[len(ingress):] or '/'
        return raw

    def get_base(self):
        """Return the ingress base path for use in HTML/JS."""
        return os.environ.get('INGRESS_PATH','').rstrip('/')

    def send_json(self,data,code=200):
        body=json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",len(body))
        self.end_headers();self.wfile.write(body)

    def do_GET(self):
        p = self.get_path()
        base = self.get_base()
        if p=="/":
            # Inject ingress base path into HTML so all fetch() calls work remotely
            html = HTML.replace("__INGRESS_BASE__", base)
            body = html.encode()
            self.send_response(200);self.send_header("Content-Type","text/html")
            self.send_header("Content-Length",len(body));self.end_headers();self.wfile.write(body)
        elif p=="/data":
            body=b"{}"
            if os.path.exists(RESULTS_FILE):
                with open(RESULTS_FILE) as f: body=f.read().encode()
            self.send_response(200);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",len(body));self.end_headers();self.wfile.write(body)
        elif p=="/config":
            self.send_json(load_config())
        elif p=="/devices":
            self.send_json(load_devices())
        elif p=="/run-progress":
            with _prog_lock: self.send_json(dict(_run_progress))
        elif p=="/ping-progress":
            with _prog_lock: self.send_json(dict(_ping_progress))
        elif p=="/warnings":
            with _warn_lock: data=list(_warnings)
            self.send_json(data)
        elif p=="/map-positions":
            self.send_json(load_map_positions())
        elif p=="/saved-maps":
            self.send_json(load_saved_maps())
        elif p.startswith("/map-image"):
            if os.path.exists(MAP_IMAGE):
                with open(MAP_IMAGE,"rb") as f: body=f.read()
                self.send_response(200);self.send_header("Content-Type","image/png")
                self.send_header("Cache-Control","no-cache")
                self.send_header("Content-Length",len(body));self.end_headers();self.wfile.write(body)
            else:
                self.send_response(404);self.end_headers();self.wfile.write(b"No map image")
        elif p=="/download-csv":
            if os.path.exists(CSV_FILE):
                with open(CSV_FILE,"rb") as f: body=f.read()
                self.send_response(200);self.send_header("Content-Type","text/csv")
                self.send_header("Content-Disposition",f'attachment; filename="{CSV_FILE}"')
                self.send_header("Content-Length",len(body));self.end_headers();self.wfile.write(body)
            else:
                self.send_response(404);self.end_headers();self.wfile.write(b"No CSV yet.")
        else:
            self.send_response(404);self.end_headers()

    def do_POST(self):
        length=int(self.headers.get("Content-Length",0))
        body=self.rfile.read(length) if length else b""
        p = self.get_path()
        if p=="/run":
            run_all_tests();self.send_json({"status":"ok"})
        elif p=="/config":
            try: save_config(json.loads(body));self.send_json({"status":"ok"})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/warnings/ack":
            try: ack_warning(json.loads(body).get("index",0));self.send_json({"status":"ok"})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/warnings/ack-all":
            ack_all();self.send_json({"status":"ok"})
        elif p=="/devices":
            try: save_devices(json.loads(body));self.send_json({"status":"ok"})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/ping-device":
            try:
                ip = json.loads(body).get("ip","")
                online = ping_device(ip)
                self.send_json({"online":online})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/ping-all":
            threading.Thread(target=ping_all_devices,daemon=True).start()
            self.send_json({"status":"started"})
        elif p=="/map-positions":
            try:
                save_map_positions(json.loads(body))
                self.send_json({"status":"ok"})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/saved-maps":
            try:
                save_saved_maps(json.loads(body))
                self.send_json({"status":"ok"})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/map-image/clear":
            try:
                if os.path.exists(MAP_IMAGE): os.remove(MAP_IMAGE)
                self.send_json({"status":"ok"})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/map-image":
            try:
                payload = json.loads(body)
                import base64
                img_data = base64.b64decode(payload.get("image",""))
                with open(MAP_IMAGE,"wb") as f: f.write(img_data)
                self.send_json({"status":"ok"})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/meraki-sync-names":
            try:
                payload  = json.loads(body)
                api_key  = payload.get("api_key","")
                org_id   = payload.get("org_id","")
                req = urllib.request.Request(
                    f"https://api.meraki.com/api/v1/organizations/{org_id}/devices",
                    headers={"X-Cisco-Meraki-API-Key":api_key,"Content-Type":"application/json"}
                )
                with urllib.request.urlopen(req,timeout=15) as resp:
                    meraki_devs = json.loads(resp.read())
                # Build IP→name lookup
                ip_to_name = {d.get("lanIp",""):d.get("name","") for d in meraki_devs if d.get("lanIp")}
                existing = load_devices()
                updated = 0
                for section in ("switches","aps","other"):
                    for dev in existing.get(section,[]):
                        ip = dev.get("ip","")
                        if ip in ip_to_name and ip_to_name[ip] and ip_to_name[ip]!=dev.get("name",""):
                            dev["name"] = ip_to_name[ip]
                            updated += 1
                save_devices(existing)
                self.send_json({"updated":updated})
            except Exception as e: self.send_json({"error":str(e)},400)
        elif p=="/meraki-import":
            try:
                payload = json.loads(body)
                api_key = payload.get("api_key","")
                org_id = payload.get("org_id","")
                req = urllib.request.Request(
                    f"https://api.meraki.com/api/v1/organizations/{org_id}/devices",
                    headers={"X-Cisco-Meraki-API-Key":api_key,"Content-Type":"application/json"}
                )
                with urllib.request.urlopen(req,timeout=15) as resp:
                    devices = json.loads(resp.read())
                existing = load_devices()
                existing_sw_ips = {d.get("ip") for d in existing.get("switches",[])}
                existing_ap_ips = {d.get("ip") for d in existing.get("aps",[])}
                sw_added = 0; ap_added = 0
                for dev in devices:
                    ip = dev.get("lanIp","")
                    name = dev.get("name","") or dev.get("serial","")
                    model = dev.get("model","")
                    notes = dev.get("notes","")
                    ptype = dev.get("productType","")
                    entry = {"name":name,"ip":ip,"model":model,"location":"","notes":notes,"online":None}
                    if ptype=="switch" and ip and ip not in existing_sw_ips:
                        existing.setdefault("switches",[]).append(entry)
                        existing_sw_ips.add(ip); sw_added+=1
                    elif ptype=="wireless" and ip and ip not in existing_ap_ips:
                        existing.setdefault("aps",[]).append(entry)
                        existing_ap_ips.add(ip); ap_added+=1
                save_devices(existing)
                self.send_json({"switches":sw_added,"aps":ap_added})
            except Exception as e: self.send_json({"error":str(e)},400)
        else:
            self.send_response(404);self.end_headers()

# ─── MAIN ─────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    print("="*55)
    print("  NETMONITOR v5.3 - Site Map Edition")
    print("="*55)
    # Clear uploaded map image on startup — image must be re-uploaded each session
    if os.path.exists(MAP_IMAGE):
        os.remove(MAP_IMAGE)
        print("  Map image cleared (session reset)")
    load_warnings()
    run_all_tests()
    threading.Thread(target=start_scheduler,daemon=True).start()
    threading.Thread(target=start_map_ping_scheduler,daemon=True).start()
    server=HTTPServer(("0.0.0.0",WEB_PORT),Handler)
    print(f"\n  Dashboard -> http://localhost:{WEB_PORT}")
    print(f"  Ctrl+C to stop\n")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n  Stopped.")
