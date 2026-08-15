def verify(workspace, output, log):
    compactions = sum(1 for e in log.events if e.type == "compaction/applied")
    if compactions == 0:
        return False, "compaction never triggered — window config not exercised"
    if "MAPLE-9271" not in output:
        return False, f"access code lost across compaction ({compactions} compactions)"
    return True, f"code survived {compactions} compaction(s)"
