# NetMonitor 2.0

Multi-site network operations dashboard. Aggregates network health across an
entire UniFi fleet via the official **UniFi Site Manager API**, with optional
**local agents** for active custom testing of anything UniFi doesn't cover.

> **1.0 (the Home Assistant add-on) lives on** in [`/legacy`](./legacy) and is
> tagged `v1-final`. 2.0 is a ground-up rebuild.

## Architecture

Two data sources feed one cloud dashboard:

1. **UniFi Site Manager API** (`https://api.ui.com/v1`) — cloud-to-cloud. One API
   key covers every site: inventory (`/sites`, `/devices`), WAN/ISP health
   (`/isp-metrics`). Zero on-site install. *This is the MVP.*
2. **Local agents** — a small Python service (reusing 1.0's proven test logic) that
   runs on-site (Dell OptiPlex / Ubuntu Server) and pushes active test results
   outbound to the cloud. Deployed to select sites only.

```
  UniFi Site Manager API ─┐
                          ├─▶  cloud (FastAPI + Postgres)  ─▶  web (React + TS)
  Local agent(s) ─────────┘
```

## Repo layout

| Path | What |
|---|---|
| `/cloud` | FastAPI backend + Postgres (SQLAlchemy, Alembic) |
| `/web`   | React + TypeScript + Vite dashboard |
| `/agent` | Local monitoring agent (Python) |
| `/legacy`| The original 1.0 Home Assistant add-on (frozen) |
| `/docs`  | Design docs |
| `docker-compose.yml` | Boots postgres + cloud + web for local dev |

## Quick start (local dev)

```bash
cp .env.example .env        # fill in secrets (never commit .env)
docker compose up --build
# web:   http://localhost:5173
# api:   http://localhost:8000  (docs at /docs)
```

## Status

Phase 0 — foundations/scaffold. See `docs/` and the project docs for the full
build plan (Phase 1 = UniFi integration, Phase 2 = local agent, …).

## Security

The UniFi API key is an **account-wide admin credential**. It is entered in the
app and stored **encrypted at rest** — never in source control, never in logs.
