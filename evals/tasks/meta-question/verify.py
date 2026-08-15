"""Capability meta-question must not trip the narrate-not-act guard.

Regression for a real false positive: answering "你支持哪些工具" lists tool
names with phrases like "让我…运行…命令", which a flat tail-window matcher
mistook for an unfulfilled tool announcement and nudged twice.
"""


def verify(workspace, output, log):
    nudges = [
        e for e in log.events
        if e.type == "user/message"
        and "described a tool action" in str(
            e.data.get("message", {}).get("content", "")
        )
    ]
    if nudges:
        return False, f"narrate guard false-fired {len(nudges)}x on a capability question"
    text = (output or "").lower()
    # On-topic sanity only — models may describe tools in English names or
    # Chinese descriptors; the load-bearing assertion is the nudge check above.
    if not any(t in text for t in (
        "bash", "write", "read", "grep", "edit",
        "工具", "命令", "文件", "读取", "写入", "搜索",
    )):
        return False, "reply does not describe the toolset"
    return True, "clean capability answer, no guard nudge"
