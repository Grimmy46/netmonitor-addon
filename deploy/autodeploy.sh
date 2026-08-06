#!/usr/bin/env bash
# NetMonitor auto-deploy — runs on rcs-hub every minute via systemd timer.
# If origin/v2 has new commits, pull them and run the idempotent setup script.
# Log: /var/log/netmonitor-autodeploy.log   Disable: systemctl disable --now netmonitor-autodeploy.timer
set -uo pipefail
LOG=/var/log/netmonitor-autodeploy.log
cd /opt/netmonitor || exit 1

# Never overlap two deploys.
exec 9>/var/lock/netmonitor-autodeploy.lock
flock -n 9 || exit 0

git fetch origin v2 --quiet 2>>"$LOG" || exit 0
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/v2)
[ "$LOCAL" = "$REMOTE" ] && exit 0

{
  echo "== $(date -Is) deploying ${LOCAL:0:7} -> ${REMOTE:0:7}"
  git reset --hard origin/v2          # untracked files (.env, .dashpass) are untouched
  bash netmonitor-prod-setup.sh
  echo "== $(date -Is) deploy finished"
} >>"$LOG" 2>&1
