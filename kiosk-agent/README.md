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

1. Copy `netmon_agent.config.example.json` → `netmon_agent.config.json` (leave the
   token empty — the kiosk claims its identity on first run).
2. Put these **three files in one folder** on the kiosk:
   - `NetMonAgent.exe`
   - `netmon_agent.config.json`
   - `agent_payload.py` (the seed — auto-updates from the server)
3. Run `NetMonAgent.exe`. On first run a **setup window** appears: type the
   enrollment PIN (dashboard → Kiosks → Manage stations → Show), pick this kiosk's
   station from the dropdown (or "➕ Add a new station"), and click **Save & start**.

Within a minute the kiosk goes green in the dashboard with a live latency chart.
Every kiosk uses the identical three files — the station is chosen at setup.

### Auto-start & background
The agent runs **windowless** (no console — invisible to customers) and
**registers itself to launch at login** on first run, so it comes back
automatically after every reboot. No Startup shortcut needed. Set
`"autostart": false` in the config to opt out.

Because there's no console, the agent logs to **`netmon_agent.log`** next to the
exe — open that to troubleshoot a kiosk.

**No Python on the kiosk?** That's the point of the `.exe` — Python is baked in.
On a machine that *does* have Python you can instead run `python netmon_agent.py`.

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
