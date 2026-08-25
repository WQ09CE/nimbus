#!/usr/bin/env python3
"""Isolation-ladder benchmark (memex Phase 1): startup tax + penetration matrix.

Measures two things across the isolation ladder, on THIS machine, with real
probes — never assumed results:

  startup tax   — wall-clock to wrap a no-op (`true`), median of N runs; the
                  price each rung charges per invocation.
  penetration   — a fixed probe set run under each rung; what each one blocks
                  vs lets through (workspace write, host write, host-secret
                  read, network egress, host PID visibility).

The ladder is three tiers of a different KIND of boundary, not a single
gradient:
  same rootfs, different OS primitive : none · unshare(ns) · bwrap · srt
  different rootfs                    : docker (busybox)
  different kernel                    : microVM (firecracker/qemu — N/A here)

bwrap is nimbus' own production wrapper (core.tools.sandbox.wrap_command), so
the row measures the real shipped sandbox, not a lookalike.

Run: python scripts/dev_tools/sandbox_ladder_bench.py [--runs N] [--json PATH]
Requires srt at ~/.cache/nimbus-bench/srt (npm i @anthropic-ai/sandbox-runtime).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from nimbus.core.tools import sandbox as nimbus_sandbox  # noqa: E402

SRT_BIN = os.path.expanduser("~/.cache/nimbus-bench/srt/node_modules/.bin/srt")
SECRET = "NIMBUS_LADDER_SECRET_9f3a"
PROBE_TIMEOUT = 12.0


def _run(argv, timeout=PROBE_TIMEOUT):
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return None, "[timeout]"
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"[error: {exc}]"


# ── ladder: each rung maps a shell script string to a full argv (or None) ──

def _srt_settings(ws: str) -> str:
    path = os.path.join(ws, ".srt-settings.json")
    Path(path).write_text(json.dumps({
        "filesystem": {"allowRead": [], "denyRead": [], "allowWrite": [ws], "denyWrite": []},
        "network": {"allowedDomains": [], "deniedDomains": []},
    }))
    return path


class Rung:
    def __init__(self, name, tier, wrap, available, note=""):
        self.name = name
        self.tier = tier
        self.wrap = wrap          # (script, ws) -> argv
        self.available = available
        self.note = note


def _rung_none(script, ws):
    return ["bash", "-c", script]


def _rung_unshare(script, ws):
    # User + PID + net + mount ns; --fork so the child is PID 1 of the new
    # namespace, then remount /proc so host PIDs are actually hidden.
    return ["unshare", "-Uprnm", "--fork", "--map-root-user", "bash", "-c",
            f"mount -t proc proc /proc 2>/dev/null; {script}"]


def _rung_bwrap(script, ws):
    return nimbus_sandbox.wrap_command(
        ["bash", "-c", script], [ws], ws, allow_network=False,
    )


def _rung_srt(script, ws):
    return [SRT_BIN, "-s", _srt_settings(ws), "bash", "-c", script]


def _rung_docker(image):
    def wrap(script, ws):
        return ["docker", "run", "--rm", "--network", "none",
                "-v", f"{ws}:{ws}", "-w", ws, image, "sh", "-c", script]
    return wrap


def _detect_docker_image():
    rc, out = _run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=10)
    if rc != 0:
        return None
    images = [ln.strip() for ln in out.splitlines() if ln.strip() and "<none>" not in ln]
    for pref in ("busybox:latest", "busybox", "alpine:latest", "alpine"):
        if pref in images:
            return pref
    # Try to pull busybox (small); skip docker rung if offline.
    rc, _ = _run(["docker", "pull", "busybox"], timeout=60)
    return "busybox" if rc == 0 else (images[0] if images else None)


def build_ladder():
    rungs = []
    rungs.append(Rung("none", "same-rootfs", _rung_none, True, "bare subprocess"))

    rc, _ = _run(["unshare", "-Uprn", "--fork", "true"], timeout=5)
    rungs.append(Rung("unshare", "same-rootfs", _rung_unshare, rc == 0,
                      "user+pid+net+mount ns" if rc == 0 else "unprivileged userns denied"))

    backend = nimbus_sandbox.sandbox_backend()
    rungs.append(Rung("bwrap", "same-rootfs", _rung_bwrap, backend == "bubblewrap",
                      "nimbus production wrapper"))

    rungs.append(Rung("srt", "same-rootfs", _rung_srt, os.path.exists(SRT_BIN),
                      "Anthropic srt (bwrap+proxy)"))

    image = _detect_docker_image()
    rungs.append(Rung("docker", "diff-rootfs",
                      _rung_docker(image) if image else None, image is not None,
                      f"container ({image})" if image else "no image/daemon"))

    rungs.append(Rung("microvm", "diff-kernel", None, False,
                      "firecracker/qemu not installed"))
    return rungs


# ── probes: (name, script → marker) ──

def probes(ws, sentinel):
    return {
        "write_ws":      f"echo x > {ws}/w.probe 2>/dev/null && echo ALLOW || echo BLOCK",
        "write_host":    "touch /usr/.nimbus_probe 2>/dev/null && echo ESCAPE || echo BLOCK",
        "read_secret":   f"grep -q {SECRET} {sentinel} 2>/dev/null && echo LEAK || echo BLOCK",
        "net_egress":    "curl -sm3 http://example.com >/dev/null 2>&1 && echo OPEN || echo BLOCK",
        "host_pids":     "P=$(ls -d /proc/[0-9]* 2>/dev/null | wc -l); [ \"$P\" -gt 40 ] && echo VISIBLE || echo HIDDEN",
    }


# expected safe result per probe (for scoring)
SAFE = {"write_ws": "ALLOW", "write_host": "BLOCK", "read_secret": "BLOCK",
        "net_egress": "BLOCK", "host_pids": "HIDDEN"}


def parse_marker(name, out):
    for token in ("ALLOW", "BLOCK", "ESCAPE", "LEAK", "OPEN", "VISIBLE", "HIDDEN"):
        if token in out:
            return token
    return "?" if "[timeout]" not in out and "[error" not in out else "err"


def measure_latency(rung, ws, runs):
    samples = []
    for _ in range(runs):
        argv = rung.wrap("true", ws)
        t0 = time.monotonic()
        _run(argv, timeout=30)
        samples.append((time.monotonic() - t0) * 1000)
    return {
        "median_ms": round(statistics.median(samples), 1),
        "min_ms": round(min(samples), 1),
        "p90_ms": round(sorted(samples)[min(len(samples) - 1, int(len(samples) * 0.9))], 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=7)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    host_tmp = tempfile.mkdtemp(prefix="nimbus_ladder_host_")
    sentinel = os.path.join(host_tmp, "host_secret")
    Path(sentinel).write_text(SECRET + "\n")

    ladder = build_ladder()
    probe_names = list(SAFE.keys())
    results = []

    for rung in ladder:
        row = {"rung": rung.name, "tier": rung.tier, "available": rung.available,
               "note": rung.note, "probes": {}, "latency": None}
        if rung.available and rung.wrap:
            ws = tempfile.mkdtemp(prefix=f"nimbus_ladder_{rung.name}_")
            try:
                pset = probes(ws, sentinel)
                for pname in probe_names:
                    _, out = _run(rung.wrap(pset[pname], ws))
                    row["probes"][pname] = parse_marker(pname, out)
                row["latency"] = measure_latency(rung, ws, args.runs)
            finally:
                shutil.rmtree(ws, ignore_errors=True)
        results.append(row)

    shutil.rmtree(host_tmp, ignore_errors=True)

    # ── terminal report ──
    print("\n\033[1mIsolation-ladder benchmark\033[0m  (this machine)")
    print(f"probes: {'  '.join(probe_names)}")
    print(f"{'rung':<9}{'tier':<14}{'lat(med)':>9}  penetration")
    print("─" * 74)
    for r in results:
        if not r["available"]:
            print(f"{r['rung']:<9}{r['tier']:<14}{'N/A':>9}  — {r['note']}")
            continue
        lat = f"{r['latency']['median_ms']}ms"
        cells = []
        for p in probe_names:
            v = r["probes"][p]
            # On a different-rootfs rung, host-fs probes test the CONTAINER's
            # own fs, not a host escape — neutral, not a penetration.
            if r["tier"] != "same-rootfs" and p in ("write_host", "read_secret"):
                cells.append(f"\033[36m{v}·cfs\033[0m")
                continue
            safe = v == SAFE[p]
            color = "\033[32m" if safe else "\033[31m"
            cells.append(f"{color}{v}\033[0m")
        print(f"{r['rung']:<9}{r['tier']:<14}{lat:>9}  {' '.join(cells)}")
    print("─" * 74)
    print("green=safe (ws=ALLOW, else BLOCK/HIDDEN) · red=penetrated · "
          "cyan·cfs=container's own fs, not a host escape\n")

    payload = {"probe_names": probe_names, "safe": SAFE, "rungs": results}
    out_path = args.json or os.path.join(host_tmp + "_result.json")
    Path(out_path).write_text(json.dumps(payload, indent=2))
    print(f"json → {out_path}")
    return payload


if __name__ == "__main__":
    main()
