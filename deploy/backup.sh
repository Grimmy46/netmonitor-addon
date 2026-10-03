#!/usr/bin/env bash
# NetMonitor nightly database backup — runs on rcs-hub via systemd timer
# (netmonitor-backup.timer, installed by netmonitor-prod-setup.sh).
#
# Writes a compressed pg_dump (custom format) to /var/backups/netmonitor and
# keeps the newest $KEEP. Raw per-second kiosk pings (ping_samples) are left
# out: they are pruned after 14 days anyway and would make the dump ~100x
# bigger. Their per-minute/per-hour rollups ARE included.
#
# These files are also captured by the Hetzner server backups, which gives
# an off-server copy. Restore:
#   docker compose -f docker-compose.prod.yml exec -T db \
#     sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
#     < /var/backups/netmonitor/netmonitor-YYYYMMDD-HHMM.dump
set -euo pipefail
DIR=/var/backups/netmonitor
KEEP=7
LOG=/var/log/netmonitor-backup.log
cd /opt/netmonitor

mkdir -p "$DIR"; chmod 700 "$DIR"
ts=$(date -u +%Y%m%d-%H%M)
out="$DIR/netmonitor-$ts.dump"
tmp="$out.part"

{
  echo "== $(date -Is) backup starting"
  docker compose -f docker-compose.prod.yml exec -T db \
    sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --exclude-table-data=ping_samples' \
    > "$tmp"
  size=$(stat -c%s "$tmp")
  # A healthy dump is tens of MB; anything tiny means pg_dump failed quietly.
  if [ "$size" -lt 100000 ]; then
    echo "!! dump is only $size bytes — keeping previous backups, failing"
    rm -f "$tmp"; exit 1
  fi
  mv "$tmp" "$out"
  ln -sfn "$out" "$DIR/latest.dump"
  ls -1t "$DIR"/netmonitor-*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -f
  echo "== $(date -Is) backup ok: $out ($((size / 1024 / 1024)) MB), kept $(ls -1 "$DIR"/netmonitor-*.dump | wc -l)"
} >>"$LOG" 2>&1
