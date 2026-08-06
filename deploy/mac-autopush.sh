#!/bin/bash
# NetMonitor auto-push — runs on Dawid's Mac every minute via launchd.
# If the local v2 branch has commits that origin doesn't (i.e. Claude committed
# a change via the Cowork bridge), push them using the keychain credentials.
# Log: ~/Library/Logs/netmonitor-autopush.log
# Disable: launchctl unload ~/Library/LaunchAgents/com.rcs.netmonitor-autopush.plist
REPO="$HOME/netmonitor-addon"
LOG="$HOME/Library/Logs/netmonitor-autopush.log"
cd "$REPO" || exit 0

# Nothing new? Done. (origin/v2 only advances when a push/fetch succeeds.)
AHEAD=$(git rev-list --count origin/v2..v2 2>/dev/null || echo 0)
[ "$AHEAD" = "0" ] && exit 0

# Clear stale git locks the bridge sometimes leaves behind (only if >2 min old).
find .git -maxdepth 1 -name '*.lock' -mmin +2 -delete 2>/dev/null

echo "== $(date '+%F %T') pushing $AHEAD commit(s)" >>"$LOG"
git push origin v2 >>"$LOG" 2>&1
