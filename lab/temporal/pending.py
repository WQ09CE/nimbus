"""Print one line about a workflow's pending activity from `temporal workflow describe -o json` on stdin."""

import json
import sys

d = json.load(sys.stdin)
st = d.get("workflowExecutionInfo", {}).get("status", "?").replace("WORKFLOW_EXECUTION_STATUS_", "")
pa = d.get("pendingActivities") or []
if not pa:
    print(f"wf={st} pending=none")
    sys.exit()
a = pa[0]
hb = str(a.get("lastHeartbeatTime", ""))[11:19]
fail = str((a.get("lastFailure") or {}).get("message", ""))[:70]
print(f"wf={st} act={a.get('activityType', {}).get('name')} state={str(a.get('state', '')).replace('PENDING_ACTIVITY_STATE_', '')} "
      f"attempt={a.get('attempt')} worker={a.get('lastWorkerIdentity')} lastHB={hb} lastFail={fail}")
