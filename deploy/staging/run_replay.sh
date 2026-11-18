#!/bin/sh
# Copy current code into staging cloud, reset the mock, replay. Output: replay/out.json
set -eu
S=$HOME/staging; C=nmstage-cloud-1
rm -f $S/mock/state.json $S/mock/calls.jsonl
sudo -n docker restart nmstage-mock-1 >/dev/null
for f in teardownbot.py teardown_match.py; do sudo -n docker cp $S/src/cloud/app/services/$f $C:/app/app/services/$f; done
sudo -n docker exec $C mkdir -p /replay
for f in replay.py msgs.json truth.json drills.json; do sudo -n docker cp $S/replay/$f $C:/replay/$f; done
sudo -n docker exec -w /app $C env PYTHONPATH=/app timeout 200 python /replay/replay.py > $S/replay/out.json
rm -f $S/mock/state.json; sudo -n docker exec -w /app $C env DRILLS=1 PYTHONPATH=/app timeout 100 python /replay/replay.py > $S/replay/drills_out.json
echo replay-ok $(wc -c < $S/replay/out.json)
