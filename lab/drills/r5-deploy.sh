#!/bin/bash
# R5.1 — rolling deploy under load, new pod ids replace old ones (a,b,c → d,e,f; k8s Deployment shape:
# names change, old ids never come back). 20 sessions run 8-step turns across the old fleet while the
# rollout SIGTERMs one old pod at a time. surge = start the new pod, then term the old (maxSurge);
# unavailable = term the old, then start the new (maxUnavailable). Every announcement lands on whoever is
# in the NATS queue group at that moment; JetStream keeps it when nobody is.
# Generations: old pods run NIMBUS_GENERATION=$GEN_OLD (1), new pods $GEN_NEW (2); GEN_NEW=1 disables the
# supersession retrofit (old pods keep consuming handoffs) for the before/after comparison.
# usage: [GEN_OLD=1 GEN_NEW=2] r5-deploy.sh surge|unavailable [sessions=20] [gap=10] [prompt="lab steps 7 sleep 8"]
set -u
cd "$(dirname "$0")/.."
MODE=${1:-surge}; N=${2:-20}; GAP=${3:-10}; PROMPT=${4:-"lab steps 7 sleep 8"}
OLD=(a b c); NEW=(d e f)
LAB=~/.local/share/nimbus-lab; VC=$LAB/vcompute/leases; PY=$PWD/../.venv/bin/python
CG=/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/app-lab\\x2dnimbus.slice
ts() { date '+%H:%M:%S'; }
act() { systemctl --user is-active "lab-nimbus@$1" 2>/dev/null; }
mem() { local d="$CG/lab-nimbus@$1.service"; [ -d "$d" ] && echo "$(( $(cat "$d/memory.current") / 1048576 ))M" || echo "-"; }
fleet() { for p in a b c d e f; do printf '%s=%s/%s ' "$p" "$(act "$p" | cut -c1-3)" "$(mem "$p")"; done; }
owners() { valkey-cli -p 6379 --scan --pattern 'turn:*' | while read -r k; do valkey-cli -p 6379 HGET "$k" pod; done | sort | uniq -c | awk '$2!=""{printf "%s:%s ", $2, $1}'; }
ended() { [ -f "$LAB/load/$TAG/status.json" ] && python3 -c "import json;print(len(json.load(open('$LAB/load/$TAG/status.json'))))" || echo 0; }
mq() { "$PY" -c "import sys; sys.path.insert(0,'.'); from mq_probe import consumer_state as c; s=c(); print(f\"mq pending={s.get('pending','?')} ack_pending={s.get('ack_pending','?')} redelivered={s.get('redelivered','?')} delivered={s.get('delivered','?')}\")" 2>/dev/null || echo "mq=n/a"; }
TAG="r5-$MODE-$(date +%H%M%S)"

setgen() { local f="pods/$1.env"; sed -i '/^NIMBUS_GENERATION=/d' "$f"; echo "NIMBUS_GENERATION=$2" >> "$f"; }
for p in a b c; do setgen "$p" "${GEN_OLD:-1}"; done; for p in d e f; do setgen "$p" "${GEN_NEW:-2}"; done
./labctl.py pods down a b c d e f >/dev/null; ./labctl.py pods up a b c | grep -c started >/dev/null
curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null
find "$VC" -name lab_steps.txt -delete 2>/dev/null; ./labctl.py ledger reset >/dev/null; "$PY" mq_probe.py purge >/dev/null 2>&1; sleep 6
"$PY" probe.py --tag "$TAG" --pods a,b,c,d,e,f >/dev/null 2>&1 & PROBE=$!
echo "== $(ts) [$MODE] fleet a,b,c gen ${GEN_OLD:-1} → d,e,f gen ${GEN_NEW:-2}; $N sessions × '$PROMPT'; rollout a→d, b→e, c→f with ${GAP}s gaps. tag=$TAG"
"$PY" load.py start --tag "$TAG" --pods a,b,c --sessions "$N" --prompt "$PROMPT" > "$LAB/load-$TAG.out" 2>&1 & LOAD=$!
sleep 12; T0=$(date +%s)
echo "   t+0s  fleet: $(fleet)| owners: $(owners)"
for i in 0 1 2; do o=${OLD[$i]}; n=${NEW[$i]}
  if [ "$MODE" = surge ]; then
    ./labctl.py pods up "$n" > /dev/null && echo "== $(ts) t+$(( $(date +%s) - T0 ))s  $n up (surge)"; sleep 6  # one heartbeat: old pods notice the new generation
    ./labctl.py term "$o" | head -1
  else
    ./labctl.py term "$o" | head -1
  fi
  for w in $(seq 1 30); do [ "$(act "$o")" != active ] && break; sleep 1; done
  echo "== $(ts) t+$(( $(date +%s) - T0 ))s  $o gone after ${w}s | $(mq) | owners: $(owners)"
  if [ "$MODE" != surge ]; then ./labctl.py pods up "$n" > /dev/null && echo "== $(ts) t+$(( $(date +%s) - T0 ))s  $n up (unavailable)"; fi
  sleep "$GAP"
  echo "   t+$(( $(date +%s) - T0 ))s fleet: $(fleet)| owners: $(owners) | ended=$(ended)/$N"
done
echo "== $(ts) rollout done; waiting for the load to drain"
for w in $(seq 1 30); do sleep 5; e=$(ended); ow=$(owners); echo "   t+$(( $(date +%s) - T0 ))s ended=$e/$N owners: $ow| $(mq)"; [ "$e" -ge "$N" ] && [ -z "$ow" ] && break; done  # clients gone AND no turn still owned
kill $PROBE 2>/dev/null; wait $LOAD 2>/dev/null; tail -1 "$LAB/load-$TAG.out"
echo "== $(ts) report:"; "$PY" load.py report --tag "$TAG"
echo "== $(ts) probe csv: $LAB/probe/$TAG.csv"
