#!/bin/bash
# R4.1 — OOM collateral + rescue cascade (the 0903 shape on one pod pair).
# pod-a runs two turns: a VICTIM (plain steps) and a SUSPECT (steps that ingest K bytes of tool output
# each, the 0829/0903 amplifier). MemoryMax on both pods; the suspect drives pod-a into the kernel OOM
# killer, the victim dies as collateral, pod-b's scanner resumes both — and the suspect's remaining
# ingest lands on pod-b. When both pods are dead the drill restarts them under the SAME pod ids (the 0903
# container-restart shape) and watches whether anyone still notices the orphans.
# usage: r4-oom.sh [MemoryMax=260M] [bloat=24M] [watch=90]. Turns are 8 steps: the loop's same-tool
# streak guard (loop.py, streak % 8) nudges the model after 8 Bash calls and the mock then ends the turn.
set -u
cd "$(dirname "$0")/.."
MAX=${1:-260M}; K=${2:-24M}; WATCH=${3:-90}
LAB=~/.local/share/nimbus-lab; VC=$LAB/vcompute/leases; PY=$PWD/../.venv/bin/python
CG=/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/app-lab\\x2dnimbus.slice
ts() { date '+%H:%M:%S'; }
mem() { local d="$CG/lab-nimbus@$1.service"; [ -d "$d" ] && echo "$(( $(cat "$d/memory.current") / 1048576 ))M/oom=$(awk '/^oom_kill/{print $2}' "$d/memory.events")" || echo "gone"; }
act() { systemctl --user is-active "lab-nimbus@$1" 2>/dev/null; }
steps() { for f in $(find "$VC" -name lab_steps.txt 2>/dev/null | sort); do printf '%s=%s ' "$(basename "$(dirname "$f")" | cut -c1-6)" "$(paste -sd, "$f" | sed 's/step-//g')"; done; }
orphan() { valkey-cli -p 6379 HGET "orphan:$1" resolution | tr -d '\n'; }
owner() { valkey-cli -p 6379 HMGET "turn:$1" pod epoch | paste -sd/ | tr -d '\n'; }
newsess() { curl -s -X POST 127.0.0.1:8000/api/v1/sessions -H 'Content-Type: application/json' -d "{\"name\":\"$1\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])'; }
chat() { curl -sN -X POST "127.0.0.1:8000/api/v1/sessions/$1/chat" -H 'Content-Type: application/json' -d "{\"content\":\"$2\"}" > "$LAB/r4-oom-$3-a.sse" 2>&1 & }
observe() { curl -sN "127.0.0.1:8001/api/v1/sessions/$1/events" > "$LAB/r4-oom-$2-b.sse" 2>&1 & }

curl -s -X POST 127.0.0.1:8793/v1/chaos/recycle -H 'Content-Type: application/json' -d '{}' >/dev/null
find "$VC" -name lab_steps.txt -delete 2>/dev/null; ./labctl.py ledger reset >/dev/null
./labctl.py mem a "$MAX" >/dev/null; ./labctl.py mem b "$MAX" >/dev/null
"$PY" probe.py --tag r4-oom --pods a,b >/dev/null 2>&1 & PROBE=$!
sleep 2
echo "== $(ts) MemoryMax=$MAX on a,b (a=$(mem a) b=$(mem b)); victim: lab steps 8 sleep 2 | suspect: lab steps 8 sleep 1 bloat $K"
V=$(newsess r4-victim); S=$(newsess r4-suspect); echo "   victim=$V suspect=$S"
chat "$V" "lab steps 8 sleep 2" victim; observe "$V" victim
sleep 2; chat "$S" "lab steps 8 sleep 1 bloat $K" suspect; observe "$S" suspect
T0=$(date +%s)
for i in $(seq 1 "$((WATCH / 5))"); do sleep 5
  echo "   t+$(( $(date +%s) - T0 ))s a=$(act a):$(mem a) b=$(act b):$(mem b) | victim owner=$(owner "$V") orphan=$(orphan "$V") | suspect owner=$(owner "$S") orphan=$(orphan "$S") | steps: $(steps)"
done
if [ "$(act a)" != active ] && [ "$(act b)" != active ]; then
  echo "== $(ts) both pods dead -> restart under the same ids (0903: same pod name, new process)"; ./labctl.py pods up | grep -E '^[ab] '
  R0=$(date +%s)
  for i in 1 2 3 4 5 6; do sleep 5
    echo "   r+$(( $(date +%s) - R0 ))s a=$(act a):$(mem a) b=$(act b):$(mem b) | victim owner=$(owner "$V") orphan=$(orphan "$V") status=$(curl -s "127.0.0.1:8000/api/v1/sessions/$V/status" | cut -c1-60) | suspect owner=$(owner "$S") orphan=$(orphan "$S")"
  done
fi
kill $PROBE 2>/dev/null; pkill -f "sessions/$V/" 2>/dev/null; pkill -f "sessions/$S/" 2>/dev/null
echo "== $(ts) OOM lines (journal):"; journalctl --user -u lab-nimbus@a -u lab-nimbus@b --since "$((WATCH + 60)) seconds ago" --no-pager -o short-precise | grep -i -E "oom|killed|Failed with result" | cut -c1-160
echo "== $(ts) victim stream tail:"; NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$V" | tail -6
echo "== $(ts) suspect stream tail:"; NIMBUS_LEDGER_URL=redis://127.0.0.1:6379 "$PY" -m nimbus.infra.ledger dump "$S" | tail -6
for who in victim suspect; do
  echo "== $who: pod-a client saw: $(grep -oE '^event: [a-z_]+' "$LAB/r4-oom-$who-a.sse" | sort | uniq -c | sort -rn | tr '\n' ' ') | pod-b observer: $(grep -oE '^event: [a-z_]+' "$LAB/r4-oom-$who-b.sse" | sort | uniq -c | sort -rn | tr '\n' ' ')"
  grep -A1 -E '^event: (done|interrupted|resume_replay)' "$LAB/r4-oom-$who-b.sse" | grep -E '^data' | cut -c1-140 | tail -3
done
echo "== $(ts) ledger:"; ./labctl.py ledger | python3 -c 'import sys,json;d=json.load(sys.stdin);print("   pods:",d["pods"]);print("   orphans:",json.dumps(d["orphans"])[:600]);print("   rejected_writes:",d["rejected_writes"])'
./labctl.py mem a infinity >/dev/null; ./labctl.py mem b infinity >/dev/null
echo "== $(ts) probe csv: $LAB/probe/r4-oom.csv (MemoryMax reset to infinity)"
