"""Multi-step task that baits premature stop.

Weak models tend to read a.txt, announce "接下来我将读取 b.txt" and stop.
After termination inversion (text ends the turn by default), the narrate
guard is the only thing standing between that announcement and a premature
turn end — this task measures whether it holds.
"""


def verify(workspace, output, log):
    c = workspace / "c.txt"
    if not c.exists():
        return False, "c.txt not created (stopped before finishing)"
    content = c.read_text()
    missing = [code for code in ("ALPHA-7391", "BRAVO-2846") if code not in content]
    if missing:
        return False, f"c.txt missing codes: {missing}"
    nudges = sum(
        1 for e in log.events
        if e.type == "user/message"
        and "did not call any tool" in str(e.data.get("message", {}).get("content", ""))
    )
    return True, f"both codes written; narrate nudges used: {nudges}"
