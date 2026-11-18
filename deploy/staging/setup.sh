#!/bin/sh
# One-time staging build. Safe to re-run (keeps .env and DB unless RESEED=1).
set -eu
S=$HOME/staging; cd $S
rm -rf src; mkdir src; git -c safe.directory=/opt/netmonitor -C /opt/netmonitor archive HEAD cloud | tar -x -C src
sed -i 's|^BASE = "https://yprisdgqxmwvzadaanpk.supabase.co/functions/v1/teardown-bot"|import os\nBASE = os.environ.get("TEARDOWN_BOT_BASE", "https://yprisdgqxmwvzadaanpk.supabase.co/functions/v1/teardown-bot")|' src/cloud/app/services/teardownbot.py
grep -q TEARDOWN_BOT_BASE src/cloud/app/services/teardownbot.py
if [ ! -f .env ]; then
  umask 077
  { echo POSTGRES_USER=stage; echo POSTGRES_DB=netmon_stage
    echo POSTGRES_PASSWORD=$(openssl rand -hex 16); echo SECRET_KEY=$(openssl rand -hex 32)
    echo ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_'); echo CORS_ORIGINS=http://localhost; } > .env
fi
D="sudo -n docker compose -p nmstage"
$D up -d db mock
i=0; until sudo -n docker exec nmstage-db-1 pg_isready -U stage >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 60 ] || exit 1; sleep 2; done
if [ "${RESEED:-0}" = 1 ] || [ "$(sudo -n docker exec nmstage-db-1 psql -U stage -d netmon_stage -Atc "select count(*) from information_schema.tables where table_name='accounts'")" = 0 ]; then
  sudo -n docker exec nmstage-db-1 psql -U stage -d postgres -qc "drop database if exists netmon_stage" -c "create database netmon_stage owner stage"
  sudo -n docker exec netmonitor-db-1 sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --no-owner --no-acl --exclude-table-data=ping_samples --exclude-table-data=probe_samples' \
    | sudo -n docker exec -i nmstage-db-1 pg_restore -U stage -d netmon_stage --no-owner --no-acl
  # scrub: prod secrets can't decrypt here anyway (new ENCRYPTION_KEY), but drop them outright
  sudo -n docker exec -i nmstage-db-1 psql -U stage -d netmon_stage -q <<'SQL'
delete from unifi_credentials;
update signal_config set teardown = coalesce(teardown,'{}'::jsonb) - 'token_enc';
delete from push_subscriptions;
update unifi_consoles set encrypted_api_key = '';
update cameras set password_enc = null;
SQL
  echo seeded
fi
$D up -d --build cloud
echo up
