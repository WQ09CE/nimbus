#!/bin/bash
# R1/R2 on the Temporal arm: same 5-step turn as a workflow; fault the worker that is
# running the current activity (kill | term | freeze[+thaw]) and watch what Temporal does.
# usage: r1-temporal.sh kill|term|freeze [SLEEP=3] [AT_S=7] [WATCH_S=30]
set -u
cd "$(dirname "$0")/.."
MODE=${1:?kill|term|freeze}; S=${2:-3}; AT=${3:-7}; WATCH=${4:-30}
LAB=~/.local/share/nimbus-lab; VC=$LAB/vcompute/leases; T=~/.local/bin/temporal; PY=$PWD/../.venv/bin/python
ts() { date '+%H:%M:%S'; }
steps() { for f in $(find "$VC" -name lab_steps.txt 2>/dev/null | sort); do printf '%s=%s ' "$(basename "$(dirname "$f")")" "$(paste -sd, "$f")"; done; }
pending() { "$T" workflow describe -w "$WID" --address 127.0.0.1:7233 -o json 2>/dev/null | python3 temporal/pending.py; }
watch() { local n=$1 T0=$(date +%s); for i in $(seq 5 5 "$n"); do sleep 5
  echo "   t+$(( $(date +%s) - T0 ))s workers: a=$(systemctl --user is-active lab-temporal-worker@a) b=$(systemctl --user is-active lab-temporal-worker@b) | $(pending) | steps: $(steps)"; done; }

if [ "${CLEAN:-0}" = 1 ]; then curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null; find "$VC" -name lab_steps.txt -delete 2>/dev/null; echo "== $(ts) cleaned leases"; fi
echo "== $(ts) [$MODE] Temporal arm: LabTurn steps=5 sleep=$S; fault the active worker at t+${AT}s"
"$PY" temporal/run_turn.py 5 "$S" > "$LAB/r1t-$MODE.out" 2>&1 &
RUN=$!
sleep 2; WID=$(grep -oE 'lab-turn-[0-9]+' "$LAB/r1t-$MODE.out" | head -1); echo "   workflow=$WID"
sleep $((AT-2))
echo "== $(ts) t+${AT}s $(pending) | steps: $(steps)"
VICTIM=$(pending | grep -oE 'worker=worker-[ab]' | cut -d= -f2); VICTIM=${VICTIM:-worker-a}
echo "== $(ts) fault $MODE -> $VICTIM"
case $MODE in kill) ./labctl.py kill "$VICTIM";; term) ./labctl.py term "$VICTIM";; freeze) ./labctl.py freeze "$VICTIM";; esac | head -1
watch "$WATCH"
if [ "$MODE" = freeze ]; then
  echo "== $(ts) THAW $VICTIM (zombie worker wakes up mid-activity)"; ./labctl.py thaw "$VICTIM" | head -1
  watch 15
  echo "== $(ts) zombie worker journal (what happened to its late completion):"
  journalctl --user -u "lab-temporal-worker@${VICTIM#worker-}" --since '2 min ago' --no-pager 2>/dev/null | grep -iE 'not found|token|complet|fail|error|warn' | tail -5 | cut -c1-200
fi
wait $RUN 2>/dev/null
echo "== $(ts) run_turn trace:"; sed 's/^/   /' "$LAB/r1t-$MODE.out" | cut -c1-200
echo "== $(ts) history summary:"; "$T" workflow show -w "$WID" --address 127.0.0.1:7233 -o json 2>/dev/null | python3 -c '
import sys,json,collections
d=json.load(sys.stdin); ev=d.get("events",[]); c=collections.Counter(e.get("eventType","?").replace("EVENT_TYPE_","") for e in ev)
print("   " + ", ".join(f"{k}={v}" for k,v in sorted(c.items()) if "ACTIVITY" in k or "WORKFLOW_EXECUTION" in k))'
echo "== $(ts) leases/workspaces: $(steps)"
echo "== $(ts) reset workers"; systemctl --user restart lab-temporal-worker@a lab-temporal-worker@b; sleep 2; ./labctl.py workers
