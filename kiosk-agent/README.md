# NetMonitor Kiosk Agent (Phase 1 — ping)

A tiny standard-library-only program that runs on a kiosk / site PC and reports
ping latency to the NetMonitor dashboard. It shows up under the **Kiosks** tab.

## Set up one kiosk

1. In the dashboard: **Settings → Agents → Add agent**. Give it a name (e.g.
   `Main Kiosk 3`), optionally pick its site, and **copy the token it shows you
   once**.
2. Copy `netmon_agent.config.example.json` to `netmon_agent.config.json` and paste
   the token into `"token"`. Set `"target"` to what you want pinged
   (default `rcs.funcardapp.com`). Leave `"gateway": "auto"` to also ping the
   local gateway as a control.
3. Run it:
   - **Kiosk (no Python):** build the `.exe` once (below) and double-click it.
   - **Any machine with Python:** `python netmon_agent.py`

The agent begins pinging immediately and pushes a batch every 30s. Within a
minute the kiosk goes green in the dashboard with a live latency chart.

## Build the standalone .exe (recommended for kiosks)

On any Windows PC that has Python, double-click
**`Build standalone EXE (Windows).bat`**. It produces `dist\NetMonAgent.exe` —
one self-contained file. Copy `NetMonAgent.exe` **and** `netmon_agent.config.json`
into the same folder on each kiosk and run the exe. Nothing else to install.

To start it automatically at login, drop a shortcut to `NetMonAgent.exe` in the
kiosk's Startup folder (`shell:startup`).

## Config

| key | meaning |
|-----|---------|
| `server_url` | the dashboard URL (default already set) |
| `token` | the per-agent token from Settings → Agents (keep it secret) |
| `target` | host/IP to ping |
| `gateway` | `auto` to detect + ping the local gateway, `""` to skip, or a fixed IP |
| `interval` | seconds between pings (default 1) |
| `post_interval` | seconds between uploads (default 30) |

If the server is unreachable the agent keeps buffering (capped) and retries — no
samples are lost during a short outage.

## What's next (later phases)

The same agent will grow to report CPU/RAM/disk and POS up/down, and to execute
allow-listed remote commands (close/restart POS, disable touchscreen, reboot,
screenshot/diagnostics). No re-install needed — it's the same program.
