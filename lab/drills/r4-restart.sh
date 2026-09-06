#!/bin/bash
# R4.2 — same-name restart (0903 §7.2: pod name unchanged, new process). kill -9 pod-b mid-turn and start
# it again at once under the same pod id. Before the incarnation retrofit the successor's heartbeat made the
# predecessor's turn look owned-and-alive forever; now the turn is an orphan the moment the successor beats,
# and the successor's own ledger says who died of what (prev_oom_kills). usage: r4-restart.sh [restart_after=2]
set -u
cd "$(dirname "$0")/.."
AFTER=${1:-2}; WATCH=${2:-40}
LAB=~/.local/share/nimbus-lab; VC=$LAB/vcompute/leases; PY=$PWD/../.venv/bin/python
ts() { date '+%H:%M:%S'; }
steps() { for f in $(find "$VC" -name lab_steps.txt 2>/dev/null | sort); do printf '%s=%s ' "$(basename "$(dirname "$f")" | cut -c1-6)" "$(paste -sd, "$f" | sed 's/step-//g')"; done; }
orphan() { valkey-cli -p 6379 HMGET "orphan:$1" resolution detected_by epoch | paste -sd/ | tr -d '\n'; }
owner() { valkey-cli -p 6379 HMGET "turn:$1" pod inc epoch | paste -sd/ | tr -d '\n'; }
incs() { for p in a b; do printf '%s=%s ' "$p" "$(valkey-cli -p 6379 HGET "pod:$p" inc | tr -d '\n')"; done; }
curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null
find "$VC" -name lab_steps.txt -delete 2>/dev/null; ./labctl.py ledger reset >/dev/null; sleep 6  # let both pods beat again after the reset
echo "== $(ts) pod-b: lab steps 5 sleep 3; kill -9 at t+7s; start again ${AFTER}s later (same pod id). incarnations: $(incs)"
SID=$(curl -s -X POST 127.0.0.1:8001/api/v1/sessions -H 'Content-Type: application/json' -d '{"name":"r4-restart"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "   session=$SID"
curl -sN -X POST "127.0.0.1:8001/api/v1/sessions/$SID/chat" -H 'Content-Type: application/json' -d '{"content":"lab steps 5 sleep 3"}' > "$LAB/r4-restart-b.sse" 2>&1 &
curl -sN "127.0.0.1:8000/api/v1/sessions/$SID/events" > "$LAB/r4-restart-a.sse" 2>&1 & OBS=$!
sleep 7; echo "== $(ts) t+7s owner=$(owner "$SID") steps: $(steps)"; ./labctl.py kill b | head -1
sleep "$AFTER"; ./labctl.py pods up >/dev/null  # start b under the same id (+ wait for health, allow tools)
T0=$(date +%s); echo "== $(ts) pod-b started again: incarnations now $(incs)"
for i in $(seq 5 5 "$WATCH"); do sleep 5
  echo "   t+$(( $(date +%s) - T0 ))s owner=$(owner "$SID") orphan(res/by/epoch)=$(orphan "$SID") | steps: $(steps)"
done
kill $OBS 2>/dev/null
echo "== $(ts) stream tail:"; NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$SID" | tail -5
echo "== $(ts) journal (who saw the orphan):"; journalctl --user -u lab-nimbus@a -u lab-nimbus@b --since "$((WATCH + 30)) seconds ago" --no-pager -o short-precise | grep -E "ORPHAN|on_orphan|previous incarnation|oom_kill=" | cut -c1-200
echo "== $(ts) ledger pods:"; ./labctl.py ledger | python3 -c 'import sys,json;d=json.load(sys.stdin);print("  ",d["pods"])'
