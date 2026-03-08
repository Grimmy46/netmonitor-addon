#!/usr/bin/env python3
"""
Network Test Automation Tool v2
- Ping / connectivity tests
- HTTP endpoint checks
- API tests (method, headers, auth, expected status/body)
- Traceroute
- Bandwidth test
- Live target management via web UI
- Scheduled + on-demand execution
- CSV logging + web dashboard
"""

import subprocess
import time
import csv
import json
import os
import threading
import urllib.request
import urllib.error
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── CONFIG FILE ─────────────────────────────────────────────────────────────

CONFIG_FILE  = "targets.json"
CSV_FILE     = "network_results.csv"
RESULTS_FILE = "latest_results.json"
WEB_PORT     = 8088
SCHEDULE_INTERVAL_MINUTES = 5

DEFAULT_CONFIG = {
    "ping": [
        {"name": "Google DNS",     "host": "8.8.8.8"},
        {"name": "Cloudflare DNS", "host": "1.1.1.1"},
        {"name": "Google",         "host": "google.com"},
    ],
    "http": [
        {"name": "Google",     "url": "https://www.google.com"},
        {"name": "Cloudflare", "url": "https://www.cloudflare.com"},
        {"name": "GitHub",     "url": "https://api.github.com"},
    ],
    "api": [
        {
            "name": "GitHub API",
            "url": "https://api.github.com",
            "method": "GET",
            "headers": {"Accept": "application/vnd.github.v3+json"},
            "body": "",
            "expected_status": 200,
            "expected_body": ""
        },
        {
            "name": "JSONPlaceholder",
            "url": "https://jsonplaceholder.typicode.com/posts/1",
            "method": "GET",
            "headers": {},
            "body": "",
            "expected_status": 200,
            "expected_body": "userId"
        },
    ],
    "traceroute": [
        {"name": "Google DNS",  "host": "8.8.8.8"},
        {"name": "Cloudflare",  "host": "1.1.1.1"},
    ],
    "bandwidth_url": "http://speedtest.tele2.net/1MB.bin"
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ─── TEST FUNCTIONS ──────────────────────────────────────────────────────────

def ping_test(host, count=4):
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", "2", host],
            capture_output=True, text=True, timeout=30
        )
        lines = result.stdout.splitlines()
        loss, rtt_avg = 100.0, None
        for line in lines:
            if "packet loss" in line:
                for p in line.split(","):
                    if "packet loss" in p:
                        loss = float(p.strip().split("%")[0])
            if "rtt" in line or "round-trip" in line:
                nums = line.split("=")[-1].strip().split("/")
                if len(nums) >= 2:
                    rtt_avg = float(nums[1])
        return {
            "type": "ping", "target": host,
            "status": "OK" if loss < 50 else "FAIL",
            "packet_loss_pct": loss, "rtt_avg_ms": rtt_avg, "error": None
        }
    except Exception as e:
        return {"type": "ping", "target": host, "status": "ERROR",
                "packet_loss_pct": 100, "rtt_avg_ms": None, "error": str(e)}


def http_test(url):
    try:
        start = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": "NetTester/2.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
            elapsed = (time.time() - start) * 1000
        return {
            "type": "http", "target": url,
            "status": "OK" if 200 <= code < 400 else "FAIL",
            "http_code": code, "latency_ms": round(elapsed, 2), "error": None
        }
    except urllib.error.HTTPError as e:
        return {"type": "http", "target": url, "status": "FAIL",
                "http_code": e.code, "latency_ms": None, "error": str(e)}
    except Exception as e:
        return {"type": "http", "target": url, "status": "ERROR",
                "http_code": None, "latency_ms": None, "error": str(e)}


def api_test(cfg_entry):
    url        = cfg_entry["url"]
    method     = cfg_entry.get("method", "GET").upper()
    headers    = dict(cfg_entry.get("headers") or {})
    body       = cfg_entry.get("body", "") or ""
    exp_status = cfg_entry.get("expected_status")
    exp_body   = cfg_entry.get("expected_body", "") or ""
    headers.setdefault("User-Agent", "NetTester/2.0")
    start = time.time()
    try:
        data = body.encode() if body else None
        req  = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as resp:
            code      = resp.getcode()
            resp_body = resp.read().decode(errors="replace")
            elapsed   = (time.time() - start) * 1000
        body_match   = (exp_body in resp_body) if exp_body else True
        status_match = (code == exp_status)    if exp_status else (200 <= code < 400)
        if status_match and body_match:
            status = "OK"
        elif not status_match:
            status = "FAIL"
        else:
            status = "WARN"
        return {
            "type": "api", "target": url, "status": status,
            "http_code": code, "latency_ms": round(elapsed, 2),
            "method": method, "body_match": body_match,
            "resp_snippet": resp_body[:200], "error": None
        }
    except urllib.error.HTTPError as e:
        return {"type": "api", "target": url, "status": "FAIL",
                "http_code": e.code, "latency_ms": round((time.time()-start)*1000, 2),
                "method": method, "body_match": False, "resp_snippet": "", "error": str(e)}
    except Exception as e:
        return {"type": "api", "target": url, "status": "ERROR",
                "http_code": None, "latency_ms": None, "method": method,
                "body_match": False, "resp_snippet": "", "error": str(e)}


def traceroute_test(host, max_hops=20):
    def parse_traceroute(output):
        hops = []
        for line in output.strip().splitlines()[1:]:
            parts = line.split()
            if not parts:
                continue
            try:
                hop_num = int(parts[0])
            except ValueError:
                continue
            ip = parts[1] if len(parts) > 1 else "*"
            rtts = []
            for p in parts[2:]:
                try:
                    rtts.append(float(p))
                except ValueError:
                    pass
            avg_rtt = round(sum(rtts) / len(rtts), 2) if rtts else None
            hops.append({"hop": hop_num, "ip": ip, "rtt_avg_ms": avg_rtt})
        return hops

    def parse_tracepath(output):
        hops = []
        for line in output.strip().splitlines():
            parts = line.split()
            if not parts:
                continue
            try:
                hop_num = int(parts[0].rstrip(":").rstrip("?"))
            except ValueError:
                continue
            ip = parts[1] if len(parts) > 1 else "*"
            rtts = []
            for p in parts:
                if p.endswith("ms"):
                    try:
                        rtts.append(float(p[:-2]))
                    except Exception:
                        pass
            avg_rtt = round(sum(rtts) / len(rtts), 2) if rtts else None
            hops.append({"hop": hop_num, "ip": ip, "rtt_avg_ms": avg_rtt})
        return hops

    # Try traceroute first, then tracepath
    for cmd, parser in [
        (["traceroute", "-m", str(max_hops), "-w", "2", "-n", host], parse_traceroute),
        (["tracepath",  "-n", host], parse_tracepath),
    ]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            hops   = parser(result.stdout)
            return {
                "type": "traceroute", "target": host,
                "status": "OK" if hops else "FAIL",
                "hops": hops, "hop_count": len(hops), "error": None
            }
        except FileNotFoundError:
            continue
        except Exception as e:
            return {"type": "traceroute", "target": host, "status": "ERROR",
                    "hops": [], "hop_count": 0, "error": str(e)}

    return {"type": "traceroute", "target": host, "status": "ERROR",
            "hops": [], "hop_count": 0,
            "error": "traceroute/tracepath not found — install with: sudo apt install traceroute"}


def bandwidth_test(url):
    try:
        req   = urllib.request.Request(url, headers={"User-Agent": "NetTester/2.0"})
        start = time.time()
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        elapsed    = time.time() - start
        bytes_dl   = len(data)
        speed_mbps = round((bytes_dl * 8) / (elapsed * 1_000_000), 2)
        return {
            "type": "bandwidth", "target": url, "status": "OK",
            "speed_mbps": speed_mbps, "bytes_downloaded": bytes_dl,
            "duration_s": round(elapsed, 2), "error": None
        }
    except Exception as e:
        return {"type": "bandwidth", "target": url, "status": "ERROR",
                "speed_mbps": None, "bytes_downloaded": None,
                "duration_s": None, "error": str(e)}


# ─── RUN ALL TESTS ───────────────────────────────────────────────────────────

def run_all_tests():
    cfg       = load_config()
    timestamp = datetime.now().isoformat()
    print(f"\n[{timestamp}] Running network tests...")
    results   = []

    for t in cfg.get("ping", []):
        r = ping_test(t["host"])
        r["name"] = t["name"]; r["timestamp"] = timestamp
        results.append(r)
        icon = "v" if r["status"] == "OK" else "x"
        print(f"  [{icon}] PING       {t['name']:22s} | loss={r['packet_loss_pct']}% rtt={r['rtt_avg_ms']}ms")

    for t in cfg.get("http", []):
        r = http_test(t["url"])
        r["name"] = t["name"]; r["timestamp"] = timestamp
        results.append(r)
        icon = "v" if r["status"] == "OK" else "x"
        print(f"  [{icon}] HTTP       {t['name']:22s} | {r['http_code']} {r['latency_ms']}ms")

    for t in cfg.get("api", []):
        r = api_test(t)
        r["name"] = t["name"]; r["timestamp"] = timestamp
        results.append(r)
        icon = "v" if r["status"] == "OK" else ("~" if r["status"] == "WARN" else "x")
        print(f"  [{icon}] API        {t['name']:22s} | {r['http_code']} {r['latency_ms']}ms [{t.get('method','GET')}]")

    for t in cfg.get("traceroute", []):
        print(f"  [~] TRACEROUTE {t['name']:20s} | running...")
        r = traceroute_test(t["host"])
        r["name"] = t["name"]; r["timestamp"] = timestamp
        results.append(r)
        icon = "v" if r["status"] == "OK" else "x"
        print(f"  [{icon}] TRACEROUTE {t['name']:22s} | {r['hop_count']} hops")

    bw_url = cfg.get("bandwidth_url", DEFAULT_CONFIG["bandwidth_url"])
    print(f"  [~] BW         Download speed test...")
    r = bandwidth_test(bw_url)
    r["name"] = "Download Speed"; r["timestamp"] = timestamp
    results.append(r)
    icon = "v" if r["status"] == "OK" else "x"
    print(f"  [{icon}] BW         Download Speed         | {r['speed_mbps']} Mbps")

    save_csv(results)
    save_json(results)
    print(f"  -> Saved to {CSV_FILE}")
    return results


# ─── STORAGE ─────────────────────────────────────────────────────────────────

def save_csv(results):
    file_exists = os.path.exists(CSV_FILE)
    fieldnames  = [
        "timestamp", "name", "type", "target", "status",
        "packet_loss_pct", "rtt_avg_ms",
        "http_code", "latency_ms", "method", "body_match",
        "hop_count", "speed_mbps", "bytes_downloaded", "duration_s", "error"
    ]
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        writer.writerows(results)


def save_json(results):
    history = {}
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE) as f:
                history = json.load(f)
        except Exception:
            history = {}
    for r in results:
        key = r["name"]
        history.setdefault(key, []).append(r)
        history[key] = history[key][-50:]
    with open(RESULTS_FILE, "w") as f:
        json.dump(history, f)


# ─── HTML DASHBOARD ──────────────────────────────────────────────────────────

HTML_DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NetMonitor</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Exo+2:wght@300;500;600;800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#050a0f;--panel:#0a1520;--panel2:#0d1e30;--border:#0d2d4a;
  --accent:#00d4ff;--accent2:#00ff9d;--warn:#ffcc00;--danger:#ff2d55;
  --text:#c8e6f5;--dim:#4a7a99;--api:#b36bff;--trace:#ff9d3b;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'Exo 2',sans-serif;min-height:100vh;
  background-image:linear-gradient(rgba(0,212,255,.025) 1px,transparent 1px),
  linear-gradient(90deg,rgba(0,212,255,.025) 1px,transparent 1px);
  background-size:40px 40px;}
header{display:flex;align-items:center;justify-content:space-between;padding:1.2rem 2rem;
  border-bottom:1px solid var(--border);background:rgba(10,21,32,.95);
  backdrop-filter:blur(10px);position:sticky;top:0;z-index:200;}
.logo{display:flex;align-items:center;gap:.75rem;}
.logo-dot{width:32px;height:32px;border:2px solid var(--accent);border-radius:8px;
  display:flex;align-items:center;justify-content:center;box-shadow:0 0 18px rgba(0,212,255,.3);}
.logo-dot::before{content:'';width:10px;height:10px;background:var(--accent);border-radius:50%;
  box-shadow:0 0 8px var(--accent);animation:pulse 2s infinite;}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1);}50%{opacity:.4;transform:scale(.7);}}
.logo h1{font-size:1.2rem;font-weight:800;letter-spacing:.12em;color:var(--accent);}
.logo small{font-family:'Share Tech Mono';font-size:.65rem;color:var(--dim);display:block;}
.hdr-actions{display:flex;gap:.75rem;align-items:center;flex-wrap:wrap;}
.badge-pill{font-family:'Share Tech Mono';font-size:.7rem;padding:.3rem .8rem;border-radius:4px;
  border:1px solid var(--accent2);color:var(--accent2);background:rgba(0,255,157,.05);}
.btn{font-family:'Exo 2';font-weight:600;font-size:.82rem;padding:.45rem 1.1rem;
  border-radius:6px;cursor:pointer;transition:all .2s;letter-spacing:.04em;border:none;}
.btn-outline{border:1px solid var(--accent);color:var(--accent);background:rgba(0,212,255,.07);}
.btn-outline:hover{background:rgba(0,212,255,.18);box-shadow:0 0 14px rgba(0,212,255,.3);}
.btn-green{border:1px solid var(--accent2) !important;color:var(--accent2);background:rgba(0,255,157,.06);}
.btn-green:hover{background:rgba(0,255,157,.15);}
.btn:disabled{opacity:.5;cursor:not-allowed;}
a.btn{text-decoration:none;display:inline-block;}
.tabs{display:flex;border-bottom:1px solid var(--border);background:rgba(10,21,32,.8);
  padding:0 2rem;position:sticky;top:64px;z-index:150;}
.tab{font-family:'Exo 2';font-size:.78rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
  padding:.75rem 1.4rem;cursor:pointer;color:var(--dim);border-bottom:2px solid transparent;transition:all .2s;}
.tab:hover{color:var(--text);}
.tab.active{color:var(--accent);border-bottom-color:var(--accent);}
main{padding:1.5rem 2rem;max-width:1500px;margin:0 auto;}
.page{display:none;}
.page.active{display:block;}
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:1rem;margin-bottom:1.5rem;}
.sc{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:1.1rem;position:relative;overflow:hidden;}
.sc::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent),var(--accent2));}
.sc .lbl{font-size:.65rem;letter-spacing:.15em;color:var(--dim);text-transform:uppercase;margin-bottom:.4rem;}
.sc .val{font-size:1.9rem;font-weight:800;line-height:1;}
.sc .sub{font-family:'Share Tech Mono';font-size:.7rem;color:var(--dim);margin-top:.3rem;}
.section{margin-bottom:2rem;}
.sec-hdr{display:flex;align-items:center;gap:.6rem;margin-bottom:.9rem;padding-bottom:.7rem;border-bottom:1px solid var(--border);}
.sec-hdr h2{font-size:.85rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;}
.cnt{font-family:'Share Tech Mono';font-size:.65rem;padding:.15rem .5rem;border-radius:99px;border:1px solid var(--border);background:rgba(255,255,255,.03);}
.test-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:.9rem;}
.test-grid.traceroute-grid{grid-template-columns:repeat(auto-fill,minmax(420px,1fr));}
.tc{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:1rem;transition:border-color .2s,box-shadow .2s;min-width:0;overflow:hidden;}
.tc:hover{border-color:var(--accent);box-shadow:0 0 18px rgba(0,212,255,.1);}
.tc.ok{border-left:3px solid var(--accent2);}
.tc.fail{border-left:3px solid var(--danger);}
.tc.warn{border-left:3px solid var(--warn);}
.tc.error{border-left:3px solid #ff6b35;}
.tc.unknown{border-left:3px solid var(--dim);}
.tc-hdr{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.7rem;}
.tc-name{font-weight:600;font-size:.9rem;}
.tc-tgt{font-family:'Share Tech Mono';font-size:.6rem;color:var(--dim);margin-top:.2rem;word-break:break-all;}
.badge{font-family:'Share Tech Mono';font-size:.62rem;font-weight:700;padding:.18rem .55rem;border-radius:4px;letter-spacing:.08em;white-space:nowrap;}
.badge.ok{background:rgba(0,255,157,.1);color:var(--accent2);border:1px solid rgba(0,255,157,.3);}
.badge.fail{background:rgba(255,45,85,.1);color:var(--danger);border:1px solid rgba(255,45,85,.3);}
.badge.warn{background:rgba(255,204,0,.1);color:var(--warn);border:1px solid rgba(255,204,0,.3);}
.badge.error{background:rgba(255,107,53,.1);color:#ff6b35;border:1px solid rgba(255,107,53,.3);}
.badge.unknown{background:rgba(74,122,153,.1);color:var(--dim);border:1px solid var(--border);}
.metrics{display:flex;gap:1.1rem;flex-wrap:wrap;}
.metric .ml{font-size:.58rem;color:var(--dim);text-transform:uppercase;letter-spacing:.1em;}
.metric .mv{font-family:'Share Tech Mono';font-size:.88rem;}
.sparkline{margin-top:.7rem;height:28px;}
.sparkline svg{width:100%;height:100%;}
.hops-wrap{overflow-x:auto;margin-top:.7rem;max-width:100%;}
.hops-table{width:100%;border-collapse:collapse;font-family:'Share Tech Mono';font-size:.7rem;table-layout:fixed;}
.hops-table colgroup col:nth-child(1){width:32px;}
.hops-table colgroup col:nth-child(2){width:38%;}
.hops-table colgroup col:nth-child(3){width:70px;}
.hops-table colgroup col:nth-child(4){width:auto;}
.hops-table th{color:var(--dim);font-weight:400;text-align:left;padding:.28rem .45rem;border-bottom:1px solid var(--border);white-space:nowrap;}
.hops-table td{padding:.28rem .45rem;border-bottom:1px solid rgba(13,45,74,.35);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.hops-table tr:last-child td{border:none;}
.hop-bar{height:5px;background:var(--trace);border-radius:3px;min-width:2px;opacity:.7;max-width:100%;}
.mgr-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1.2rem;}
.mgr-card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:1.2rem;}
.mgr-card h3{font-size:.78rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;margin-bottom:1rem;padding-bottom:.6rem;border-bottom:1px solid var(--border);}
.target-list{display:flex;flex-direction:column;gap:.45rem;margin-bottom:.9rem;min-height:36px;}
.target-item{display:flex;align-items:center;justify-content:space-between;background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:.45rem .7rem;}
.ti-name{font-family:'Share Tech Mono';font-size:.72rem;color:var(--text);font-weight:700;}
.ti-val{font-family:'Share Tech Mono';font-size:.62rem;color:var(--dim);}
.btn-del{background:none;border:none;color:var(--dim);cursor:pointer;font-size:.85rem;padding:.1rem .35rem;border-radius:4px;transition:color .2s;}
.btn-del:hover{color:var(--danger);}
.add-form{display:flex;flex-direction:column;gap:.5rem;}
.add-form input,.add-form select,.add-form textarea{
  background:var(--panel2);border:1px solid var(--border);color:var(--text);
  font-family:'Share Tech Mono';font-size:.75rem;padding:.42rem .65rem;border-radius:6px;
  outline:none;transition:border-color .2s;width:100%;}
.add-form input:focus,.add-form select:focus,.add-form textarea:focus{border-color:var(--accent);}
.add-form textarea{resize:vertical;min-height:56px;}
.add-form .row{display:flex;gap:.5rem;}
.add-form .row>*{flex:1;min-width:0;}
.add-form label{font-size:.6rem;color:var(--dim);letter-spacing:.08em;text-transform:uppercase;display:block;margin-bottom:.2rem;}
.form-group{display:flex;flex-direction:column;}
.save-notice{font-family:'Share Tech Mono';font-size:.68rem;color:var(--accent2);margin-top:.5rem;opacity:0;transition:opacity .4s;}
.save-notice.show{opacity:1;}
.last-upd{font-family:'Share Tech Mono';font-size:.68rem;color:var(--dim);text-align:right;margin-bottom:1rem;}
.empty{text-align:center;padding:3rem;color:var(--dim);font-family:'Share Tech Mono';}
#toast{position:fixed;bottom:2rem;right:2rem;background:var(--panel);border:1px solid var(--accent);
  color:var(--accent);font-family:'Share Tech Mono';font-size:.8rem;padding:.7rem 1.2rem;
  border-radius:8px;box-shadow:0 0 28px rgba(0,212,255,.3);opacity:0;transform:translateY(8px);
  transition:all .3s;pointer-events:none;z-index:999;}
#toast.show{opacity:1;transform:translateY(0);}
.type-ping{color:var(--accent2);}
.type-http{color:var(--accent);}
.type-api{color:var(--api);}
.type-traceroute{color:var(--trace);}
.type-bandwidth{color:var(--warn);}
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-dot"></div>
    <div>
      <h1>NETMONITOR</h1>
      <small>AUTOMATED NETWORK DIAGNOSTICS v2</small>
    </div>
  </div>
  <div class="hdr-actions">
    <span class="badge-pill">&#9201; SCHEDULED: EVERY 5 MIN</span>
    <a class="btn btn-green" href="/download-csv">&#8595; CSV</a>
    <button class="btn btn-outline" id="run-btn" onclick="runTests()">&#9654; RUN NOW</button>
  </div>
</header>

<div class="tabs">
  <div class="tab active" onclick="showTab('dashboard',this)">Dashboard</div>
  <div class="tab" onclick="showTab('targets',this)">&#9881; Manage Targets</div>
</div>

<main>
  <div class="page active" id="page-dashboard">
    <div class="last-upd" id="last-updated">Loading...</div>
    <div class="summary-grid" id="summary"></div>
    <div id="sections"></div>
  </div>

  <div class="page" id="page-targets">
    <div style="margin-bottom:1.2rem;display:flex;align-items:center;justify-content:space-between;">
      <div style="font-size:.8rem;color:var(--dim);font-family:'Share Tech Mono';">
        Changes save instantly and apply on the next test run.
      </div>
      <button class="btn btn-outline" onclick="runTests()">&#9654; Run Tests Now</button>
    </div>
    <div class="mgr-grid">

      <!-- PING -->
      <div class="mgr-card">
        <h3 class="type-ping">&#11044; Ping Targets</h3>
        <div class="target-list" id="list-ping"></div>
        <div class="add-form">
          <div class="row">
            <div class="form-group"><label>Name</label><input id="ping-name" placeholder="My Server"></div>
            <div class="form-group"><label>Host / IP</label><input id="ping-host" placeholder="192.168.1.1"></div>
          </div>
          <button class="btn btn-outline" onclick="addTarget('ping')">+ Add Ping Target</button>
        </div>
        <div class="save-notice" id="notice-ping">&#10003; Saved</div>
      </div>

      <!-- HTTP -->
      <div class="mgr-card">
        <h3 class="type-http">&#11044; HTTP Targets</h3>
        <div class="target-list" id="list-http"></div>
        <div class="add-form">
          <div class="row">
            <div class="form-group"><label>Name</label><input id="http-name" placeholder="My Site"></div>
            <div class="form-group"><label>URL</label><input id="http-url" placeholder="https://example.com"></div>
          </div>
          <button class="btn btn-outline" onclick="addTarget('http')">+ Add HTTP Target</button>
        </div>
        <div class="save-notice" id="notice-http">&#10003; Saved</div>
      </div>

      <!-- API -->
      <div class="mgr-card">
        <h3 class="type-api">&#11044; API Targets</h3>
        <div class="target-list" id="list-api"></div>
        <div class="add-form">
          <div class="row">
            <div class="form-group"><label>Name</label><input id="api-name" placeholder="My API"></div>
            <div class="form-group"><label>Method</label>
              <select id="api-method">
                <option>GET</option><option>POST</option><option>PUT</option>
                <option>PATCH</option><option>DELETE</option>
              </select>
            </div>
          </div>
          <div class="form-group"><label>URL</label><input id="api-url" placeholder="https://api.example.com/v1/health"></div>
          <div class="row">
            <div class="form-group"><label>Expected Status</label><input id="api-status" placeholder="200" type="number"></div>
            <div class="form-group"><label>Body Must Contain</label><input id="api-body-check" placeholder="ok"></div>
          </div>
          <div class="form-group"><label>Headers JSON (optional)</label>
            <textarea id="api-headers" placeholder='{"Authorization":"Bearer TOKEN"}'></textarea>
          </div>
          <div class="form-group"><label>Request Body (POST/PUT)</label>
            <textarea id="api-body" placeholder='{"key":"value"}'></textarea>
          </div>
          <button class="btn btn-outline" onclick="addTarget('api')">+ Add API Target</button>
        </div>
        <div class="save-notice" id="notice-api">&#10003; Saved</div>
      </div>

      <!-- TRACEROUTE -->
      <div class="mgr-card">
        <h3 class="type-traceroute">&#11044; Traceroute Targets</h3>
        <div class="target-list" id="list-traceroute"></div>
        <div class="add-form">
          <div class="row">
            <div class="form-group"><label>Name</label><input id="tr-name" placeholder="My Gateway"></div>
            <div class="form-group"><label>Host / IP</label><input id="tr-host" placeholder="10.0.0.1"></div>
          </div>
          <button class="btn btn-outline" onclick="addTarget('traceroute')">+ Add Traceroute Target</button>
        </div>
        <div class="save-notice" id="notice-traceroute">&#10003; Saved</div>
      </div>

      <!-- BANDWIDTH -->
      <div class="mgr-card">
        <h3 class="type-bandwidth">&#11044; Bandwidth Test URL</h3>
        <div style="font-family:'Share Tech Mono';font-size:.7rem;color:var(--dim);margin-bottom:.8rem;line-height:1.5;">
          Direct URL to a binary file for download speed testing.<br>
          Tip: use a 10MB+ file for more accurate results.
        </div>
        <div class="add-form">
          <div class="form-group">
            <label>Test File URL</label>
            <input id="bw-url" placeholder="http://speedtest.tele2.net/10MB.bin">
          </div>
          <button class="btn btn-outline" onclick="saveBwUrl()">Save URL</button>
        </div>
        <div class="save-notice" id="notice-bw">&#10003; Saved</div>
      </div>

    </div>
  </div>
</main>

<div id="toast"></div>

<script>
let allData = {}, allConfig = {};

function showTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('page-' + name).classList.add('active');
}

function toast(msg, ok=true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.borderColor = ok ? 'var(--accent)' : 'var(--danger)';
  t.style.color = ok ? 'var(--accent)' : 'var(--danger)';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

function showNotice(id) {
  const el = document.getElementById('notice-' + id);
  if (!el) return;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}

function sparkline(history, key) {
  const vals = history.map(h => h[key]).filter(v => v != null);
  if (vals.length < 2) return '';
  const min = Math.min(...vals), max = Math.max(...vals), range = max - min || 1;
  const w = 260, h = 26;
  const pts = vals.map((v,i) => `${(i/(vals.length-1))*w},${h-((v-min)/range)*h}`).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="rgba(0,212,255,.55)" stroke-width="1.5"/>
  </svg>`;
}

function sc(s) { return (s||'unknown').toLowerCase(); }

function renderDashboard(data) {
  allData = data;
  const all = Object.values(data).flat();
  if (!all.length) {
    document.getElementById('summary').innerHTML = '';
    document.getElementById('sections').innerHTML = '<div class="empty">No data yet. Click RUN NOW to start.</div>';
    document.getElementById('last-updated').textContent = 'No data yet';
    return;
  }
  const latest = {};
  for (const [n, rs] of Object.entries(data)) latest[n] = rs[rs.length-1];
  const arr = Object.values(latest);
  const ts  = arr[0]?.timestamp;
  if (ts) document.getElementById('last-updated').textContent =
    'Last updated: ' + new Date(ts).toLocaleString();

  const total = arr.length;
  const ok    = arr.filter(r => r.status === 'OK').length;
  const fail  = arr.filter(r => ['FAIL','ERROR'].includes(r.status)).length;
  const lats  = arr.filter(r => r.latency_ms).map(r => r.latency_ms);
  const avgLat = lats.length ? (lats.reduce((a,b)=>a+b,0)/lats.length).toFixed(0) : '&#8212;';
  const bw    = arr.find(r => r.type === 'bandwidth');

  document.getElementById('summary').innerHTML = `
    <div class="sc"><div class="lbl">Online</div>
      <div class="val" style="color:var(--accent2)">${ok}/${total}</div>
      <div class="sub">targets OK</div></div>
    <div class="sc"><div class="lbl">Failures</div>
      <div class="val" style="color:${fail?'var(--danger)':'var(--dim)'}">${fail}</div>
      <div class="sub">tests failing</div></div>
    <div class="sc"><div class="lbl">Avg Latency</div>
      <div class="val">${avgLat}</div><div class="sub">milliseconds</div></div>
    <div class="sc"><div class="lbl">Download</div>
      <div class="val">${bw?.speed_mbps ?? '&#8212;'}</div><div class="sub">Mbps</div></div>
  `;

  const byType = {ping:[],http:[],api:[],traceroute:[],bandwidth:[]};
  for (const [name, history] of Object.entries(data)) {
    const last = history[history.length-1];
    if (last && byType[last.type]) byType[last.type].push({name,last,history});
  }

  const labels = {
    ping:'&#11044; Connectivity / Ping',
    http:'&#11044; HTTP Endpoints',
    api:'&#11044; API Tests',
    traceroute:'&#11044; Traceroute',
    bandwidth:'&#11044; Bandwidth'
  };
  let html = '';

  for (const [type, items] of Object.entries(byType)) {
    if (!items.length) continue;
    html += `<div class="section"><div class="sec-hdr">
      <h2 class="type-${type}">${labels[type]}</h2>
      <span class="cnt">${items.length}</span></div><div class="test-grid${type==='traceroute'?' traceroute-grid':''}">`;

    for (const {name, last, history} of items) {
      const cls = sc(last.status);
      let metrics = '', extra = '';

      if (type === 'ping') {
        metrics = `
          <div class="metric"><div class="ml">Packet Loss</div><div class="mv">${last.packet_loss_pct??'&#8212;'}%</div></div>
          <div class="metric"><div class="ml">RTT Avg</div><div class="mv">${last.rtt_avg_ms??'&#8212;'} ms</div></div>`;
        extra = sparkline(history, 'rtt_avg_ms');
      } else if (type === 'http') {
        metrics = `
          <div class="metric"><div class="ml">Code</div><div class="mv">${last.http_code??'&#8212;'}</div></div>
          <div class="metric"><div class="ml">Latency</div><div class="mv">${last.latency_ms??'&#8212;'} ms</div></div>`;
        extra = sparkline(history, 'latency_ms');
      } else if (type === 'api') {
        const bm = last.body_match === true ? '&#10003;' : last.body_match === false ? '&#10007;' : '&#8212;';
        metrics = `
          <div class="metric"><div class="ml">Method</div><div class="mv">${last.method??'GET'}</div></div>
          <div class="metric"><div class="ml">Code</div><div class="mv">${last.http_code??'&#8212;'}</div></div>
          <div class="metric"><div class="ml">Latency</div><div class="mv">${last.latency_ms??'&#8212;'} ms</div></div>
          <div class="metric"><div class="ml">Body</div><div class="mv">${bm}</div></div>`;
        extra = sparkline(history, 'latency_ms');
      } else if (type === 'traceroute') {
        const hops = last.hops || [];
        const maxRtt = Math.max(...hops.map(h => h.rtt_avg_ms||0), 1);
        metrics = `<div class="metric"><div class="ml">Hops</div><div class="mv">${last.hop_count??'&#8212;'}</div></div>`;
        if (hops.length) {
          extra = `<div class="hops-wrap"><table class="hops-table">
            <colgroup><col/><col/><col/><col/></colgroup>
            <tr><th>#</th><th>IP</th><th>RTT</th><th></th></tr>` +
            hops.slice(0,15).map(h => `<tr>
              <td>${h.hop}</td>
              <td title="${h.ip}">${h.ip}</td>
              <td>${h.rtt_avg_ms != null ? h.rtt_avg_ms+'ms' : '*'}</td>
              <td><div class="hop-bar" style="width:${h.rtt_avg_ms!=null?Math.max(2,Math.min(100,(h.rtt_avg_ms/maxRtt*100))).toFixed(0):2}%"></div></td>
            </tr>`).join('') +
            (hops.length > 15 ? `<tr><td colspan="4" style="color:var(--dim);font-size:.65rem">&#8230; ${hops.length-15} more hops</td></tr>` : '') +
            '</table></div>';
        }
      } else {
        metrics = `
          <div class="metric"><div class="ml">Speed</div><div class="mv">${last.speed_mbps??'&#8212;'} Mbps</div></div>
          <div class="metric"><div class="ml">Duration</div><div class="mv">${last.duration_s??'&#8212;'}s</div></div>`;
        extra = sparkline(history, 'speed_mbps');
      }

      html += `<div class="tc ${cls}">
        <div class="tc-hdr">
          <div><div class="tc-name">${name}</div><div class="tc-tgt">${last.target??''}</div></div>
          <span class="badge ${cls}">${last.status}</span>
        </div>
        <div class="metrics">${metrics}</div>
        ${extra ? `<div class="${type==='traceroute'?'':'sparkline'}">${extra}</div>` : ''}
        ${last.error ? `<div style="font-family:'Share Tech Mono';font-size:.62rem;color:#ff6b35;margin-top:.5rem;word-break:break-all">${last.error}</div>` : ''}
      </div>`;
    }
    html += '</div></div>';
  }
  document.getElementById('sections').innerHTML = html || '<div class="empty">No results yet.</div>';
}

function renderTargetManager(cfg) {
  allConfig = cfg;
  const renderList = (type, items, labelFn) => {
    const el = document.getElementById('list-' + type);
    if (!el) return;
    if (!items || !items.length) {
      el.innerHTML = '<div style="font-family:\'Share Tech Mono\';font-size:.7rem;color:var(--dim);padding:.3rem 0">No targets configured.</div>';
      return;
    }
    el.innerHTML = items.map((t, i) => `
      <div class="target-item">
        <div><div class="ti-name">${t.name}</div><div class="ti-val">${labelFn(t)}</div></div>
        <button class="btn-del" onclick="removeTarget('${type}',${i})" title="Remove">&#10005;</button>
      </div>`).join('');
  };
  renderList('ping',       cfg.ping||[],       t => t.host);
  renderList('http',       cfg.http||[],       t => t.url);
  renderList('api',        cfg.api||[],        t => `[${t.method||'GET'}] ${t.url}`);
  renderList('traceroute', cfg.traceroute||[], t => t.host);
  const bwEl = document.getElementById('bw-url');
  if (bwEl) bwEl.value = cfg.bandwidth_url || '';
}

function addTarget(type) {
  const cfg = JSON.parse(JSON.stringify(allConfig));
  let target;
  if (type === 'ping') {
    const name = document.getElementById('ping-name').value.trim();
    const host = document.getElementById('ping-host').value.trim();
    if (!name || !host) return toast('Name and host required', false);
    target = {name, host};
    document.getElementById('ping-name').value = '';
    document.getElementById('ping-host').value = '';
  } else if (type === 'http') {
    const name = document.getElementById('http-name').value.trim();
    const url  = document.getElementById('http-url').value.trim();
    if (!name || !url) return toast('Name and URL required', false);
    target = {name, url};
    document.getElementById('http-name').value = '';
    document.getElementById('http-url').value = '';
  } else if (type === 'api') {
    const name    = document.getElementById('api-name').value.trim();
    const url     = document.getElementById('api-url').value.trim();
    const method  = document.getElementById('api-method').value;
    const expSt   = document.getElementById('api-status').value.trim();
    const expBd   = document.getElementById('api-body-check').value.trim();
    const hdrsRaw = document.getElementById('api-headers').value.trim();
    const bodyRaw = document.getElementById('api-body').value.trim();
    if (!name || !url) return toast('Name and URL required', false);
    let headers = {};
    if (hdrsRaw) { try { headers = JSON.parse(hdrsRaw); } catch { return toast('Invalid JSON in headers', false); } }
    target = {name, url, method, headers, body: bodyRaw,
      expected_status: expSt ? parseInt(expSt) : null, expected_body: expBd};
    ['api-name','api-url','api-status','api-body-check','api-headers','api-body']
      .forEach(id => document.getElementById(id).value = '');
  } else if (type === 'traceroute') {
    const name = document.getElementById('tr-name').value.trim();
    const host = document.getElementById('tr-host').value.trim();
    if (!name || !host) return toast('Name and host required', false);
    target = {name, host};
    document.getElementById('tr-name').value = '';
    document.getElementById('tr-host').value = '';
  }
  cfg[type] = cfg[type] || [];
  cfg[type].push(target);
  saveConfigToServer(cfg, type);
}

function removeTarget(type, idx) {
  const cfg = JSON.parse(JSON.stringify(allConfig));
  cfg[type].splice(idx, 1);
  saveConfigToServer(cfg, type);
}

function saveBwUrl() {
  const url = document.getElementById('bw-url').value.trim();
  if (!url) return toast('URL required', false);
  const cfg = JSON.parse(JSON.stringify(allConfig));
  cfg.bandwidth_url = url;
  saveConfigToServer(cfg, 'bw');
}

async function saveConfigToServer(cfg, noticeId) {
  try {
    const r = await fetch('/config', {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(cfg)});
    if (!r.ok) throw new Error();
    allConfig = cfg;
    renderTargetManager(cfg);
    showNotice(noticeId);
    toast('Target saved');
  } catch { toast('Failed to save', false); }
}

async function runTests() {
  const btn = document.getElementById('run-btn');
  btn.textContent = 'Running...';
  btn.disabled = true;
  try {
    await fetch('/run', {method:'POST'});
    toast('Tests complete');
    await Promise.all([fetchData(), fetchConfig()]);
  } catch { toast('Run failed', false); }
  finally { btn.innerHTML = '&#9654; RUN NOW'; btn.disabled = false; }
}

async function fetchData() {
  try { renderDashboard(await (await fetch('/data')).json()); }
  catch(e) { console.error(e); }
}

async function fetchConfig() {
  try { renderTargetManager(await (await fetch('/config')).json()); }
  catch(e) { console.error(e); }
}

fetchData();
fetchConfig();
setInterval(fetchData, 15000);
</script>
</body>
</html>"""


# ─── HTTP HANDLER ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            body = HTML_DASHBOARD.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/data":
            body = b"{}"
            if os.path.exists(RESULTS_FILE):
                with open(RESULTS_FILE) as f:
                    body = f.read().encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/config":
            self.send_json(load_config())
        elif self.path == "/download-csv":
            if os.path.exists(CSV_FILE):
                with open(CSV_FILE, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Disposition", f'attachment; filename="{CSV_FILE}"')
                self.send_header("Content-Length", len(body))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()
                self.wfile.write(b"No CSV data yet.")
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""
        if self.path == "/run":
            run_all_tests()
            self.send_json({"status": "ok"})
        elif self.path == "/config":
            try:
                cfg = json.loads(body)
                save_config(cfg)
                self.send_json({"status": "ok"})
            except Exception as e:
                self.send_json({"error": str(e)}, 400)
        else:
            self.send_response(404); self.end_headers()


# ─── SCHEDULER ───────────────────────────────────────────────────────────────

def start_scheduler():
    print(f"  Scheduler: tests every {SCHEDULE_INTERVAL_MINUTES} minutes")
    while True:
        time.sleep(SCHEDULE_INTERVAL_MINUTES * 60)
        run_all_tests()


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 58)
    print("  NETMONITOR v2 - Network Test Automation")
    print("=" * 58)
    run_all_tests()
    threading.Thread(target=start_scheduler, daemon=True).start()
    server = HTTPServer(("0.0.0.0", WEB_PORT), Handler)
    print(f"\n  Dashboard  ->  http://localhost:{WEB_PORT}")
    print(f"  CSV log    ->  {CSV_FILE}")
    print(f"  Config     ->  {CONFIG_FILE}")
    print(f"  Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
