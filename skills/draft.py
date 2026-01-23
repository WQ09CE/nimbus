"""Draft/artifact generation skills for OpenNotebook."""

from typing import Any, Dict
import json
import re


def create_draft_skill(llm_client):
    """Create draft artifact generation skills.

    Args:
        llm_client: LLM client with async complete() method.

    Returns:
        Dictionary of draft skill functions.
    """

    async def draft_outline(topic: str, context: str = "") -> Dict[str, Any]:
        """Generate an outline for a topic.

        Args:
            topic: Topic to outline.
            context: Optional reference material.

        Returns:
            Artifact dictionary with outline data.
        """
        prompt = f"""Generate a detailed outline for the following topic.

Topic: {topic}
{"Reference material: " + context if context else ""}

Please output in JSON format:
{{
    "title": "Outline title",
    "sections": [
        {{"heading": "Section 1", "points": ["Point 1", "Point 2"]}},
        {{"heading": "Section 2", "points": ["Point 1", "Point 2"]}}
    ]
}}

Output JSON only, no other content."""

        response = await llm_client.complete(prompt)

        try:
            # Extract JSON from response
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return {
                    "type": "artifact",
                    "artifact_type": "outline",
                    "data": data
                }
        except (json.JSONDecodeError, AttributeError):
            pass

        # Fallback if JSON parsing fails
        return {
            "type": "artifact",
            "artifact_type": "outline",
            "data": {"title": topic, "sections": [], "raw": response}
        }

    async def draft_summary(content: str, max_length: int = 300) -> Dict[str, Any]:
        """Generate a summary of content.

        Args:
            content: Content to summarize.
            max_length: Maximum summary length in characters.

        Returns:
            Artifact dictionary with summary data.
        """
        # Truncate input if too long
        truncated_content = content[:2000] if len(content) > 2000 else content

        prompt = f"""Please generate a concise summary of the following content.
The summary should not exceed {max_length} characters.

Content:
{truncated_content}

Summary:"""

        summary = await llm_client.complete(prompt)

        return {
            "type": "artifact",
            "artifact_type": "summary",
            "data": {
                "summary": summary.strip(),
                "source_length": len(content)
            }
        }

    async def draft_notes(content: str, style: str = "bullet") -> Dict[str, Any]:
        """Extract key notes from content.

        Args:
            content: Content to extract notes from.
            style: Note format - "bullet" or "paragraph".

        Returns:
            Artifact dictionary with notes data.
        """
        style_desc = "bullet points" if style == "bullet" else "paragraphs"

        # Truncate input if too long
        truncated_content = content[:2000] if len(content) > 2000 else content

        prompt = f"""Extract key notes from the following content using {style_desc} format.

Content:
{truncated_content}

Notes:"""

        notes = await llm_client.complete(prompt)

        return {
            "type": "artifact",
            "artifact_type": "notes",
            "data": {
                "notes": notes.strip(),
                "style": style
            }
        }

    async def draft_table(content: str, columns: str = "") -> Dict[str, Any]:
        """Generate a comparison/summary table from content.

        Args:
            content: Content to tabulate.
            columns: Optional comma-separated column names.

        Returns:
            Artifact dictionary with table data.
        """
        column_hint = f"Use these columns: {columns}" if columns else ""

        # Truncate input if too long
        truncated_content = content[:2000] if len(content) > 2000 else content

        prompt = f"""Organize the following content into a structured table.
{column_hint}

Content:
{truncated_content}

Please output in JSON format:
{{
    "headers": ["Column1", "Column2", ...],
    "rows": [
        ["value1", "value2", ...],
        ["value1", "value2", ...]
    ]
}}

Output JSON only, no other content."""

        response = await llm_client.complete(prompt)

        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return {
                    "type": "artifact",
                    "artifact_type": "table",
                    "data": data
                }
        except (json.JSONDecodeError, AttributeError):
            pass

        return {
            "type": "artifact",
            "artifact_type": "table",
            "data": {"headers": [], "rows": [], "raw": response}
        }

    return {
        "outline": draft_outline,
        "summary": draft_summary,
        "notes": draft_notes,
        "table": draft_table,
    }
