#!/usr/bin/env bash
# NetMonitor 2.0 — one-shot production bring-up on the Hetzner hub.
# Run from /opt/netmonitor as: sudo bash netmonitor-prod-setup.sh
set -euo pipefail
APP=/opt/netmonitor
DOMAIN=rcs-fleet-mon.duckdns.org
cd "$APP"
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

echo "== free RAM: retire the demo ping-hub (folds in later as a module) =="
systemctl disable --now rcs-agent rcs-monitor 2>/dev/null || true

echo "== Docker =="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

echo "== production compose =="
cat > docker-compose.prod.yml <<'YML'
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes: [ pgdata:/var/lib/postgresql/data ]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped
  cloud:
    build: ./cloud
    depends_on:
      db:
        condition: service_healthy
    env_file: .env
    environment:
      POSTGRES_HOST: db
      POSTGRES_PORT: "5432"
    ports: [ "127.0.0.1:8010:8000" ]
    command: sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"
    restart: unless-stopped
volumes:
  pgdata:
YML

echo "== secrets (.env generated once; never overwritten) =="
if [ ! -f .env ]; then
  cat > .env <<ENV
POSTGRES_USER=netmonitor
POSTGRES_PASSWORD=$(openssl rand -hex 16)
POSTGRES_DB=netmonitor
SECRET_KEY=$(openssl rand -hex 32)
ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')
CORS_ORIGINS=https://$DOMAIN
ENV
  chmod 600 .env
fi

echo "== dashboard password (basic-auth at the edge) =="
if [ ! -f .dashpass ]; then
  echo "FATAL: /opt/netmonitor/.dashpass is missing — refusing to silently generate a new dashboard password." >&2
  echo "Create it first:  sudo bash -c 'read -s -p \"Password: \" P; echo; printf \"%s\" \"$P\" > .dashpass; chmod 600 .dashpass'" >&2
  exit 1
fi
DASHPASS=$(cat .dashpass)
HASH=$(caddy hash-password --plaintext "$DASHPASS")

echo "== build the React app to static (same-origin API) =="
docker run --rm -v "$APP/web":/app -w /app node:20-alpine \
  sh -c "npm ci && VITE_API_BASE_URL=https://$DOMAIN npm run build"

echo "== start db + cloud (runs DB migrations) =="
docker compose -f docker-compose.prod.yml up -d --build

echo "== Caddy: basic-auth, serve the SPA, proxy the API =="
cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    encode zstd gzip

    # Site agents hit these with their own X-Agent-Token (verified at the app
    # layer): report (push samples), version + payload (self-update). They are
    # exempt from the dashboard basic-auth since agents can't do interactive
    # auth. Everything else — including the agent MANAGEMENT endpoints — stays
    # behind basic-auth.
    @agentapi path /agents/report /agents/payload /agents/version /agents/enroll/* /agents/targets /agents/device-report
    handle @agentapi {
        reverse_proxy 127.0.0.1:8010
    }

    handle {
        basic_auth {
            admin $HASH
        }
        @api path /health* /sites* /integrations* /map* /agents* /docs* /openapi.json /redoc*
        handle @api {
            reverse_proxy 127.0.0.1:8010
        }
        handle {
            root * $APP/web/dist
            try_files {path} /index.html
            file_server
        }
    }
}
CADDY
systemctl reload caddy

echo
echo "===================== DONE ====================="
echo " NetMonitor is live at:  https://$DOMAIN"
echo " Login:  admin  /  $DASHPASS"
echo " Then open Settings and paste a valid UniFi owner key."
echo "================================================"
