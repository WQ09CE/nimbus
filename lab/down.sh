#!/bin/bash
set -e
systemctl --user stop "lab-nimbus@*" lab-vcompute lab-pi-sidecar lab-valkey lab-nats lab-temporal 2>/dev/null || true
echo "lab infra stopped (data kept under ~/.local/share/nimbus-lab)"
