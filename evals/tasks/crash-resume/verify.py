def verify(workspace, output, log):
    target = workspace / "summary.txt"
    if not target.exists():
        return False, "summary.txt not created after resume"
    content = target.read_text()
    if "DONE" not in content:
        return False, "summary.txt missing DONE marker"
    return True, "resumed and completed"
