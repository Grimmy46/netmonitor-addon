#!/bin/sh
# Build the staging SPA from ~/staging/src/web (default: prod's current web/).
set -eu
S=$HOME/staging
if [ "${FROM_PROD:-1}" = 1 ]; then rm -rf $S/src/web; git -c safe.directory=/opt/netmonitor -C /opt/netmonitor archive HEAD web | tar -x -C $S/src; fi
sudo -n docker run --rm -v $S/src/web:/app -w /app node:20-alpine sh -c "npm ci --no-audit --no-fund >/dev/null && VITE_API_BASE_URL=https://staging.rcs-fleet-mon.duckdns.org npm run build >/dev/null"
rm -rf $S/web; mkdir -p $S/web; cp -r $S/src/web/dist $S/web/dist
sudo -n docker compose -p nmstage restart web >/dev/null 2>&1 || true   # remount the new dist
echo web-built
