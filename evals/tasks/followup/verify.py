def verify(workspace, output, log):
    turns = sum(1 for e in log.events if e.type == "turn/start")
    if turns < 2:
        return False, f"expected 2 turns (goal + follow-up), got {turns}"
    if "BLUEFIN" not in output.upper():
        return False, "codename from turn 1 not recalled in turn 2"
    return True, "ok"
