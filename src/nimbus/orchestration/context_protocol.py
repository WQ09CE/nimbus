"""
Context Protocol - Structured Goal Documents for Specialist Agents.

Replaces LLM-based goal summarization with programmatic composition.
Zero information loss, deterministic, no extra LLM call.

Phase 1 addition: GoalDocument.render() now expands nimfs:// references
in the context field before truncation, so specialists receive the full
content of large artifacts without hitting the 16K context cap.
"""

import re
from dataclasses import dataclass, field
from typing import List

# Max depth to expand nested nimfs:// references (prevents infinite loops)
_NIMFS_EXPAND_MAX_DEPTH = 2

# Regex to match nimfs:// references in text
_NIMFS_REF_PATTERN = re.compile(r"nimfs://(?:artifact|memory)/[\w\-]+")


def _expand_nimfs_refs(text: str, workspace: str, depth: int = 0) -> str:
    """
    Expand nimfs:// references in text by fetching their content from NimFS.

    Expands up to _NIMFS_EXPAND_MAX_DEPTH levels to prevent loops.
    Silently leaves unresolvable references unexpanded (graceful degradation).

    Args:
        text:      Text potentially containing nimfs:// references.
        workspace: Current workspace path for NimFSManager.
        depth:     Current expansion depth (internal recursion guard).

    Returns:
        Text with nimfs:// references replaced by their content.
    """
    if depth >= _NIMFS_EXPAND_MAX_DEPTH:
        return text

    refs = _NIMFS_REF_PATTERN.findall(text)
    if not refs:
        return text

    try:
        from nimbus.core.nimfs.manager import NimFSManager
        from nimbus.core.nimfs.models import (
            ArtifactExpiredError,
            ArtifactNotFoundError,
        )
        from nimbus.core.nimfs.project_id import parse_nimfs_ref
    except ImportError:
        return text  # NimFS not available — leave refs as-is

    manager = NimFSManager(workspace)

    for ref in set(refs):  # deduplicate
        try:
            kind, item_id = parse_nimfs_ref(ref)
            if kind == "artifact":
                content = manager.read_artifact(ref)
                manifest = manager.get_artifact_manifest(ref)
                expanded = (
                    f"<!-- NimFS Artifact: {ref} | type={manifest.type} | "
                    f"producer={manifest.producer} | {manifest.size_bytes:,}B -->\n"
                    f"{content}"
                )
            elif kind == "memory":
                content = manager.read_memory(item_id, layer=1)  # L1 overview by default
                expanded = f"<!-- NimFS Memory: {ref} -->\n{content}"
            else:
                continue

            # Recursively expand nested refs (up to depth limit)
            expanded = _expand_nimfs_refs(expanded, workspace, depth + 1)
            text = text.replace(ref, expanded)

        except (ArtifactExpiredError, ArtifactNotFoundError) as e:
            # Leave the ref in place with an error annotation
            text = text.replace(ref, f"[NimFS: {ref} — {type(e).__name__}]")
        except Exception:
            # Unknown error — leave ref unexpanded
            pass

    return text


@dataclass
class GoalDocument:
    """
    Structured goal document for specialist agents.

    Composed programmatically from orchestrator's tool call arguments.
    Passed verbatim to the specialist — no LLM summarization.

    Phase 1: context field supports nimfs:// references.
    When render() is called with a workspace, all nimfs:// references are
    expanded inline before the 16K truncation is applied. This means:
    - Orchestrator can pass a short "nimfs://artifact/task-1-abc" reference
    - Specialist receives the full artifact content (no size limit)
    - 16K cap still applies as a safety net for the *expanded* content
    """
    mission: str                          # The specific task (verbatim from orchestrator)
    context: str = ""                     # May contain nimfs:// references
    workspace: str = ""                   # Workspace path (needed for NimFS expansion)
    constraints: List[str] = field(default_factory=list)
    expected_output: str = ""

    # Context cap to prevent specialist context overflow.
    # Applied AFTER nimfs:// expansion, so large artifacts are fully included
    # up to this limit (truncation only kicks in if total expanded content exceeds it).
    MAX_CONTEXT_CHARS: int = 16_000

    # Whether to expand nimfs:// references during render().
    # Can be disabled for testing or when NimFS is unavailable.
    expand_nimfs_refs: bool = True

    def render(self) -> str:
        """
        Render the goal document as a structured markdown string.

        If workspace is set and expand_nimfs_refs is True, any nimfs://
        references in the context field are expanded to their full content
        before the 16K truncation is applied.
        """
        parts = [f"## Mission\n{self.mission}"]

        if self.context:
            ctx = self.context

            # Phase 1: expand nimfs:// references if workspace is available
            if self.expand_nimfs_refs and self.workspace:
                ctx = _expand_nimfs_refs(ctx, self.workspace)

            if len(ctx) > self.MAX_CONTEXT_CHARS:
                ctx = ctx[:self.MAX_CONTEXT_CHARS] + "\n\n[Context truncated]"
            parts.append(f"## Context\n{ctx}")

        if self.workspace:
            parts.append(f"## Workspace\n{self.workspace}")

        if self.constraints:
            constraints_str = "\n".join(f"- {c}" for c in self.constraints)
            parts.append(f"## Constraints\n{constraints_str}")

        if self.expected_output:
            parts.append(f"## Expected Output\n{self.expected_output}")

        return "\n\n".join(parts)
