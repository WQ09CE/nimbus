#!/bin/bash
# R1 — death morphology on pod-a mid-turn (step 3 of 5 in flight), baseline nimbus (no retrofit).
#   kill   : SIGKILL the pod cgroup            (R1.1)
#   term   : SIGTERM = graceful shutdown path  (R1.2)
#   freeze : SIGSTOP, hand session to pod-b, then SIGCONT — zombie writer wakes up (R1.3)
# usage: r1-death.sh kill|term|freeze [SLEEP_PER_STEP=3] [AT_S=7] [WATCH_S=20]
set -u
cd "$(dirname "$0")/.."
MODE=${1:?kill|term|freeze}; S=${2:-3}; AT=${3:-7}; WATCH=${4:-20}
LAB=~/.local/share/nimbus-lab; SESS=$LAB/sessions; VC=$LAB/vcompute/leases
ts() { date '+%H:%M:%S'; }
steps() { for f in $(find "$VC" -name lab_steps.txt 2>/dev/null | sort); do printf '%s=%s ' "$(basename "$(dirname "$f")")" "$(paste -sd, "$f")"; done; }
vc() { curl -s 127.0.0.1:8793/v1/health; }
PY=$PWD/../.venv/bin/python
STREAM=$(grep -q 'NIMBUS_LOG_STORE=valkey' units/lab-nimbus@.service && echo 1 || echo 0)
lastev() { if [ "$STREAM" = 1 ]; then NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$1" | tail -1 | awk '{print $2}'; else tail -1 "$SESS/$1.jsonl" | grep -oE '"type": "[^"]+"' | cut -d'"' -f4; fi; }
rejected() { valkey-cli -p 6379 GET ledger:rejected_writes | tr -d '\n'; }
watch() { local n=$1 T0=$(date +%s); for i in $(seq 5 5 "$n"); do sleep 5
  echo "   t+$(( $(date +%s) - T0 ))s pod-a=$(systemctl --user is-active lab-nimbus@a) vc=$(vc) steps: $(steps)| log-tail=$(lastev "$SID") rej=$(rejected) | pod-b view: $(curl -s "127.0.0.1:8001/api/v1/sessions/$SID/status" | grep -oE '"running":[a-z]+,"status":"[a-z]+"') | ledger: $(ledger)"; done; }
ledger() { valkey-cli -p 6379 --no-raw EXISTS "pod:a" | tr -d '\n' | sed 's/(integer) 1/pod-a alive/; s/(integer) 0/pod-a DEAD/'; o=$(valkey-cli -p 6379 HGET "orphan:$SID" detected_by); [ -n "$o" ] && printf ' ORPHAN(by %s)' "$o" || printf ' no-orphan'; }
seqdump() { if [ "$STREAM" = 1 ]; then NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$SID" | tail -"$1"; NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$SID" | awk '{c[$1]++} END{d=""; for(k in c) if(c[k]>1) d=d" "k; print "DUPLICATE seq:" (d==""?" none":d) " | total lines: " NR}'; echo "rejected_writes (global counter): $(rejected)"; return; fi
python3 - "$SESS/$SID.jsonl" "$1" <<'PY'
import json,sys,collections
seen=collections.Counter(); rows=[]
for line in open(sys.argv[1]):
    e=json.loads(line); d=e.get("data",{}); m=d.get("message",{}) if isinstance(d,dict) else {}
    seen[e["seq"]]+=1; x=""
    if e["type"] in("turn/end","step/end"): x=json.dumps({k:v for k,v in d.items() if k in("reason","turn","step","synthetic")})
    elif e["type"]=="tool/result":
        meta=m.get("meta") or {}; x=(m.get("content") or "")[:60].replace("\n"," ")+" "+str(meta.get("code") or (meta.get("ui_detail") or {}).get("lease_id",""))
    elif e["type"] in("assistant/message","user/message"): x=(m.get("content") or "")[:36]
    rows.append(f'{e["seq"]:>3} {e["type"]:<18} {x}')
print("\n".join(rows[-int(sys.argv[2]):]))
d=sorted(s for s,c in seen.items() if c>1); print("DUPLICATE seq:", d if d else "none", "| total lines:", sum(seen.values()))
PY
}

if [ "${CLEAN:-0}" = 1 ]; then  # CLEAN=1: drop old leases + ledger records so the run's evidence stands alone
  curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null
  find "$VC" -name lab_steps.txt -delete 2>/dev/null; ./labctl.py ledger reset >/dev/null; echo "== $(ts) cleaned leases + ledger"
fi
echo "== $(ts) [$MODE] pod-a mock rail, fresh session; turn: lab steps 5 sleep $S; fault at t+${AT}s"
./labctl.py allow a >/dev/null
SID=$(curl -s -X POST 127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -d "{\"name\":\"r1-$MODE\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "   session=$SID"
curl -sN -X POST "127.0.0.1:8000/api/v1/sessions/$SID/chat" -H 'Content-Type: application/json' -d "{\"content\":\"lab steps 5 sleep $S\"}" > "$LAB/r1-$MODE-a.sse" 2>&1 &
CURL=$!
sleep "$AT"
echo "== $(ts) t+${AT}s steps: $(steps)| last event: $(lastev "$SID")"
case $MODE in kill) ./labctl.py kill a | head -1;; term) ./labctl.py term a | head -1;; freeze) ./labctl.py freeze a | head -1;; esac
echo "== $(ts) watching ${WATCH}s"
watch "$WATCH"
echo "== $(ts) pod-a client stream last event: $(grep -oE '^event: .*' "$LAB/r1-$MODE-a.sse" | tail -1)   (curl alive: $(kill -0 $CURL 2>/dev/null && echo yes || echo no))"
echo "== $(ts) hand session to pod-b: 'lab steps 2 sleep 1'"
./labctl.py turn b "lab steps 2 sleep 1" --session "$SID" --timeout 60 | grep -E 'tool_result|done|finished|ended' | cut -c1-120
if [ "$MODE" = freeze ]; then
  echo "== $(ts) THAW pod-a (zombie wakes up with step 3 in flight)"; ./labctl.py thaw a | head -1
  watch 15
  echo "== $(ts) pod-a client stream after thaw: $(grep -oE '^event: .*' "$LAB/r1-$MODE-a.sse" | tail -1)"
fi
kill $CURL 2>/dev/null
echo "== $(ts) session log (tail) + duplicate-seq check"; seqdump 30
echo "== $(ts) leases: $(vc) workspaces: $(steps)"
echo "== $(ts) reset pod-a"; systemctl --user restart lab-nimbus@a; sleep 3; ./labctl.py pods up | grep -E '^a '
