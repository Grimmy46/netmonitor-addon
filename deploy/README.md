# The closed deploy loop

Since 2026-08-06, NetMonitor ships itself. Nobody pastes anything.

```
Claude edits + commits (via the Cowork bridge, on Dawid's approval)
        │
        ▼   ≤ 60 s
Mac auto-push  ──────  launchd: com.rcs.netmonitor-autopush
  pushes v2 to GitHub using the Mac's keychain credentials
        │
        ▼   ≤ 60 s
Server auto-deploy ──  systemd: netmonitor-autodeploy.timer (rcs-hub)
  sees origin/v2 moved → git reset --hard → netmonitor-prod-setup.sh
        │
        ▼   ~2–3 min build
Live at https://rcs-fleet-mon.duckdns.org
  (kiosk agents then self-update payloads on their own check-ins)
```

## Where to look when curious

| What | Where |
|---|---|
| Mac push log | `~/Library/Logs/netmonitor-autopush.log` |
| Server deploy log | `/var/log/netmonitor-autodeploy.log` (on rcs-hub) |
| Is the timer alive? | `systemctl status netmonitor-autodeploy.timer` |
| Did it deploy? | dashboard header version badge changes |

## Kill switches

- Mac: `launchctl unload ~/Library/LaunchAgents/com.rcs.netmonitor-autopush.plist`
- Server: `sudo systemctl disable --now netmonitor-autodeploy.timer`

Both are safe to flip anytime; everything reverts to manual push/pull.

## Notes

- The deploy is gated on **commits**: nothing moves unless a commit lands on v2.
- `git reset --hard` on the server never touches untracked files (`.env`, `.dashpass`).
- Deploys are serialized with a lock; rapid pushes just collapse into the next run.
