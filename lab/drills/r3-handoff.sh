#!/bin/bash
# R3.3 — graceful handoff: SIGTERM pod-a mid-turn. Expected: pod-a pauses at the next step seam
# (binding taken), announces on NATS, exits; pod-b consumes and resumes the turn on the restored lease.
# usage: r3-handoff.sh [SLEEP=3] [AT_S=7] [WATCH_S=40]
set -u
cd "$(dirname "$0")/.."
S=${1:-3}; AT=${2:-7}; WATCH=${3:-40}
LAB=~/.local/share/nimbus-lab; VC=$LAB/vcompute/leases; PY=$PWD/../.venv/bin/python
ts() { date '+%H:%M:%S'; }
steps() { for f in $(find "$VC" -name lab_steps.txt 2>/dev/null | sort); do printf '%s=%s ' "$(basename "$(dirname "$f")")" "$(paste -sd, "$f")"; done; }
dump() { NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$SID"; }
status_b() { curl -s "127.0.0.1:8001/api/v1/sessions/$SID/status" | grep -oE '"running":[a-z]+,"status":"[a-z]+"'; }
./labctl.py repeat keyed >/dev/null
curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null; find "$VC" -name lab_steps.txt -delete 2>/dev/null; ./labctl.py ledger reset >/dev/null
echo "== $(ts) pod-a: lab steps 5 sleep $S; SIGTERM pod-a at t+${AT}s (mid step 3)"
SID=$(curl -s -X POST 127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -d '{"name":"r3-handoff"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "   session=$SID"
curl -sN -X POST "127.0.0.1:8000/api/v1/sessions/$SID/chat" -H 'Content-Type: application/json' -d "{\"content\":\"lab steps 5 sleep $S\"}" > "$LAB/r3h-a.sse" 2>&1 &
CURL=$!
curl -sN "127.0.0.1:8001/api/v1/sessions/$SID/events" > "$LAB/r3h-b.sse" 2>&1 &
OBS=$!
sleep "$AT"; echo "== $(ts) t+${AT}s steps: $(steps)"; ./labctl.py term a | head -1
T0=$(date +%s)
for i in $(seq 4 4 "$WATCH"); do sleep 4
  echo "   t+$(( $(date +%s) - T0 ))s pod-a=$(systemctl --user is-active lab-nimbus@a) | log-tail=$(dump | tail -1 | awk '{print $2, $3, $4, $5}') | pod-b: $(status_b) | steps: $(steps)"
done
kill $CURL $OBS 2>/dev/null
echo "== $(ts) pod-a client saw: $(grep -oE '^event: [a-z_]+' "$LAB/r3h-a.sse" | tail -3 | paste -sd' ')"
echo "== $(ts) pod-a log (handoff lines):"; grep -E 'pause_all|handoff|graceful|Paused|PAUSED|bound at seam|Clean seam' ~/.local/share/nimbus-lab/pods/a/.logs/nimbus.log | tail -6 | cut -c1-170
echo "== $(ts) pod-b log (handoff lines):"; grep -E 'handoff|resumed|restore' ~/.local/share/nimbus-lab/pods/b/.logs/nimbus.log | tail -5 | cut -c1-170
echo "== $(ts) session stream tail:"; dump | grep -E 'turn/|LAB_DONE|tool/result' | tail -8
echo "== $(ts) pod-b observer events: $(grep -oE '^event: [a-z_]+' "$LAB/r3h-b.sse" | sort | uniq -c | sort -rn | tr '\n' ' ')"
echo "== $(ts) NATS stream: $(curl -s 127.0.0.1:8222/jsz?streams=true | python3 -c 'import sys,json;d=json.load(sys.stdin);print([ (s["name"], s["state"]["messages"], s["state"]["consumer_count"]) for a in d.get("account_details",[]) for s in a.get("stream_detail",[])])' 2>/dev/null)"
echo "== $(ts) workspaces: $(steps)"
echo "== $(ts) reset pod-a"; ./labctl.py pods up | grep -E '^a '
