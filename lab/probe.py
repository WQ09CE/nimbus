#!/usr/bin/env python3
"""nimbus-lab probe — the 1 Hz sampler the 0903 pod did not have switched on.

Per pod, every second: cgroup memory.current / memory.peak / memory.max / oom_kill count,
memory + cpu PSI (some avg10), the ledger's view of the pod (heartbeat age and whatever the
pod self-reports: rss / loop lag / gc pause), and /health round-trip latency (a probe request
is the cheapest "is the event loop responsive" signal — 0903 saw /ready go 0.1 s → 10 s).

  lab/probe.py [--tag NAME] [--pods a,b] [--every 1]     writes ~/.local/share/nimbus-lab/probe/NAME.csv
                                                        and prints one line per sample; Ctrl-C / SIGTERM stops.
"""
import argparse
import csv
import os
import signal
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labctl import base  # noqa: E402

CG = Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/app-lab\\x2dnimbus.slice")
OUT = Path.home() / ".local/share/nimbus-lab/probe"
FIELDS = ["t", "pod", "mem_mb", "peak_mb", "max_mb", "swap_mb", "oom_kill", "psi_mem_some10", "psi_mem_full10", "psi_cpu_some10",
          "hb_age_s", "rss_mb", "lag_ms", "gc2_ms", "health_ms"]


def read(p, default=""):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return default


def psi(p, which="some"):
    for line in read(p).splitlines():
        if line.startswith(which):
            return float(line.split("avg10=")[1].split()[0])
    return ""


def cgroup(pod):
    d = CG / f"lab-nimbus@{pod}.service"
    if not d.exists():
        return {"mem_mb": "", "peak_mb": "", "max_mb": "", "swap_mb": "", "oom_kill": "", "psi_mem_some10": "", "psi_mem_full10": "", "psi_cpu_some10": ""}
    mx = read(d / "memory.max")
    ev = dict(line.split() for line in read(d / "memory.events").splitlines() if line)
    return {
        "mem_mb": round(int(read(d / "memory.current", "0")) / 2**20, 1),
        "peak_mb": round(int(read(d / "memory.peak", "0")) / 2**20, 1),
        "max_mb": "" if mx in ("", "max") else round(int(mx) / 2**20),
        "swap_mb": round(int(read(d / "memory.swap.current", "0")) / 2**20, 1),
        "oom_kill": ev.get("oom_kill", ""),
        "psi_mem_some10": psi(d / "memory.pressure"), "psi_mem_full10": psi(d / "memory.pressure", "full"),
        "psi_cpu_some10": psi(d / "cpu.pressure"),
    }


def health(pod, timeout=5.0):
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(base(pod) + "/health", timeout=timeout):
            return round((time.monotonic() - t0) * 1000)
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def ledger_view(r, pod):
    """Heartbeat age + self-reported facts. Survives the pod's death via podlast:{pod} (R4 retrofit);
    before that retrofit only pod:{pod} exists and vanishes with the heartbeat."""
    h = r.hgetall(f"pod:{pod}") or r.hgetall(f"podlast:{pod}")
    if not h:
        return {"hb_age_s": "", "rss_mb": "", "lag_ms": "", "gc2_ms": ""}
    return {"hb_age_s": round(time.time() - float(h.get("last", 0)), 1), "rss_mb": h.get("rss_mb", ""),
            "lag_ms": h.get("lag_ms", ""), "gc2_ms": h.get("gc2_ms", "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=time.strftime("probe-%H%M%S"))
    ap.add_argument("--pods", default="a,b")
    ap.add_argument("--every", type=float, default=1.0)
    a = ap.parse_args()
    import redis
    r = redis.from_url(os.environ.get("NIMBUS_LEDGER_URL", "redis://127.0.0.1:6379"), decode_responses=True)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{a.tag}.csv"
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("_stop", True))
    print(f"probe → {path}", flush=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        t0 = time.time()
        while not globals().get("_stop"):
            for pod in a.pods.split(","):
                row = {"t": round(time.time() - t0, 1), "pod": pod, **cgroup(pod), **ledger_view(r, pod), "health_ms": health(pod)}
                w.writerow(row)
                f.flush()
                print(f"t={row['t']:6.1f} {pod} mem={row['mem_mb']:>7}M peak={row['peak_mb']:>7}M swap={row['swap_mb']}M oom={row['oom_kill'] or '-'} "
                      f"psi_mem={row['psi_mem_some10']} hb_age={row['hb_age_s']} rss={row['rss_mb']} lag={row['lag_ms']} gc2={row['gc2_ms']} "
                      f"health={row['health_ms']}", flush=True)
            time.sleep(a.every)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
