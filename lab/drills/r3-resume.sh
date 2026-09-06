#!/bin/bash
# R3.1 — first-tier resume: kill pod-a mid-turn; the ledger's orphan scanner on pod-b decides by the
# in-flight call's repeat class. keyed → pod-b re-executes the graded call and finishes the turn on
# its own (no user message); once → fast-fail (turn closed, client told). usage: r3-resume.sh keyed|once
set -u
cd "$(dirname "$0")/.."
CLS=${1:-keyed}; S=${2:-3}; AT=${3:-7}; WATCH=${4:-45}
LAB=~/.local/share/nimbus-lab; VC=$LAB/vcompute/leases; PY=$PWD/../.venv/bin/python
ts() { date '+%H:%M:%S'; }
steps() { for f in $(find "$VC" -name lab_steps.txt 2>/dev/null | sort); do printf '%s=%s ' "$(basename "$(dirname "$f")")" "$(paste -sd, "$f")"; done; }
dump() { NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$SID"; }
orphan() { valkey-cli -p 6379 HGET "orphan:$SID" resolution | tr -d '\n'; }
./labctl.py repeat "$CLS" | grep -c restarted >/dev/null
curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null; find "$VC" -name lab_steps.txt -delete 2>/dev/null; ./labctl.py ledger reset >/dev/null
echo "== $(ts) [repeat=$CLS] pod-a: lab steps 5 sleep $S; kill -9 at t+${AT}s; pod-b's scanner decides"
SID=$(curl -s -X POST 127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -d "{\"name\":\"r3-$CLS\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "   session=$SID"
curl -sN -X POST "127.0.0.1:8000/api/v1/sessions/$SID/chat" -H 'Content-Type: application/json' -d "{\"content\":\"lab steps 5 sleep $S\"}" > "$LAB/r3-$CLS-a.sse" 2>&1 &
CURL=$!
# attach an observer to pod-b's SSE for this session (it will carry the resumed turn)
curl -sN "127.0.0.1:8001/api/v1/sessions/$SID/events" > "$LAB/r3-$CLS-b.sse" 2>&1 &
OBS=$!
sleep "$AT"; echo "== $(ts) t+${AT}s steps: $(steps)"; ./labctl.py kill a | head -1; kill $CURL 2>/dev/null
T0=$(date +%s)
for i in $(seq 5 5 "$WATCH"); do sleep 5
  echo "   t+$(( $(date +%s) - T0 ))s orphan=$(orphan) | log-tail=$(dump | tail -1 | awk '{print $2, $3, $4}') | steps: $(steps)"
done
kill $OBS 2>/dev/null
echo "== $(ts) session stream (since the kill):"; dump | awk 'f||/turn\/end/{f=1} f' | head -40
echo "== $(ts) pod-b observer events: $(grep -oE '^event: [a-z_]+' "$LAB/r3-$CLS-b.sse" | sort | uniq -c | sort -rn | tr '\n' ' ')"
grep -A1 -E '^event: (done|interrupted|resume_replay)' "$LAB/r3-$CLS-b.sse" | grep -E '^data' | cut -c1-140 | tail -4
echo "== $(ts) ledger:"; ./labctl.py ledger | python3 -c 'import sys,json;d=json.load(sys.stdin);print("   orphans:",d["orphans"]);print("   rejected_writes:",d["rejected_writes"])'
echo "== $(ts) binding in session metadata: $(python3 -c "import json;d=json.load(open('$LAB/sessions/$SID.json'));b=d.get('metadata',{}).get('sandbox_binding');print(b and {k:b[k] for k in ('snapshot_id','lease_id') if k in b} or 'none')")"
echo "== $(ts) workspaces: $(steps)"
echo "== $(ts) reset pod-a"; ./labctl.py pods up | grep -E '^a '
