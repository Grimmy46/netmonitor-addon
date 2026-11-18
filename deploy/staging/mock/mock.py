"""Staging mock: signal-cli REST + RCS Teardown Bot API. Logs every call."""
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

D = "/mock"
INV = os.path.join(D, "inventory.json")      # [{asset_tag, name, area, venue, primary_serial, ...}]
STATE = os.path.join(D, "state.json")        # {asset_tag: "returned"}

def load(p, d):
    try:
        return json.load(open(p))
    except Exception:
        return d

def log(entry):
    entry["t"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(os.path.join(D, "calls.jsonl"), "a") as f:
        f.write(json.dumps(entry)[:4000] + "\n")

def lookup(q):
    inv, ql, out = load(INV, []), q.lower().lstrip("#"), []
    st = load(STATE, {})
    for d in inv:
        # replay starts with everything still out at the fair
        back = st.get(str(d.get("asset_tag"))) == "returned"
        d = {**d, "status": "in_storage" if back else "deployed", "venue": "ARK" if back else "2026 ASF"}
        tag = str(d.get("asset_tag", ""))
        if ql.isdigit() and tag.lstrip("0") == ql.lstrip("0"):
            out.append({**d, "matched_on": "asset_tag"})
        elif ql == str(d.get("primary_serial", "")).lower():
            out.append({**d, "matched_on": "primary_serial"})
        elif not ql.isdigit() and len(ql) >= 3 and ql in " ".join(str(d.get(k) or "") for k in ("name", "area", "model")).lower():
            out.append({**d, "matched_on": "name"})
    return {"ok": True, "results": out[:10]}

class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        log({"m": "GET", "p": u.path, "q": q})
        if u.path == "/teardown/health":
            return self._send(200, {"ok": True, "mock": True})
        if u.path == "/teardown/lookup":
            return self._send(200, lookup((q.get("q") or [""])[0]))
        if u.path.startswith("/v1/receive"):
            return self._send(200, [])
        return self._send(200, {} if not u.path.startswith("/v1/groups") else [])

    def do_POST(self):
        u = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            j = json.loads(body or b"{}")
        except ValueError:
            j = {}
        if isinstance(j, dict) and "photo_base64" in j:
            j["photo_base64"] = f"<{len(j['photo_base64'])} b64 chars>"
        log({"m": "POST", "p": u.path, "body": j})
        if u.path == "/teardown/devices/return":
            st = load(STATE, {}); tag = str(j.get("asset_tag"))
            if not any(str(d.get("asset_tag")) == tag for d in load(INV, [])):
                return self._send(404, {"ok": False, "error": "not_found"})
            already = st.get(tag) == "returned"
            if not already and not j.get("dry_run"):
                st[tag] = "returned"; json.dump(st, open(STATE, "w"))
            return self._send(200, {"ok": True, "dry_run": bool(j.get("dry_run")),
                                    "inventory": {"already_returned": already},
                                    "message": f"#{tag} returned by {j.get('returned_by')} (mock)."})
        if u.path == "/v2/send":
            return self._send(201, {"timestamp": int(time.time() * 1000)})
        return self._send(200, {})

ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
