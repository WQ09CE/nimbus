#!/usr/bin/env python3
"""nimbus-lab load generator + outcome report (R5: a fleet under a rolling deploy).

  load.py start  --tag T --pods a,b,c --sessions 20 --prompt "lab steps 7 sleep 8" [--timeout 300]
      N sessions round-robin over the pods; one chat each, SSE streams captured to
      ~/.local/share/nimbus-lab/load/T/<sid>.sse; status.json updated as sessions end
      (what the ORIGINAL pod's client saw: done / interrupted / paused / connection lost).
  load.py report --tag T
      per session, from the Valkey stream + ledger: turns, owners (pod/epoch history),
      tool results / replays, end reasons, steps on the bound workspace, outcome; then
      per-pod counts (originals, takeovers), outcome histogram, handoff-bus counters.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from labctl import base, cmd_allow, http  # noqa: E402

LAB = Path.home() / ".local/share/nimbus-lab"
OUT = LAB / "load"


def stream(pod, sid, prompt, out, status, lock, timeout):
    req = urllib.request.Request(f"{base(pod)}/sessions/{sid}/chat", data=json.dumps({"content": prompt}).encode(),
                                 method="POST", headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    t0, ev, last = time.time(), None, "no_terminal"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(out, "w") as f:
            for raw in r:
                line = raw.decode(errors="replace").rstrip("\n")
                f.write(line + "\n")
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:") and ev in ("done", "interrupted", "paused", "error"):
                    d = line[5:].strip()
                    last = ev + ":" + (json.loads(d).get("status") or json.loads(d).get("reason") or "") if d.startswith("{") else ev
                    if ev == "done" or ev == "interrupted":
                        break
    except Exception as e:
        last = f"conn_lost:{type(e).__name__}"
    with lock:
        status[sid] = {"pod": pod, "client_saw": last, "elapsed_s": round(time.time() - t0, 1)}


def start(a):
    pods = a.pods.split(",")
    d = OUT / a.tag
    d.mkdir(parents=True, exist_ok=True)
    for p in pods:
        cmd_allow(p)
    manifest, status, lock, threads = {}, {}, threading.Lock(), []
    t_start = time.time()
    for i in range(a.sessions):
        pod = pods[i % len(pods)]
        sid = http("POST", base(pod) + "/sessions", {"name": f"{a.tag}-{i:02d}"})["id"]
        manifest[sid] = {"pod": pod, "i": i}
        th = threading.Thread(target=stream, args=(pod, sid, a.prompt, d / f"{sid}.sse", status, lock, a.timeout), daemon=True)
        th.start()
        threads.append(th)
        time.sleep(0.15)
    (d / "manifest.json").write_text(json.dumps({"started": t_start, "prompt": a.prompt, "pods": pods, "sessions": manifest}, indent=1))
    print(f"{a.sessions} sessions started over {pods} → {d}", flush=True)
    while any(t.is_alive() for t in threads):
        time.sleep(2)
        with lock:
            (d / "status.json").write_text(json.dumps(status, indent=1))
    with lock:
        (d / "status.json").write_text(json.dumps(status, indent=1))
    print(f"all sessions ended after {time.time() - t_start:.0f}s: {Counter(v['client_saw'] for v in status.values())}", flush=True)


def report(a):
    import redis
    r = redis.from_url(os.environ.get("NIMBUS_LEDGER_URL", "redis://127.0.0.1:6379"), decode_responses=True)
    d = OUT / a.tag
    man = json.loads((d / "manifest.json").read_text())
    status = json.loads((d / "status.json").read_text()) if (d / "status.json").exists() else {}
    t0 = man["started"]
    rows, outcomes, takeovers, originals = [], Counter(), Counter(), Counter()
    for sid, m in man["sessions"].items():
        ev = [json.loads(f["e"]) for _id, f in r.xrange(f"sess:{sid}:log")]
        turns = sum(1 for e in ev if e["type"] == "turn/start")
        ends = [e["data"].get("reason", {}).get("kind", "?") for e in ev if e["type"] == "turn/end"]
        results = [e for e in ev if e["type"] in ("tool/result", "tool/result.v2")]  # .v2: lab contract-v2 branch
        real = sum(1 for e in results if not e["data"].get("synthetic"))
        replays = sum(1 for e in results if e["data"].get("resumed"))
        done = any(e["type"] == "assistant/message" and str(e["data"].get("message", {}).get("content", "")).startswith("LAB_DONE") for e in ev)
        open_turn = bool(ev) and ev[-1]["type"] != "turn/end"
        owners = (r.hget(f"turn:{sid}", "owners") or "").split()
        for o in owners[1:]:
            takeovers[o.split("/")[0]] += 1
        originals[m["pod"]] += 1
        steps = ""
        try:
            b = json.load(open(LAB / "sessions" / f"{sid}.json")).get("metadata", {}).get("sandbox_binding") or {}
            p = LAB / "vcompute/leases" / b.get("lease_id", "-") / "lab_steps.txt"
            steps = ",".join(x.replace("step-", "") for x in p.read_text().split()) if p.exists() else ""
        except Exception:
            pass
        last_t = max((e.get("time", 0) for e in ev), default=0)
        if not open_turn and ends and ends[-1] == "completed" and (done or real >= 7):
            outcome = "completed"  # LAB_DONE, or the loop's same-tool nudge ended an 8-step turn normally
        elif open_turn:
            outcome = "open(lost)"
        elif ends and ends[-1] == "paused":
            outcome = "paused(not resumed)"
        elif ends and ends[-1] == "interrupted":
            outcome = "interrupted"
        else:
            outcome = "other:" + (ends[-1] if ends else "empty")
        outcomes[outcome] += 1
        rows.append((m["i"], sid, m["pod"], " ".join(owners), turns, real, replays, "/".join(ends), steps, outcome,
                     status.get(sid, {}).get("client_saw", "?"), round(last_t - t0, 1) if last_t else ""))
    print(f"{'#':>2} {'session':17} {'orig':4} {'owners (pod/epoch)':22} {'turns':5} {'res':3} {'rep':3} {'end reasons':28} {'steps':16} {'outcome':20} {'client saw':24} {'t_last':6}")
    for row in sorted(rows):
        print("{:>2} {:17} {:4} {:22} {:>5} {:>3} {:>3} {:28} {:16} {:20} {:24} {:>6}".format(*row))
    print("\noutcomes:", dict(outcomes))
    print("originals per pod:", dict(originals), "| takeovers per pod:", dict(takeovers))
    orph = {k.split(":", 1)[1]: r.hgetall(k) for k in r.scan_iter(match="orphan:*")}
    print("ledger orphans (scanner fallback):", Counter(v.get("resolution", "?") for v in orph.values()) or "none",
          "| rejected_writes:", r.get("ledger:rejected_writes") or 0,
          "| stranded now:", len(list(r.scan_iter(match="stranded:*"))))
    try:
        from mq_probe import consumer_state
        print("handoff consumer:", consumer_state())
    except Exception as e:
        print("handoff consumer: n/a", type(e).__name__)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--tag", required=True)
    s.add_argument("--pods", default="a,b,c")
    s.add_argument("--sessions", type=int, default=20)
    s.add_argument("--prompt", default="lab steps 7 sleep 8")
    s.add_argument("--timeout", type=float, default=300)
    s.set_defaults(fn=start)
    p = sub.add_parser("report")
    p.add_argument("--tag", required=True)
    p.set_defaults(fn=report)
    args = ap.parse_args()
    args.fn(args)
