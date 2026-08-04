# NetMonitor Kiosk Agent

A tiny standard-library program that runs on a kiosk / site PC, reports to the
NetMonitor dashboard (**Kiosks** tab), and **keeps itself up to date**.

## How updates work (important)

The agent is two parts:

- **`NetMonAgent.exe`** — a small *bootstrapper* (Python baked in). Installed once,
  rarely changes.
- **`agent_payload.py`** — the actual logic (ping now; CPU/RAM/disk/POS and remote
  control later). The bootstrapper **downloads the latest payload from the server**,
  caches it, and re-fetches whenever a newer version is published.

So to roll out a new agent version across every kiosk, you **edit the payload on
the server and redeploy once** — the kiosks update themselves on their next
check-in. No walking around with USB sticks. It's offline-safe (runs the cached
copy if the server's down) and won't adopt a broken update (payloads are
compile-checked first).

You only ever rebuild the `.exe` if the *bootstrapper itself* changes — which is
almost never.

## Set up one kiosk

1. Dashboard → **Settings → Agents → Add agent**. Name it (e.g. `Main Kiosk 3`),
   optionally pick its site, and **copy the token** (shown once).
2. Copy `netmon_agent.config.example.json` → `netmon_agent.config.json` and paste
   the token into `"token"`. Set `"target"` to what to ping (default
   `rcs.funcardapp.com`).
3. Put these **three files in one folder** on the kiosk and run the exe:
   - `NetMonAgent.exe`
   - `netmon_agent.config.json`
   - `agent_payload.py` (the seed — auto-updates from the server)

Within a minute the kiosk goes green in the dashboard with a live latency chart.

**No Python on the kiosk?** That's the point of the `.exe` — it has Python baked
in. On a machine that *does* have Python you can instead run
`python netmon_agent.py` (with the same three-file layout).

### Auto-start at login
Drop a shortcut to `NetMonAgent.exe` in the Startup folder (Win+R → `shell:startup`).

## Build the .exe (once)

On any Windows PC with Python, double-click **`Build standalone EXE (Windows).bat`**.
It refreshes the seed payload, builds `dist\NetMonAgent.exe`, and tells you the
three files to copy.

## Config keys

| key | meaning |
|-----|---------|
| `server_url` | dashboard URL (default set) |
| `token` | per-agent token from Settings → Agents (keep secret) |
| `target` | host/IP to ping |
| `gateway` | `auto` to detect + ping the local gateway, `""` to skip, or a fixed IP |
| `interval` | seconds between pings (default 1) |
| `post_interval` | seconds between uploads (default 30) |
| `version_check_interval` | seconds between "is there a newer payload?" checks (default 600) |

## For developers — shipping an agent update

The canonical payload lives at **`cloud/app/agent_runtime/payload.py`** (this is
what the server serves). To ship a change:

1. Edit `cloud/app/agent_runtime/payload.py`, bump `PAYLOAD_VERSION`.
2. Commit, push, and redeploy the server.
3. Every agent picks it up within `version_check_interval` and restarts into the
   new payload. Nothing to touch on the kiosks.

`kiosk-agent/agent_payload.py` is just the seed copy shipped in the installer; the
build script refreshes it from the canonical file.
