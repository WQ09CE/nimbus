#!/bin/bash
# nimbus-lab: link + start infrastructure units (Valkey, NATS JetStream, Temporal dev).
# Pods/vcompute come up via lab-ctl (see README). Idempotent.
set -e
cd "$(dirname "$0")"
for u in units/lab-*.service; do
  systemctl --user link "$PWD/$u" >/dev/null 2>&1 || true
done
systemctl --user daemon-reload
systemctl --user start lab-valkey lab-nats lab-temporal lab-vcompute lab-pi-sidecar
sleep 2
./status.sh
