import subprocess
import sys


def verify(workspace, output, log):
    target = workspace / "add.py"
    if not target.exists():
        return False, "add.py was not created"
    check = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); import add; "
         "assert add.add(2, 3) == 5; print('OK')",
         str(workspace)],
        capture_output=True, text=True, timeout=15,
    )
    if check.returncode != 0:
        return False, f"add.py failed the check: {check.stderr[:200]}"
    names = set()
    for e in log.events:
        if e.type == "assistant/message":
            for tc in e.data.get("message", {}).get("tool_calls") or []:
                names.add(tc.get("function", {}).get("name"))
    if not names & {"Write", "write_file", "Bash"}:
        return False, f"expected Write/Bash tool usage, saw {sorted(names)}"
    return True, "ok"
