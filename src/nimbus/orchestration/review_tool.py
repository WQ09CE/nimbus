"""
ReviewTool — AI Review Committee Meta-Tool.

Spawns parallel reviewer processes using different LLM models,
collects their reviews, and saves results to docs/.

Usage:
    review_tool = ReviewTool(agent_os=agent_os)
    agent_os.register_tool("ReviewCommittee", review_tool.review, ...)
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from nimbus.agentos import AgentOS
from nimbus.adapters.llm_factory import create_llm_client, get_default_review_models


# =============================================================================
# Tool Definition (for AgentOS.register_tool)
# =============================================================================

REVIEW_TOOL_DEF = {
    "name": "ReviewCommittee",
    "description": (
        "Submit code or architecture for parallel review by multiple AI models "
        "(e.g. Claude, GPT, Gemini). Each model reviews independently, then "
        "results are collected for you to synthesize. "
        "Reviews are saved to docs/reviews/ for persistence."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "The code, architecture doc, or design to review. "
                    "Can be file contents or a description."
                ),
            },
            "focus": {
                "type": "string",
                "description": (
                    "Review focus area. Examples: 'security', 'performance', "
                    "'architecture', 'code-quality', 'all'. Default: 'all'"
                ),
            },
            "models": {
                "type": "string",
                "description": (
                    "Comma-separated model list to use as reviewers. "
                    "Default: reads from ~/.nimbus/config.json or uses "
                    "anthropic/claude-opus-4-6,openai/gpt-5.3-codex,google/gemini-3-pro-high"
                ),
            },
            "title": {
                "type": "string",
                "description": (
                    "Short title for this review (used in filename). "
                    "Example: 'agentos-architecture'. Default: 'review'"
                ),
            },
        },
        "required": ["content"],
    },
}


# =============================================================================
# Review Prompt Template
# =============================================================================

REVIEWER_PROMPT_TEMPLATE = """You are an expert code/architecture reviewer.
You are one of several reviewers on an AI Review Committee. Your identity: {model_name}

## Review Focus: {focus}

## Content to Review

{content}

## Instructions

Provide a thorough, structured review:

1. **Overall Assessment** — Score (1-10) with one-line summary
2. **Strengths** — What's done well (be specific, cite sections)
3. **Issues Found** — List each issue with:
   - Severity: 🔴 Critical / 🟡 Major / 🔵 Minor
   - Location: which section/function/line
   - Description: what's wrong
   - Suggestion: how to fix
4. **Architecture/Design Observations** — Higher-level insights
5. **Actionable Recommendations** — Top 3 things to improve, prioritized

Be honest, specific, and constructive. Don't pad with generic praise.
If the content is excellent, say so briefly and focus on subtle improvements."""


# =============================================================================
# ReviewTool
# =============================================================================


class ReviewTool:
    """
    AI Review Committee — parallel multi-model code/architecture review.

    Registered on AgentOS as a Meta-Tool (similar to DispatchTool).
    Spawns pure-reasoning reviewer processes with different LLM models,
    waits for all to complete in parallel, and persists results.
    """

    def __init__(
        self,
        agent_os: AgentOS,
        workspace: Optional[Path] = None,
    ):
        self._agent_os = agent_os
        self._workspace = workspace or Path.cwd()

    async def review(
        self,
        content: str,
        focus: str = "all",
        models: str = "",
        title: str = "review",
        **kwargs,
    ) -> str:
        """
        Handle ReviewCommittee tool calls from Core Agent.

        1. Parse model list
        2. Spawn a pure-reasoning process for each model
        3. Wait for all reviewers in parallel
        4. Save results to docs/reviews/
        5. Return formatted results for Core Agent to synthesize
        """
        # Parse model list
        if models and models.strip():
            model_list = [m.strip() for m in models.split(",") if m.strip()]
        else:
            model_list = get_default_review_models()

        logger.info(
            f"🏛️ Review Committee: {len(model_list)} reviewers, focus={focus}, "
            f"title={title}"
        )

        # Spawn reviewer processes in parallel
        pids = []
        llm_clients = []  # Track for cleanup

        for model in model_list:
            try:
                llm = await create_llm_client(model)
                llm_clients.append(llm)
            except Exception as e:
                logger.error(f"Failed to create LLM client for {model}: {e}")
                continue

            review_prompt = REVIEWER_PROMPT_TEMPLATE.format(
                model_name=model,
                focus=focus,
                content=content,
            )

            pid = self._agent_os.spawn(
                goal=review_prompt,
                role="reviewer",
                llm_client=llm,
                max_iterations=1,       # Pure reasoning, single turn
                tools_override=[],      # No tools needed
                system_rules="You are an expert code reviewer. Respond with a thorough, structured review.",
            )
            pids.append((model, pid))
            logger.info(f"  📋 Spawned reviewer: {model} → {pid}")

        if not pids:
            return "[Error] Failed to create any reviewer processes. Check model configuration and pi-ai bridge."

        # Wait for all reviewers in parallel
        all_pids = [pid for _, pid in pids]
        logger.info(f"  ⏳ Waiting for {len(all_pids)} reviewers...")
        start_time = time.time()

        results = await self._agent_os.wait_all(all_pids, timeout=120.0)

        elapsed = time.time() - start_time
        logger.info(f"  ✅ All reviewers done in {elapsed:.1f}s")

        # Collect reviews
        reviews = []
        for model, pid in pids:
            result = results.get(pid)
            if result and result.output:
                review_text = result.output
            elif result and result.fault:
                review_text = f"(Review failed: {result.fault.message})"
            else:
                review_text = "(No response from reviewer)"
            reviews.append({"model": model, "review": review_text})

        # Save to docs/reviews/
        saved_path = self._save_reviews(reviews, focus, title, elapsed)

        # Cleanup LLM clients
        for llm in llm_clients:
            try:
                await llm.__aexit__(None, None, None)
            except Exception:
                pass

        # Format output for Core Agent
        output = f"## 🏛️ AI Review Committee Results\n\n"
        output += f"**Focus:** {focus} | **Reviewers:** {len(reviews)} | **Time:** {elapsed:.1f}s\n\n"

        for r in reviews:
            output += f"### 📋 Review by `{r['model']}`\n\n"
            output += r["review"]
            output += "\n\n---\n\n"

        output += f"📁 Reviews saved to: `{saved_path}`\n\n"
        output += (
            "## 📝 Synthesis Required\n\n"
            "Above are the individual reviews from all committee members.\n"
            "Please synthesize them into a final assessment covering:\n"
            "- **Consensus**: What all reviewers agree on\n"
            "- **Divergence**: Where reviewers disagree (and your judgment)\n"
            "- **Priority Actions**: Top recommendations, ranked\n"
        )

        return output

    def _save_reviews(
        self,
        reviews: List[Dict[str, str]],
        focus: str,
        title: str,
        elapsed: float,
    ) -> str:
        """Save review results to docs/reviews/ as markdown."""
        # Create directory
        reviews_dir = self._workspace / "docs" / "reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = "".join(c if c.isalnum() or c in "-_" else "-" for c in title)
        filename = f"{timestamp}_{safe_title}.md"
        filepath = reviews_dir / filename

        # Build markdown content
        lines = [
            f"# AI Review Committee: {title}",
            "",
            f"- **Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Focus**: {focus}",
            f"- **Reviewers**: {len(reviews)}",
            f"- **Total Time**: {elapsed:.1f}s",
            "",
            "---",
            "",
        ]

        for r in reviews:
            lines.append(f"## Review by `{r['model']}`")
            lines.append("")
            lines.append(r["review"])
            lines.append("")
            lines.append("---")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"  📁 Reviews saved to {filepath}")

        # Return relative path
        try:
            return str(filepath.relative_to(self._workspace))
        except ValueError:
            return str(filepath)
