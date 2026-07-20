# NetMonitor 2.0 — Architecture (Phase 0)

## Components

- **cloud/** — FastAPI + PostgreSQL. Owns config, storage, the UniFi Site Manager
  integration, and (Phase 2+) agent ingest, alerting, and auth. Async SQLAlchemy;
  Alembic migrations.
- **web/** — React + TypeScript + Vite dashboard. Talks to the cloud over REST
  (WebSocket for live updates in a later phase).
- **agent/** — Python service run on-site (Dell OptiPlex / Ubuntu Server). Outbound
  only. Reuses 1.0's ping/HTTP/etc. check logic. Deployed to select sites.
- **legacy/** — the frozen 1.0 Home Assistant add-on (`git tag v1-final`).

## Two data sources → one dashboard

1. **UniFi Site Manager API** (`https://api.ui.com/v1`, `X-API-KEY`) — one key,
   all sites. `/sites`, `/devices`, `/isp-metrics`. Cloud-to-cloud, zero install.
   Identity is UniFi's own device id/MAC, so DHCP lease changes never matter.
2. **Local agents** — active custom checks UniFi doesn't do (arbitrary internal
   ping/HTTP/API/traceroute/bandwidth).

## Data model (Phase 0)

`account` → `user`, `site`; `site` → `device`, `isp_metric`; `unifi_credential`
(encrypted key) per account; `agent` per site. See `cloud/app/models/`.

## Security

- UniFi API key encrypted at rest with Fernet (`ENCRYPTION_KEY`); only the last 4
  chars stored in clear as a display hint.
- Agent auth: per-agent bearer token; only the hash is stored server-side.

## Local dev

`cp .env.example .env`, set `ENCRYPTION_KEY` (see the comment in `.env.example`),
then `docker compose up --build`. API on :8000 (`/docs`), web on :5173.

## Roadmap

Phase 0 foundations (this) → Phase 1 UniFi integration (live fleet view) →
Phase 2 local agent → Phase 3 UX/site-map → Phase 4 alerting → Phase 5 multi-user
polish + packaging. Full plan in the project doc `netmonitor-build-plan.md`.
