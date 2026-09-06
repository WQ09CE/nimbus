#!/bin/bash
# R4.3 — event-loop stall (0903 §1.3/§7.5: the pod was alive but everything on it took 10 s; a
# pod that cannot beat is dead to the ledger). pod-a runs a turn whose model call blocks the event
# loop for STALL seconds (MockLLM `stall`): the ledger sees no heartbeat, pod-b takes the turn over
# (a false death — fenced by the epoch, so harmless to the log), pod-a wakes up and confesses the
# stall in its next heartbeat (lag_ms). STALL < dead_after shows the precursor without the takeover.
# usage: r4-stall.sh [stall=20] [watch=45]
set -u
cd "$(dirname "$0")/.."
STALL=${1:-20}; WATCH=${2:-45}
LAB=~/.local/share/nimbus-lab; VC=$LAB/vcompute/leases; PY=$PWD/../.venv/bin/python
ts() { date '+%H:%M:%S'; }
steps() { for f in $(find "$VC" -name lab_steps.txt 2>/dev/null | sort); do printf '%s=%s ' "$(basename "$(dirname "$f")" | cut -c1-6)" "$(paste -sd, "$f" | sed 's/step-//g')"; done; }
orphan() { valkey-cli -p 6379 HGET "orphan:$1" resolution | tr -d '\n'; }
owner() { valkey-cli -p 6379 HMGET "turn:$1" pod epoch | paste -sd/ | tr -d '\n'; }
podfacts() { valkey-cli -p 6379 HMGET "podlast:a" last lag_ms gc2_ms | paste -sd' ' | awk -v now="$(date +%s.%N)" '{printf "age=%.1fs lag=%sms gc2=%sms", now-$1, $2, $3}'; }
health() { local t0=$(date +%s%N); if curl -s -m 8 127.0.0.1:8000/api/v1/health >/dev/null; then echo "$(( ($(date +%s%N) - t0) / 1000000 ))ms"; else echo "TIMEOUT"; fi; }
curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null
find "$VC" -name lab_steps.txt -delete 2>/dev/null; ./labctl.py ledger reset >/dev/null; sleep 6
"$PY" probe.py --tag r4-stall --pods a,b >/dev/null 2>&1 & PROBE=$!
echo "== $(ts) pod-a: lab steps 3 sleep 1 stall $STALL (every model call blocks the loop ${STALL}s; ledger dead_after=15s)"
SID=$(curl -s -X POST 127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -d '{"name":"r4-stall"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "   session=$SID"
curl -sN -X POST "127.0.0.1:8000/api/v1/sessions/$SID/chat" -H 'Content-Type: application/json' -d "{\"content\":\"lab steps 3 sleep 1 stall $STALL\"}" > "$LAB/r4-stall-a.sse" 2>&1 & CHAT=$!
curl -sN "127.0.0.1:8001/api/v1/sessions/$SID/events" > "$LAB/r4-stall-b.sse" 2>&1 & OBS=$!
T0=$(date +%s)
for i in $(seq 1 "$((WATCH / 3))"); do sleep 3
  echo "   t+$(( $(date +%s) - T0 ))s pod-a $(podfacts) health=$(health) | owner=$(owner "$SID") orphan=$(orphan "$SID") | steps: $(steps)"
done
kill $PROBE $OBS 2>/dev/null; sleep 1; kill $CHAT 2>/dev/null
echo "== $(ts) pod-a client saw: $(grep -oE '^event: [a-z_]+' "$LAB/r4-stall-a.sse" | sort | uniq -c | sort -rn | tr '\n' ' ')"
grep -A1 -E '^event: (done|interrupted)' "$LAB/r4-stall-a.sse" | grep -E '^data' | cut -c1-120 | tail -2
echo "== $(ts) pod-b observer saw: $(grep -oE '^event: [a-z_]+' "$LAB/r4-stall-b.sse" | sort | uniq -c | sort -rn | tr '\n' ' ')"
grep -A1 -E '^event: (done|interrupted|resume_replay)' "$LAB/r4-stall-b.sse" | grep -E '^data' | cut -c1-120 | tail -3
echo "== $(ts) stream tail:"; NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$SID" | tail -8
echo "== $(ts) ledger:"; ./labctl.py ledger | python3 -c 'import sys,json;d=json.load(sys.stdin);print("   pods:",d["pods"]);print("   orphans:",json.dumps(d["orphans"])[:400]);print("   rejected_writes:",d["rejected_writes"])'
echo "== $(ts) probe (pod-a hb_age / health_ms per second): $LAB/probe/r4-stall.csv"
python3 - "$LAB/probe/r4-stall.csv" <<'EOF'
import csv, sys
rows = [r for r in csv.DictReader(open(sys.argv[1])) if r["pod"] == "a"]
print("   " + " ".join(f"{float(r['t']):.0f}:{r['hb_age_s'] or '-'}/{r['health_ms']}" for r in rows))
EOF
