"""submit_result — Structured deliverable tool for sub-agents.

Sub-agents in contract_mode must call this tool to deliver results.
Writes a deliverable.json to disk and signals VCPU to stop.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from nimbus.core.tools.registry import ToolParameter, tool

logger = logging.getLogger("nimbus.submit_result")

# Field length limits (truncate AFTER JSON parse, never break JSON structure)
MAX_SUMMARY_LEN = 500
MAX_FINDING_LEN = 200
MAX_ARTIFACT_LEN = 200


def _truncate(s: str, limit: int) -> str:
    """Truncate string, appending '...' if over limit."""
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def submit_result_impl(
    summary: str,
    findings: List[str],
    artifacts: List[str],
    deliverable_path: str,
) -> Dict[str, Any]:
    """Core implementation (testable without tool decorator)."""
    # Truncate fields to prevent context bloat
    clean = {
        "summary": _truncate(summary, MAX_SUMMARY_LEN),
        "findings": [_truncate(f, MAX_FINDING_LEN) for f in findings],
        "artifacts": [_truncate(a, MAX_ARTIFACT_LEN) for a in artifacts],
    }

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(deliverable_path), exist_ok=True)

    # Write atomically: write to tmp then rename
    tmp_path = deliverable_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, deliverable_path)

    logger.info(f"Deliverable written to {deliverable_path}")
    return {"status": "DELIVERED", "path": deliverable_path}


@tool(
    name="submit_result",
    description=(
        "Submit structured results and end your task. "
        "You MUST call this tool when your work is complete."
    ),
    parameters=[
        ToolParameter(
            name="summary",
            type="string",
            description="Brief summary of what you accomplished (max 500 chars).",
            required=True,
        ),
        ToolParameter(
            name="findings",
            type="array",
            description="List of key findings, one string per item (max 200 chars each).",
            required=True,
            items={"type": "string"},
        ),
        ToolParameter(
            name="artifacts",
            type="array",
            description="List of file paths created or modified.",
            required=False,
            items={"type": "string"},
        ),
    ],
)
async def submit_result(
    summary: str = "",
    findings: Optional[List[str]] = None,
    artifacts: Optional[List[str]] = None,
    _sub_session_id: str = "",
    _vcpu: Any = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Submit structured results. Triggers VCPU interruption after delivery."""
    findings = findings or []
    artifacts = artifacts or []

    deliverable_path = f".nimbus/sessions/{_sub_session_id}/deliverable.json"

    # 1. Write deliverable to disk FIRST (before interruption)
    result = submit_result_impl(
        summary=summary,
        findings=findings,
        artifacts=artifacts,
        deliverable_path=deliverable_path,
    )

    # 2. THEN request VCPU interruption — sub-agent stops after this tool returns
    if _vcpu is not None:
        _vcpu.request_interruption()
        logger.info("VCPU interruption requested after submit_result")

    return {
        "output": f"✅ Results delivered. ({len(findings)} findings, {len(artifacts)} artifacts)",
        "ui_detail": result,
    }
