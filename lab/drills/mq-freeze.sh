#!/bin/bash
# Frozen consumer holds messages: SIGSTOP pod-b (a member of the handoff queue group), publish N
# announcements, watch the JetStream consumer's ack_pending/redelivered counters — messages
# routed to the frozen member are stuck until ack_wait (15s) redelivers them to pod-a.
# usage: mq-freeze.sh [N=6] [WATCH=40]
set -u
cd "$(dirname "$0")/.."
N=${1:-6}; WATCH=${2:-40}; PY=$PWD/../.venv/bin/python
ts() { date '+%H:%M:%S'; }
./labctl.py pods up >/dev/null; sleep 2
echo "== $(ts) both pods consuming; freeze pod-b, then publish $N messages"
./labctl.py freeze b | head -1
"$PY" mq_probe.py publish "$N" 2>&1 | grep -c announced | sed 's/^/   published: /'
"$PY" mq_probe.py watch "$WATCH"
echo "== $(ts) pod-a handled: $(grep -c 'mq_probe\|probe-' ~/.local/share/nimbus-lab/pods/a/.logs/nimbus.log 2>/dev/null) log lines mentioning probe sessions"
echo "== $(ts) THAW pod-b"; ./labctl.py thaw b | head -1; sleep 3
"$PY" mq_probe.py watch 5
