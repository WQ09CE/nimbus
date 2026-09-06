#!/usr/bin/env python3
"""nimbus-lab control: pods, vcompute, faults, turns. stdlib only.

  labctl pods up|down|status            start/stop pod units (lab-nimbus@a, @b)
  labctl vc recycle | vc chaos '{json}' vcompute: drop all leases / set chaos knobs
  labctl ledger [reset | dump SID]      Valkey ledger: pods, owners+epoch, orphans, rejected writes; dump = session stream
  labctl workers up|down|status         Temporal-arm workers (lab-temporal-worker@a, @b)
  labctl kill|term|freeze|thaw POD      SIGKILL / SIGTERM / SIGSTOP / SIGCONT the pod cgroup (POD = a|b|worker-a|worker-b)
  labctl mem POD MAX                    set MemoryMax (e.g. 300M) on the pod unit (runtime)
  labctl allow POD                      allow_always Bash/Write/Edit on POD (done automatically by pods up / turn)
  labctl llm POD mock|real              switch the pod's LLM rail (MockLLM vs pi-codex/gpt-5.6-luna via sidecar) + restart
  labctl repeat once|keyed|free         lab knob: repeat class override for Bash on both pods (+restart) — resume admission drills
  labctl perms POD | respond POD REQ allow_once|deny
  labctl turn POD "prompt" [--session SID] [--timeout S]
                                        create session (or reuse) + chat; prints SSE events
  labctl sessions [POD]                 list sessions seen by a pod
  labctl status POD SID                 session status
"""
import functools, json, os, subprocess, sys, time, urllib.request
print = functools.partial(print, flush=True)  # SSE lines must reach a pipe live
from pathlib import Path

LAB = Path(__file__).resolve().parent
UNIT = "lab-nimbus@{}"
WUNIT = "lab-temporal-worker@{}"

def unit_for(name):
    """'a'/'b' -> nimbus pod unit; 'worker-a'/'worker-b' -> Temporal worker unit."""
    return WUNIT.format(name[len("worker-"):]) if name.startswith("worker-") else UNIT.format(name)

def pod_port(pod):
    for line in (LAB / "pods" / f"{pod}.env").read_text().splitlines():
        if line.startswith("NIMBUS_PORT="):
            return int(line.split("=", 1)[1])
    raise SystemExit(f"no NIMBUS_PORT for pod {pod}")

def base(pod): return f"http://127.0.0.1:{pod_port(pod)}/api/v1"

def sc(*args): return subprocess.run(["systemctl", "--user", *args], text=True, capture_output=True)

def http(method, url, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None

def pods(): return sorted(p.stem for p in (LAB / "pods").glob("*.env"))

def cmd_pods(action):
    if action == "up":
        for p in pods():
            ok = sc("start", UNIT.format(p)).returncode == 0 and wait_health(p)
            print(p, "started" if ok else "FAILED")
            if ok: cmd_allow(p)
    elif action == "down":
        for p in pods(): sc("stop", UNIT.format(p)); print(p, "stopped")
    else:
        for p in pods():
            act = sc("is-active", UNIT.format(p)).stdout.strip()
            pid = sc("show", "-p", "MainPID", "--value", UNIT.format(p)).stdout.strip()
            try: h = http("GET", base(p) + "/health", timeout=2)
            except Exception as e: h = f"unreachable ({type(e).__name__})"
            print(f"{p:3} {act:9} pid={pid:7} {h}")

def cmd_signal(sig, pod):
    r = sc("kill", "-s", sig, "--kill-whom=all", unit_for(pod)); print(pod, sig, "ok" if r.returncode == 0 else r.stderr.strip())
    print(f"  t={time.strftime('%H:%M:%S')}")

def cmd_workers(action):
    for w in ("a", "b"):
        u = WUNIT.format(w)
        if action == "up": print("worker-" + w, "started" if sc("start", u).returncode == 0 else "FAILED")
        elif action == "down": sc("stop", u); print("worker-" + w, "stopped")
        else: print(f"worker-{w:2} {sc('is-active', u).stdout.strip():9} pid={sc('show', '-p', 'MainPID', '--value', u).stdout.strip()}")

def cmd_mem(pod, mx):
    r = sc("set-property", "--runtime", unit_for(pod), f"MemoryMax={mx}"); print(pod, "MemoryMax", mx, "ok" if r.returncode == 0 else r.stderr.strip())

def cmd_llm(pod, rail):
    """Flip a pod between the deterministic MockLLM rail and the real pi-codex rail, then restart it."""
    f = LAB / "pods" / f"{pod}.env"
    lines = [l for l in f.read_text().splitlines() if not l.startswith("NIMBUS_LLM=")]
    if rail == "mock": lines.append("NIMBUS_LLM=mock")
    elif rail != "real": raise SystemExit("rail must be mock|real")
    f.write_text("\n".join(lines) + "\n")
    sc("restart", UNIT.format(pod)); ok = wait_health(pod)
    print(pod, "llm rail =", rail, "(restarted)" if ok else "(restart FAILED)")
    if ok: cmd_allow(pod)

def cmd_ledger(*args):
    """Valkey ledger view (pods, turn owners+epoch, orphans, rejected writes); 'reset' clears; 'dump SID' prints a session stream."""
    root = LAB.parent
    r = subprocess.run([str(root / ".venv" / "bin" / "python"), "-m", "nimbus.infra.ledger", *args],
                       text=True, capture_output=True, env={**os.environ, "NIMBUS_LEDGER_URL": "redis://127.0.0.1:6379"})
    print(r.stdout.strip() or r.stderr.strip())

def cmd_repeat(cls):
    """Lab knob: NIMBUS_REPEAT_OVERRIDE=Bash=<cls> on both pods (+restart). 'once' = declaration default (fast-fail path),
    'keyed' = treat the lab Bash step as idempotent so the resume path runs."""
    if cls not in ("once", "keyed", "free"): raise SystemExit("repeat must be once|keyed|free")
    for pod in pods():
        f = LAB / "pods" / f"{pod}.env"
        lines = [l for l in f.read_text().splitlines() if not l.startswith("NIMBUS_REPEAT_OVERRIDE=")]
        if cls != "once": lines.append(f"NIMBUS_REPEAT_OVERRIDE=Bash={cls}")
        f.write_text("\n".join(lines) + "\n")
        sc("restart", UNIT.format(pod)); ok = wait_health(pod)
        print(pod, "Bash repeat =", cls, "(restarted)" if ok else "(restart FAILED)")
        if ok: cmd_allow(pod)

def cmd_vc(action, arg=None):
    url = "http://127.0.0.1:8793/v1"
    if action == "recycle": print(http("POST", url + "/chaos/recycle", {}))
    elif action == "chaos": print(http("POST", url + "/chaos", json.loads(arg or "{}")))
    else: print(http("GET", url + "/health"))

ALLOW_TOOLS = ("Bash", "Write", "Edit")

def cmd_allow(pod):
    for t in ALLOW_TOOLS:
        http("PUT", f"{base(pod)}/permissions/rules/{t}", {"decision": "allow_always"})
    print(pod, "allow_always:", ",".join(ALLOW_TOOLS))

def wait_health(pod, tries=30):
    for _ in range(tries):
        try: http("GET", base(pod) + "/health", timeout=2); return True
        except Exception: time.sleep(0.5)
    return False

def cmd_perms(pod): print(json.dumps(http("GET", base(pod) + "/permissions/pending"), indent=1)[:1500])

def cmd_respond(pod, req, decision): print(http("POST", f"{base(pod)}/permissions/{req}/respond", {"decision": decision}))

def sse(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    t0 = time.monotonic(); ev = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode(errors="replace").rstrip("\n")
            if line.startswith("event:"): ev = line[6:].strip()
            elif line.startswith("data:"):
                d = line[5:].strip()
                try: d = json.loads(d)
                except Exception: pass
                short = json.dumps(d, ensure_ascii=False)[:160] if not isinstance(d, str) else d[:160]
                if ev not in ("heartbeat", "message"):  # keep the trace readable; text deltas are noise
                    print(f"  +{time.monotonic()-t0:6.2f}s {ev or 'data':<16} {short}")
                if ev == "done":  # server keeps the SSE open after the turn; 'done' is the terminal event
                    print(f"  turn finished in {time.monotonic()-t0:.2f}s")
                    return

def cmd_turn(pod, prompt, session=None, timeout=120):
    b = base(pod)
    cmd_allow(pod)  # rules are per-process; a restarted pod forgets them
    if not session:
        s = http("POST", b + "/sessions", {"name": f"lab-{pod}-{int(time.time())}"})
        session = s.get("id") or s.get("session_id"); print(f"session {session} on pod {pod}")
    print(f"turn: {prompt!r}")
    try: sse(f"{b}/sessions/{session}/chat", {"content": prompt}, timeout)
    except Exception as e: print(f"  stream ended: {type(e).__name__}: {e}")
    print(f"session={session}")

def main(a):
    if not a or a[0] in ("-h", "--help"): print(__doc__); return
    c = a[0]
    if c == "pods": cmd_pods(a[1] if len(a) > 1 else "status")
    elif c == "workers": cmd_workers(a[1] if len(a) > 1 else "status")
    elif c in ("kill", "term", "freeze", "thaw"):
        cmd_signal({"kill": "SIGKILL", "term": "SIGTERM", "freeze": "SIGSTOP", "thaw": "SIGCONT"}[c], a[1])
    elif c == "mem": cmd_mem(a[1], a[2])
    elif c == "allow": cmd_allow(a[1])
    elif c == "ledger": cmd_ledger(*a[1:])
    elif c == "llm": cmd_llm(a[1], a[2])
    elif c == "repeat": cmd_repeat(a[1])
    elif c == "perms": cmd_perms(a[1])
    elif c == "respond": cmd_respond(a[1], a[2], a[3])
    elif c == "vc": cmd_vc(a[1] if len(a) > 1 else "health", a[2] if len(a) > 2 else None)
    elif c == "turn":
        sid = a[a.index("--session") + 1] if "--session" in a else None
        to = float(a[a.index("--timeout") + 1]) if "--timeout" in a else 120
        cmd_turn(a[1], a[2], sid, to)
    elif c == "sessions": print(json.dumps(http("GET", base(a[1] if len(a) > 1 else "a") + "/sessions"), indent=1)[:2000])
    elif c == "status": print(http("GET", f"{base(a[1])}/sessions/{a[2]}/status"))
    else: print(__doc__); sys.exit(2)

if __name__ == "__main__": main(sys.argv[1:])
