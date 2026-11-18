# NetMon staging (rcs-hub-01, ~/staging)

Isolated copy of prod for testing bots against real data. Compose project
`nmstage`, internal-only network (no internet), its own Postgres and a fresh
ENCRYPTION_KEY, so prod secrets can't decrypt and nothing can reach Signal,
UniFi, web push or RCS Inventory. The `mock` service answers as both
signal-cli (`http://signal:8080`) and the Teardown Bot API (`TEARDOWN_BOT_BASE`)
and logs every call to `mock/calls.jsonl`.

- `setup.sh`: build or refresh (`RESEED=1` re-copies the prod DB, minus raw
  ping samples, and scrubs stored keys).
- `run_replay.sh`: copies the bot code in, resets the mock, replays
  `replay/msgs.json` (+ `drills.json`) and writes `replay/out.json`.
- Data files (`msgs.json`, `truth.json`, `drills.json`, `mock/inventory.json`)
  hold real chat and inventory data and stay on the server, never in this repo.
