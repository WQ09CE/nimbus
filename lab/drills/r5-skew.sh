#!/bin/bash
# R5.2 — contract skew across a rollout. The running fleet d,e,f is generation 2 on the lab branch
# `lab/contract-v2` (~/Projects/nimbus-v2 via PYTHONPATH: session-log contract 2, tool results written as
# `tool/result.v2`). A ROLLBACK rolls generation 3 = the v1 code in this tree onto a,b,c (surge order) while
# 20 sessions are mid-turn, so v1 pods receive handoffs of sessions written under contract 2.
# Before the reader gate: v1 cannot see the v2 tool results and re-executes the turn from step 1.
# After the gate: v1 refuses (`interrupted reason=contract_newer`), the announcement waits for a capable pod.
# FAULT=term (default, graceful: pause snapshot + handoff) or FAULT=kill (kill -9: crash path, the v1 scanner
# grades the v2 log). PROMPT= overrides the turn (a turn longer than the rollout leaves v2 sessions on the last
# v2 pod when it goes: those strand; ROLLFORWARD=1 then brings a v2 pod back to drain them).
# usage: [FAULT=term|kill] [PROMPT="lab steps 8 sleep 12"] r5-skew.sh [sessions=20] [gap=10]
set -u
cd "$(dirname "$0")/.."
N=${1:-20}; GAP=${2:-10}; PROMPT=${PROMPT:-"lab steps 7 sleep 8"}; FAULT=${FAULT:-term}
V2=$HOME/Projects/nimbus-v2/src
LAB=~/.local/share/nimbus-lab; VC=$LAB/vcompute/leases; PY=$PWD/../.venv/bin/python
ts() { date '+%H:%M:%S'; }
act() { systemctl --user is-active "lab-nimbus@$1" 2>/dev/null; }
ver() { curl -s -m 2 "127.0.0.1:$((8000 + $(printf '%d' "'$1") - 97))/api/v1/health" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("version","?"))' 2>/dev/null || echo "-"; }
fleet() { for p in a b c d e f; do printf '%s=%s/%s ' "$p" "$(act "$p" | cut -c1-3)" "$(ver "$p")"; done; }
owners() { valkey-cli -p 6379 --scan --pattern 'turn:*' | while read -r k; do valkey-cli -p 6379 HGET "$k" pod; done | sort | uniq -c | awk '$2!=""{printf "%s:%s ", $2, $1}'; }
ended() { [ -f "$LAB/load/$TAG/status.json" ] && python3 -c "import json;print(len(json.load(open('$LAB/load/$TAG/status.json'))))" || echo 0; }
mq() { "$PY" -c "import sys; sys.path.insert(0,'.'); from mq_probe import consumer_state as c; s=c(); print(f\"mq pending={s.get('pending','?')} ack_pending={s.get('ack_pending','?')} redelivered={s.get('redelivered','?')} delivered={s.get('delivered','?')}\")" 2>/dev/null || echo "mq=n/a"; }
setenv() { local f="pods/$1.env"; sed -i "/^$2=/d" "$f"; [ -n "$3" ] && echo "$2=$3" >> "$f"; }
TAG="r5-skew-$FAULT-$(date +%H%M%S)"

for p in d e f; do setenv "$p" NIMBUS_GENERATION 2; setenv "$p" PYTHONPATH "$V2"; done
for p in a b c; do setenv "$p" NIMBUS_GENERATION 3; setenv "$p" PYTHONPATH ""; done
./labctl.py pods down a b c d e f >/dev/null; ./labctl.py pods up d e f >/dev/null
curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null
find "$VC" -name lab_steps.txt -delete 2>/dev/null; ./labctl.py ledger reset >/dev/null; "$PY" mq_probe.py purge >/dev/null 2>&1; sleep 6
echo "== $(ts) [skew/$FAULT] fleet d,e,f gen 2 = v2 (contract 2) → ROLLBACK to a,b,c gen 3 = v1 (this tree); $N sessions × '$PROMPT'. tag=$TAG"
echo "   fleet: $(fleet)"
"$PY" load.py start --tag "$TAG" --pods d,e,f --sessions "$N" --prompt "$PROMPT" > "$LAB/load-$TAG.out" 2>&1 & LOAD=$!
sleep 12; T0=$(date +%s)
echo "   t+0s owners: $(owners)"
OLD=(d e f); NEW=(a b c)
for i in 0 1 2; do o=${OLD[$i]}; n=${NEW[$i]}
  ./labctl.py pods up "$n" > /dev/null && echo "== $(ts) t+$(( $(date +%s) - T0 ))s  $n up (v1, gen 3)"; sleep 6
  ./labctl.py "$FAULT" "$o" | head -1
  for w in $(seq 1 30); do [ "$(act "$o")" != active ] && break; sleep 1; done
  echo "== $(ts) t+$(( $(date +%s) - T0 ))s  $o gone after ${w}s | $(mq) | owners: $(owners)"
  sleep "$GAP"
  echo "   t+$(( $(date +%s) - T0 ))s fleet: $(fleet)| owners: $(owners) | ended=$(ended)/$N"
done
echo "== $(ts) rollout done; waiting for the load to drain"
for w in $(seq 1 30); do sleep 5; e=$(ended); ow=$(owners); echo "   t+$(( $(date +%s) - T0 ))s ended=$e/$N owners: $ow| $(mq)"; [ "$e" -ge "$N" ] && [ -z "$ow" ] && break; done  # clients gone AND no turn still owned
wait $LOAD 2>/dev/null; tail -1 "$LAB/load-$TAG.out"
stranded() { valkey-cli -p 6379 --scan --pattern 'stranded:*' | wc -l | tr -d ' '; }
echo "== $(ts) after the rollback: stranded=$(stranded) | $(mq)"
if [ "$(stranded)" -gt 0 ] && [ "${ROLLFORWARD:-1}" = 1 ]; then
  setenv d NIMBUS_GENERATION 4; setenv d PYTHONPATH "$V2"
  ./labctl.py pods up d >/dev/null && echo "== $(ts) ROLL FORWARD: d up again as v2 (contract 2, gen 4) — can it drain the stranded sessions?"
  R0=$(date +%s)
  for w in $(seq 1 20); do sleep 5; echo "   r+$(( $(date +%s) - R0 ))s stranded=$(stranded) owners: $(owners)| fleet: $(fleet)"; [ "$(stranded)" -eq 0 ] && [ -z "$(owners)" ] && [ "$w" -gt 2 ] && break; done
fi
echo "== $(ts) report:"; "$PY" load.py report --tag "$TAG"
echo "== $(ts) journal (refusals / strands / drains):"; journalctl --user -u lab-nimbus@a -u lab-nimbus@b -u lab-nimbus@c -u lab-nimbus@d -u lab-nimbus@e -u lab-nimbus@f --since "5 min ago" --no-pager -o cat | grep -iE "contract|STRANDED|stranded\]|handoff\] (resumed|could not)|on_orphan" | sed 's/sess_[0-9a-f]*/sess_X/g; s/[0-9a-f]\{8\}//g' | sort | uniq -c | sort -rn | head -8 | cut -c1-170
echo "== $(ts) one v2 session's stream as v1 sees it:"; SID=$(python3 -c "import json;print(list(json.load(open('$LAB/load/$TAG/manifest.json'))['sessions'])[0])"); NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$SID" | awk '{print $2}' | sort | uniq -c | sort -rn | head -8 | tr '\n' ' '; echo
for p in a b c d e f; do setenv "$p" PYTHONPATH ""; done
