"""
Code Agent Application.

Architecture Layer: 2 (Application)
Von Neumann Role: Process Definition

This is the Code Agent application built on top of Agent OS Kernel.
It provides code exploration, modification, and execution capabilities
using real LLM providers (Gemini, OpenRouter, etc.) and real tools.

Example:
    >>> from nimbus.apps import CodeAgent
    >>>
    >>> async def main():
    ...     agent = CodeAgent(workspace="/path/to/project")
    ...     result = await agent.run("Find all Python files and count lines of code")
    ...     print(result["output"])
"""

__layer__ = 2
__role__ = "Application"

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set

from nimbus.kernel import AgentOS
from nimbus.llm.factory import create_llm_client
from nimbus.tools.base import ToolRegistry
from nimbus.tools.bash import bash_command
from nimbus.tools.edit import edit_file
from nimbus.tools.glob import glob_files
from nimbus.tools.grep import grep_content
from nimbus.tools.read import read_file
from nimbus.tools.write import write_file

logger = logging.getLogger(__name__)


# Default system prompt for Code Agent
CODE_AGENT_SYSTEM_PROMPT = """You are a skilled Code Agent with expertise in code exploration, analysis, and modification.

## CRITICAL: Output Format Constraints (Negative Examples)

You are a HEADLESS API CONNECTOR. Your job is to:
1. Think silently (no explanations of what you will do)
2. Call tools directly through function calls (no announcements)
3. Report results concisely after completion

### WRONG - Describing tool calls in text (DO NOT DO THIS):
- "I will now call the Edit tool with old_string='def foo():' and new_string='def bar():'"
- "[Called Edit with {...}]"
- "Let me use Glob to search for files..."
- "I'll invoke the Read function to examine..."
- "Using the Grep tool, I'll search for..."

### RIGHT - Actually invoke tools through function calls:
Just call the tool directly. No announcements needed.

NEVER describe what tool you're going to call. JUST CALL IT.
If you need to read a file, CALL Read. If you need to edit, CALL Edit.
Do NOT write about calling tools - ACTUALLY call them as function calls.

## Capabilities
You have access to the following tools:
- Read: Read file contents (displayed with line numbers for reference)
- Glob: Find files matching glob patterns (e.g., **/*.py)
- Grep: Search file contents with regex patterns
- Bash: Execute shell commands
- Write: Create or overwrite files
- Edit: Make precise edits to existing files (supports batch edits)

## IMPORTANT: Read output format
When you use Read, the output shows line numbers like "    1->content" for reference only.
The actual file content is ONLY the part AFTER the "->" symbol.
- Display format: "    5->    def foo():"
- Actual file content: "    def foo():"
When using Edit, NEVER include the line number prefix (e.g., "    5->") - only use the actual content!

## Edit Tool Rules (Search-and-Replace)
When using Edit, follow these rules STRICTLY:
1. The 'old_string' (search block) MUST contain 3-5 lines of context to ensure uniqueness
2. NEVER use '...' or comments to abbreviate - output COMPLETE code blocks
3. Copy the EXACT text from Read output (without line number prefixes like '   14->')
4. If your edit fails, re-read the file and try with more context
5. For multiple changes, you can use the 'edits' array parameter for batch edits

Example of GOOD edit:
```
old_string: "    def remove_item(self, name):
        self.items = [i for i in self.items if i[\"name\"] != name]"
new_string: "    def remove_item(self, name):
        self.items = [i for i in self.items if i[\"name\"] != name]

    def get_total(self):
        return sum(item[\"price\"] * item[\"quantity\"] for item in self.items)"
```

Example of BAD edit (will fail):
- old_string: "self.items = ..."  # Too short, not unique
- old_string: "   14->    def foo():"  # Contains line number prefix

## Guidelines
1. **Always explore first**: Before making changes, understand the codebase structure
2. **Read before edit**: Always read a file before modifying it
3. **Use appropriate tools**:
   - Use Glob to find files by pattern
   - Use Grep to search content across files
   - Use Read to understand file contents
   - Use Bash for commands like git, tests, builds
4. **Verify changes**: After modifications, verify by reading the changed files
5. **Be precise**: When editing, provide exact content matches (without line number prefixes!)

## Output Format
When completing a task, provide:
1. What you did
2. Files changed (if any)
3. Key findings or results

Always complete tasks thoroughly and verify your work.
"""


class CodeAgent:
    """Code Agent application built on Agent OS.

    This agent can:
    - Search code (Grep, Glob)
    - Read and understand code (Read)
    - Modify code (Edit, Write)
    - Execute commands (Bash)

    It uses the Agent OS kernel for process management and the
    vCPU for LLM-driven execution loops.

    Example:
        >>> agent = CodeAgent(workspace="/path/to/project")
        >>> result = await agent.run(
        ...     goal="Find all TODO comments in Python files",
        ...     allowed_tools={"Read", "Glob", "Grep"}
        ... )
        >>> print(result["output"])
    """

    # Available tools by category
    READONLY_TOOLS = {"Read", "Glob", "Grep"}
    EXECUTE_TOOLS = {"Bash"}
    WRITE_TOOLS = {"Write", "Edit"}
    ALL_TOOLS = READONLY_TOOLS | EXECUTE_TOOLS | WRITE_TOOLS

    def __init__(
        self,
        workspace: str = ".",
        llm_provider: str = "gemini",
        system_prompt: Optional[str] = None,
        max_iterations: int = 50,
        **llm_kwargs: Any,
    ):
        """Initialize Code Agent.

        Args:
            workspace: Workspace directory (absolute or relative path)
            llm_provider: LLM provider to use ("gemini", "openrouter", "ollama")
            system_prompt: Custom system prompt (uses default if not provided)
            max_iterations: Maximum iterations per task
            **llm_kwargs: Additional kwargs for LLM client (model, api_key, etc.)
        """
        self.workspace = Path(workspace).resolve()
        self.system_prompt = system_prompt or CODE_AGENT_SYSTEM_PROMPT
        self.max_iterations = max_iterations

        # Create LLM client
        # All LLM clients now implement the standard LLMClient interface directly,
        # including complete_with_tools() with OpenAI-style messages
        self.llm = create_llm_client(provider=llm_provider, **llm_kwargs)

        # Create tool registry
        self.tools = ToolRegistry()
        self._register_tools()

        # Create Agent OS kernel
        self.kernel = AgentOS(
            llm_client=self.llm,
            tool_registry=self.tools,
            max_iterations=max_iterations,
            workspace=self.workspace,
        )

        logger.info(
            f"CodeAgent initialized: workspace={self.workspace}, "
            f"provider={llm_provider}, tools={self.tools.list_tools()}"
        )

    def _register_tools(self) -> None:
        """Register all code agent tools."""
        # File reading
        self.tools.register_decorated(read_file)

        # File searching
        self.tools.register_decorated(glob_files)
        self.tools.register_decorated(grep_content)

        # File writing
        self.tools.register_decorated(write_file)
        self.tools.register_decorated(edit_file)

        # Command execution
        self.tools.register_decorated(bash_command)

        logger.debug(f"Registered tools: {self.tools.list_tools()}")

    async def run(
        self,
        goal: str,
        allowed_tools: Optional[Set[str]] = None,
        timeout: float = 300.0,
        max_turns: int = 50,
    ) -> Dict[str, Any]:
        """Run the Code Agent on a task.

        Args:
            goal: The task goal/instruction
            allowed_tools: Tools to allow (None = all registered tools)
            timeout: Execution timeout in seconds
            max_turns: Maximum conversation turns

        Returns:
            Result dict with:
            - pid: Process ID
            - status: "success" or "failed"
            - exit_code: 0 for success, non-zero for failure
            - output: Task result text
            - error: Error message if failed
            - token_usage: Approximate token usage
            - turns: Number of conversation turns

        Example:
            >>> result = await agent.run(
            ...     goal="Search for 'def main' in Python files",
            ...     allowed_tools={"Grep", "Glob", "Read"}
            ... )
            >>> print(result["output"])
        """
        if allowed_tools is None:
            # Default to readonly tools for safety
            allowed_tools = self.READONLY_TOOLS.copy()

        # Validate allowed tools
        unknown = allowed_tools - self.ALL_TOOLS
        if unknown:
            logger.warning(f"Unknown tools requested: {unknown}")
            allowed_tools = allowed_tools - unknown

        # Inject workspace into goal context
        goal_with_context = (
            f"Workspace: {self.workspace}\n\n"
            f"Task: {goal}"
        )

        logger.info(f"Running CodeAgent: goal={goal[:100]}..., tools={allowed_tools}")

        try:
            # Spawn agent process
            pid = await self.kernel.spawn(
                role="CodeAgent",
                goal=goal_with_context,
                allowed_tools=allowed_tools,
                max_token_budget=500000,  # Gemini 2.0 Flash supports 1M tokens
                max_turns=max_turns,
                system_prompt=self.system_prompt,
            )

            # Wait for completion
            result = await self.kernel.wait(pid, timeout=timeout)

            # Extract output
            task_result = result.get("result", {})
            output = task_result.get("text", "") if isinstance(task_result, dict) else str(task_result)

            return {
                "pid": pid,
                "status": "success" if result["exit_code"] == 0 else "failed",
                "exit_code": result["exit_code"],
                "output": output,
                "error": result.get("error"),
                "token_usage": result.get("token_usage", 0),
                "turns": result.get("turns", 0),
            }

        except Exception as e:
            logger.error(f"CodeAgent failed: {e}")
            return {
                "pid": None,
                "status": "failed",
                "exit_code": 1,
                "output": "",
                "error": str(e),
                "token_usage": 0,
                "turns": 0,
            }

    async def search_code(
        self,
        pattern: str,
        file_type: Optional[str] = None,
        path: str = ".",
    ) -> Dict[str, Any]:
        """Convenience method for code search tasks.

        Args:
            pattern: Regex pattern to search for
            file_type: File type filter (py, js, ts, etc.)
            path: Directory to search in

        Returns:
            Result dict with search results
        """
        goal = f"Search for '{pattern}' in files"
        if file_type:
            goal += f" of type {file_type}"
        if path != ".":
            goal += f" under {path}"
        goal += ". Show matching files and line numbers."

        return await self.run(
            goal=goal,
            allowed_tools={"Grep", "Glob", "Read"},
        )

    async def read_file(self, file_path: str) -> Dict[str, Any]:
        """Convenience method for reading a file.

        Args:
            file_path: Path to file to read

        Returns:
            Result dict with file contents
        """
        return await self.run(
            goal=f"Read the file {file_path} and show its contents",
            allowed_tools={"Read"},
        )

    async def analyze_codebase(self) -> Dict[str, Any]:
        """Convenience method for codebase analysis.

        Returns:
            Result dict with analysis
        """
        return await self.run(
            goal=(
                "Analyze this codebase. Find:\n"
                "1. Main programming languages used\n"
                "2. Directory structure\n"
                "3. Key entry points\n"
                "4. Test coverage (if any)\n"
                "Provide a brief summary."
            ),
            allowed_tools={"Glob", "Grep", "Read", "Bash"},
        )

    async def close(self) -> None:
        """Close the agent and release resources."""
        if hasattr(self.llm, 'close'):
            await self.llm.close()

    async def __aenter__(self) -> "CodeAgent":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()
