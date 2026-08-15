def verify(workspace, output, log):
    if "NIMBUS-EVAL-7431" not in output:
        return False, "token from data.txt missing in final output"
    tools = [e for e in log.events if e.type == "tool/result"]
    if not tools:
        return False, "no tool/result event — model answered without running the tool"
    return True, "ok"
