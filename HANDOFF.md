# NetMonitor 2.0 — Developer Handoff Document
*Prepared 2026-08-07 for transition of development assistance. Self-contained:
everything a new developer (human or AI) needs is in here, the repo, and the
server. No secrets appear in this document — see "Access & secrets" for where
credentials live.*

## 1. What this system is
**NetMonitor** is the network-monitoring platform for Ray Cammack Shows (RCS),
a traveling carnival. It monitors ~11 UniFi sites, ~50+ Windows POS kiosks, and
renders a live site map. Owner/operator: Dawid (dawidrcs@gmail.com), RCS Tech.

Live production: **https://rcs-fleet-mon.duckdns.org**

Three moving parts:
1. **Cloud** (`cloud/`): FastAPI + async SQLAlchemy + Alembic + Postgres,
   deployed via docker-compose on a Hetzner VPS. Pulls UniFi data, ingests
   kiosk agent reports, serves the API + auth.
2. **Web** (`web/`): React 18 + TypeScript + Vite + recharts SPA, built to
   static files, served by Caddy on the same VPS. Includes an embedded
   single-file planning tool (SitePlanner) at `web/public/planner/index.html`.
3. **Kiosk agent** (`kiosk-agent/`): pure-stdlib Python on each Windows kiosk,
   shipped as a PyInstaller exe. Two-part design (critical to understand):
   - `netmon_agent.py` = **bootstrapper**, baked into `NetMonAgent.exe`
     (BOOTSTRAP_VERSION 2.4). Enrolls the kiosk (zero-touch by hostname), then
     downloads/caches/runs the payload. Rebuilt rarely, via GitHub Actions
     workflow "Build kiosk agent EXE" (artifact: NetMonAgent-windows).
   - `cloud/app/agent_runtime/payload.py` = **payload**, served by the API.
     Edit + bump PAYLOAD_VERSION + deploy → every kiosk self-updates on its
     next check-in (~10 min). This is how agent features ship WITHOUT touching
     kiosks.

## 2. Where things run
- **Repo**: https://github.com/Grimmy46/netmonitor-addon — branch **v2** is
  production; `main` is the retired 1.0 (tag v1-final). Repo is public-readable.
- **Server**: Hetzner CPX11, Ubuntu, IP 46.62.237.123, hostname rcs-hub-01.
  Dawid SSHes as `dawid` (`ssh rcs-hub`, key ~/.ssh/rcs_hub). App lives at
  `/opt/netmonitor` (a git clone of v2). Caddy (auto-HTTPS) fronts everything;
  backend on 127.0.0.1:8010; Postgres 16 in docker (`netmonitor-db-1`).
- **Dawid's Mac** (macbook-pro-local): working clone at `~/netmonitor-addon`.
- **Deploy script**: `netmonitor-prod-setup.sh` (repo root) — idempotent:
  rebuilds frontend (docker node image, `npm ci && npm run build` with
  VITE_API_BASE_URL), rewrites the Caddyfile, `docker compose -f
  docker-compose.prod.yml up -d --build`. The cloud container entrypoint runs
  `alembic upgrade head` → **DB migrations auto-apply on every deploy**.
  All migrations are written idempotently (inspect-before-alter) — keep that.

## 3. THE DEPLOY PIPELINE (do not break this)
Commits to `v2` deploy **automatically**:
1. A **launchd agent on the Mac** (`com.rcs.netmonitor-autopush`, every 60s,
   script `deploy/mac-autopush.sh`) pushes any local commit on v2 to GitHub
   using the Mac's keychain credential.
2. A **systemd timer on the server** (`netmonitor-autodeploy.timer`, every 60s,
   script `deploy/autodeploy.sh`) detects origin/v2 moved, `git reset --hard`,
   runs the setup script. Live ~3-4 min after commit.
- Logs: `~/Library/Logs/netmonitor-autopush.log` (Mac),
  `/var/log/netmonitor-autodeploy.log` (server).
- Kill switches: `launchctl unload ~/Library/LaunchAgents/com.rcs.netmonitor-autopush.plist`;
  `sudo systemctl disable --now netmonitor-autodeploy.timer`.
- **Implication: every commit to v2 goes to production.** Commit only complete,
  tested changes. `git reset --hard` on the server preserves untracked files
  (`.env` holds SECRET_KEY/DB creds — never delete it).
- Known limitation: the Mac's stored GitHub PAT **lacks the `workflow` scope**
  — any push touching `.github/workflows/` is rejected. Either add the scope
  to the PAT, or don't modify workflow files. (This is why the exe builds with
  `--console` + a runtime console-hide instead of `--windowed`.)

## 4. Access & secrets (NEVER put actual secret values in AI chats)
- **GitHub**: repo is public-readable; write access = Dawid's accounts
  (Grimmy46 / dawidrcs-design). To let a new tool push: add a collaborator or
  deploy key — Dawid's decision.
- **Server SSH**: only via Dawid's key. An AI without an execution bridge
  should OUTPUT commands for Dawid to paste into his rcs-hub SSH session.
- **Dashboard**: app-level accounts (see §6 — pending deploy). Old basic-auth
  password lived in `/opt/netmonitor/.dashpass` (being retired).
- **UniFi API keys**: entered ONLY via the dashboard Settings UI; stored
  encrypted (Fernet) in Postgres. Never in chat, screenshots, or the repo.
- **Kiosk enrollment PIN**: 6-digit code in the DB (accounts.enrollment_pin),
  shown in dashboard → Kiosks → Manage stations. It is baked into the deploy
  kit config on kiosks; regenerate it after mass rollouts.
- Standing rule of this project: **no credentials in chats or the repo.**
  Two GitHub PATs and one UniFi key were exposed early on and are still
  pending rotation — a good early task.

## 5. Domain logic worth knowing
- **UniFi ingestion**: two paths feeding the same sites/devices tables —
  Site Manager API (account key) and per-console Network Integration API
  (`https://<id>.unifi-hosting.ui.com/proxy/network/integration/v1`,
  X-API-KEY, TLS verify off). Uplink/port topology is NOT available from
  these APIs; device up/down is. Poller syncs every ~5 min.
- **Kiosk agents** report ping samples (`POST /agents/report`,
  X-Agent-Token, hashed at rest) and ALSO probe their site's LAN: they fetch
  `/agents/targets` (their linked site's device IPs), ping everything every
  ~2 min, report to `/agents/device-report`. Stations auto-link to the site
  named in `default_probe_site_name` (config, "Main").
- **5-state device model** (the product's key insight): online / degraded /
  **unreachable** (UniFi says online but no kiosk can ping it — orange, the
  money signal) / offline / unknown. Multi-vantage merge: a device is
  reachable if ANY kiosk reached it within `probe_positive_grace_seconds`.
- **PAYLOAD IMPORT RULE (caused a real outage once)**: the payload runs inside
  the frozen exe and may only import stdlib modules explicitly imported by the
  bootstrapper (see the import block in `kiosk-agent/netmon_agent.py`).
  `concurrent.futures` broke the whole fleet on 2026-08-05; use `threading`.
  The bootstrapper compile-checks payloads and self-heals within ~30s of a
  fixed payload being served, but don't rely on that.
- **Agent-facing endpoints** must never require a browser session:
  `/agents/report|version|payload|targets|device-report|enroll/*`.
- **SitePlanner** (Planner tab): a mature 486KB single-file HTML app embedded
  in an iframe, extended ONLY via an appended shim script + one hook line.
  Cloud plan storage in `site_plans` (plan JSONB + aerial bytea, per-site).
  Live map feed: `GET /map/live/{site_id}` (netcheck-compatible shape).
  Don't rewrite this tool; it works.

## 6. Current state (as of this handoff)
- Fleet: ~12+ kiosks enrolled and reporting; mass rollout of the v2.4
  zero-touch kit is in progress (kit: exe + config with enrollment PIN +
  seed payload + install.bat; per-kiosk = copy + double-click via AnyDesk).
- Dashboard: fleet view, site pages with 5-state device lists (fresh faults
  sort to top), Kiosks tab with live sparklines + row expansion + per-kiosk
  24h PDF report, Dormant tab, fleet map, Planner tab (cloud save/load +
  live device drag-and-drop + per-item switch/AP assignments).
- **IN FLIGHT — top priority**: a complete conventional-auth build (login
  landing page, first-run "create admin" setup, admin/viewer roles enforced
  server-side, user management UI; basic-auth and the interim admin-PIN both
  retired). It is finished and tested (24/24 e2e) but was awaiting the Mac
  bridge to commit when this handoff was written. Verify whether a commit
  titled "conventional accounts…" exists on v2; if not, this work exists as
  a local checkpoint in the previous assistant's workspace and may need
  re-landing. After it deploys: first dashboard visit shows the one-time
  admin-creation page.
- Kiosks pending attention: K1-6003, K2-6010, K6014 (had stale-cert issues —
  solved by agent v2.3+ which pins ISRG roots; just install the current kit),
  K3-6024 (dark since 08-05, needs the kit).
- Post-rollout hygiene: regenerate enrollment PIN; rotate the exposed PATs +
  UniFi key; delete `.dashpass` once app auth is live.

## 7. Roadmap (agreed with Dawid)
1. Finish kiosk mass rollout (v2.4 kit).
2. Agent Phase 2: CPU/RAM/disk telemetry + POS-process up/down (ship via
   payload; add columns/endpoints like ping_samples).
3. Agent Phase 3: remote control — close/restart POS, USB touchscreen
   disable/enable, reboot, screenshot; commands via a closed allow-list
   returned from /agents/report, with audit log. Wake-on-LAN needs an
   always-on LAN presence per site.
4. Inside-site topology map (uplink data isn't in the UniFi integration API —
   either find another source or derive from agent data).
5. Notifications (push/SMS/email/webhook) for device-down / mass-outage.
6. Later: second server (South Africa), multi-account tenancy (schema is
   already multi-tenant-ready).

## 8. Working conventions that kept this project healthy
- Test against a real Postgres before shipping (spin a scratch cluster; the
  repo's migrations apply cleanly from empty).
- Idempotent migrations, additive schema changes.
- One complete feature per commit (every commit auto-deploys).
- The payload is the delivery vehicle for agent changes; the exe is only for
  bootstrapper changes.
- When a kiosk misbehaves: `netmon_agent.log` next to the exe on the kiosk;
  server side: `docker logs netmonitor-cloud-1`, and the agents table
  (version + last_seen_at) tells you what each kiosk is actually running.
