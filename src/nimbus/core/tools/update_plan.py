"""update_plan — the agent's pinned task plan (attention anchor).

Replaces the old write-scratchpad-every-step discipline. The plan lives in
the MMU's anchor region: always near the top of the assembled context,
immune to compaction (it is not part of the message stream), and re-stated
to the model on every step — Manus-style "recitation" against goal drift.

Durability is NOT this tool's job (the session event log records every tool
result); the plan is working memory. Each update also:
- emits a `plan/updated` session-log event (rendered by the Trace panel),
- mirrors the rendered plan to the session scratchpad file for humans.
"""

import logging
from pathlib import Path
from typing import Any, List, Optional

from .registry import ToolParameter, ToolTraits, tool

logger = logging.getLogger("nimbus.update_plan")


def _render(todos: List[str], notes: Optional[str]) -> str:
    lines = ["## Task Plan"]
    for t in todos:
        text = str(t).strip()
        if not text:
            continue
        if text.startswith(("[x] ", "[X] ", "[ ] ")):
            lines.append(f"- {text}")
        else:
            lines.append(f"- [ ] {text}")
    if notes and notes.strip():
        lines.append("")
        lines.append("### Notes")
        lines.append(notes.strip())
    return "\n".join(lines)


@tool(
    name="update_plan",
    description=(
        "Maintain your task plan — a pinned checklist that stays visible at "
        "the top of your context and survives compaction. Call once at the "
        "start with your TODO list, then again when an item completes or "
        "the plan changes. Each call REPLACES the whole plan, so always "
        "send the complete list. Prefix finished items with '[x] ' and "
        "pending items with '[ ] '. This is working memory, not a report — "
        "keep it short."
    ),
    parameters=[
        ToolParameter(
            name="todos",
            type="array",
            description="The complete todo list, one string per item; prefix done items with '[x] '.",
            required=True,
            items={"type": "string"},
        ),
        ToolParameter(
            name="notes",
            type="string",
            description="Optional key findings or intermediate conclusions worth keeping visible.",
            required=False,
        ),
    ],
    traits=ToolTraits(side_effects="none"),
)
async def update_plan(
    todos: Optional[List[str]] = None,
    notes: Optional[str] = None,
    _mmu: Any = None,
    _plan_mirror_path: str = "",
    **kwargs: Any,
) -> str:
    todos = todos or []
    rendered = _render(todos, notes)

    if _mmu is not None and hasattr(_mmu, "set_plan"):
        _mmu.set_plan(rendered)

    # Human-readable mirror (best-effort; never the source of truth).
    if _plan_mirror_path:
        try:
            p = Path(_plan_mirror_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(rendered + "\n", encoding="utf-8")
        except OSError as e:
            logger.debug(f"plan mirror write failed ({e}); ignoring")

    done = sum(1 for t in todos if str(t).startswith(("[x] ", "[X] ")))
    return f"Plan updated: {done}/{len(todos)} done."
