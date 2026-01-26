"""Code search tool with text and optional semantic search.

This module provides a unified search tool that combines:
- Text search (grep-based, fast and precise)
- Semantic search (embedding-based, for natural language queries)

Example:
    >>> from pathlib import Path
    >>> result = await code_search("authenticate user", workspace=Path("/project"))
    >>> print(result)
    src/auth.py:42: def authenticate_user(token):
    src/auth.py:43:     # Authenticate user with JWT token
"""

from pathlib import Path
from typing import Any, List, Literal, Optional

from .base import ToolParameter, tool
from .grep import grep_content
from .sandbox import Sandbox, SandboxError


@tool(
    name="Search",
    description="Search code in the workspace. Combines text search (grep) with optional semantic search for natural language queries.",
    parameters=[
        ToolParameter(
            "query",
            "string",
            "Search query - can be regex pattern or natural language",
            required=True,
        ),
        ToolParameter(
            "path",
            "string",
            "Directory or file to search in. Defaults to workspace root.",
            required=False,
            default=".",
        ),
        ToolParameter(
            "type",
            "string",
            "File type filter (e.g., 'py', 'js', 'ts'). Filters by file extension.",
            required=False,
        ),
        ToolParameter(
            "glob",
            "string",
            "Glob pattern to filter files (e.g., '*.py', '**/*.ts')",
            required=False,
        ),
        ToolParameter(
            "mode",
            "string",
            "Search mode: 'text' (grep), 'semantic' (embedding), or 'hybrid' (both). Defaults to 'text'.",
            required=False,
            default="text",
        ),
        ToolParameter(
            "case_sensitive",
            "boolean",
            "Case sensitive search. Defaults to False.",
            required=False,
            default=False,
        ),
        ToolParameter(
            "context_lines",
            "integer",
            "Number of context lines to show around matches. Defaults to 2.",
            required=False,
            default=2,
        ),
        ToolParameter(
            "limit",
            "integer",
            "Maximum number of results to return. Defaults to 20.",
            required=False,
            default=20,
        ),
    ],
)
async def code_search(
    query: str,
    path: str = ".",
    type: Optional[str] = None,
    glob: Optional[str] = None,
    mode: Literal["text", "semantic", "hybrid"] = "text",
    case_sensitive: bool = False,
    context_lines: int = 2,
    limit: int = 20,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """Search code in the workspace.

    Provides unified search combining text search (grep-based) and optional
    semantic search (embedding-based). Text search is fast and precise for
    exact matches, while semantic search understands natural language queries.

    Features:
        - Text search with regex support
        - File type and glob filtering
        - Context lines around matches
        - Case sensitivity control
        - Result limiting

    Args:
        query: Search query (regex pattern or natural language).
        path: Directory or file to search in. Defaults to ".".
        type: File type filter (e.g., "py", "js").
        glob: Glob pattern to filter files.
        mode: Search mode - "text", "semantic", or "hybrid".
        case_sensitive: Whether search is case sensitive.
        context_lines: Lines of context around matches.
        limit: Maximum results to return.
        workspace: Workspace directory for sandbox validation.

    Returns:
        Formatted search results with file paths, line numbers, and content.

    Raises:
        SandboxError: If path escapes workspace.
        ValueError: If query is empty.

    Example:
        >>> result = await code_search("def authenticate", type="py")
        >>> print(result)
        src/auth.py:42: def authenticate_user(token):
        src/auth.py:43:     \"\"\"Authenticate user with JWT token.\"\"\"
    """
    # Validate parameters
    if not query:
        raise ValueError("query cannot be empty")

    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    # Determine workspace
    if workspace is None:
        workspace = Path.cwd()

    # Validate path with sandbox
    sandbox = Sandbox(workspace)
    try:
        base_path = sandbox.validate(path, must_exist=True)
    except SandboxError:
        raise
    except FileNotFoundError:
        raise FileNotFoundError(f"Search path not found: {path}")

    results: List[str] = []

    # Text search using grep
    if mode in ("text", "hybrid"):
        try:
            grep_result = await grep_content(
                pattern=query,
                path=path,
                type=type,
                glob=glob,
                output_mode="content",
                head_limit=limit,
                workspace=workspace,
                **{
                    "-i": not case_sensitive,
                    "-C": context_lines,
                },
            )
            if grep_result and "No matches found" not in grep_result:
                results.append(grep_result)
        except Exception as e:
            # Don't fail on grep errors, continue with other modes
            if mode == "text":
                raise
            results.append(f"[Text search error: {e}]")

    # Semantic search (placeholder for future implementation)
    if mode in ("semantic", "hybrid"):
        # TODO: Integrate with vector store for semantic search
        # For now, provide a helpful message
        if mode == "semantic":
            results.append(
                "[Semantic search not yet implemented. "
                "Use mode='text' for grep-based search.]"
            )
        # In hybrid mode, we already have text results

    # Format output
    if not results:
        return f"No matches found for '{query}' in '{path}'"

    return "\n".join(results)


# Alias for backward compatibility
search = code_search
