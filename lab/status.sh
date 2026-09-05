#!/bin/bash
printf '%-14s %s\n' valkey  "$(systemctl --user is-active lab-valkey)  $(valkey-cli -p 6379 ping 2>/dev/null)"
printf '%-14s %s\n' nats    "$(systemctl --user is-active lab-nats)  $(curl -sf 127.0.0.1:8222/healthz 2>/dev/null)"
printf '%-14s %s\n' temporal "$(systemctl --user is-active lab-temporal)  $(~/.local/bin/temporal operator cluster health --address 127.0.0.1:7233 2>/dev/null)"
printf '%-14s %s\n' vcompute "$(systemctl --user is-active lab-vcompute)  $(curl -sf 127.0.0.1:8793/health 2>/dev/null)"
for p in a b; do printf '%-14s %s\n' "pod-$p" "$(systemctl --user is-active lab-nimbus@$p)  $(curl -sf 127.0.0.1:$(sed -n s/NIMBUS_PORT=//p "$(dirname "$0")/pods/$p.env")/health 2>/dev/null | head -c 80)"; done
printf '%-14s %s\n' pi-sidecar "$(systemctl --user is-active lab-pi-sidecar)  $(curl -sf -o /dev/null -w %{http_code} 127.0.0.1:8799/v1/models 2>/dev/null)"
