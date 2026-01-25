"""Sandbox runner for integration testing with real LLM.

This module provides the SandboxRunner class for running CodeAgent
in an isolated workspace with real LLM backends.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from nimbus.core.agent import CodeAgent
from nimbus.core.types import AgentResponse
from nimbus.llm import create_llm_client
from nimbus.tools import (
    ToolRegistry,
    read_file,
    write_file,
    edit_file,
    glob_files,
    grep_content,
    bash_command,
)
from nimbus.tools.subagent import subagent_task


class SandboxRunner:
    """Run CodeAgent in an isolated sandbox workspace.

    This class provides:
    - Isolated temporary workspace for each test
    - Real LLM integration (configurable provider)
    - Pre-configured tools
    - Cleanup after test

    Usage:
        async with SandboxRunner(provider="ollama", model="qwen3:8b") as runner:
            # Setup workspace
            runner.create_file("src/main.py", "def hello(): pass")

            # Run agent
            response = await runner.run("Add docstring to hello function")

            # Assert results
            assert "docstring" in runner.read_file("src/main.py")

    Attributes:
        provider: LLM provider name (ollama, gemini, openrouter)
        model: Model name for the provider
        workspace: Path to the temporary workspace
        agent: The CodeAgent instance
    """

    def __init__(
        self,
        provider: str = "ollama",
        model: Optional[str] = None,
        workspace_prefix: str = "nimbus_sandbox_",
        keep_workspace: bool = False,
        memory_type: str = "simple",
        tools: Optional[List[str]] = None,
        multi_agent: bool = True,
        enable_logging: bool = False,
    ):
        """Initialize SandboxRunner.

        Args:
            provider: LLM provider (ollama, gemini, openrouter). Default: ollama
            model: Model name override. If None, uses provider default.
            workspace_prefix: Prefix for temp directory name.
            keep_workspace: If True, don't delete workspace after test.
            memory_type: Memory type for agent (simple, tiered).
            tools: List of tool names to register. Default: all common tools.
            multi_agent: Enable multi-agent mode with Subagent tool. Default: True
            enable_logging: Enable logging to .logs/nimbus.log. Default: False
        """
        self.provider = provider
        self.model = model
        self.workspace_prefix = workspace_prefix
        self.keep_workspace = keep_workspace
        self.memory_type = memory_type
        self.tools = tools
        self.multi_agent = multi_agent
        # Enable logging from param or env var
        import os
        self._enable_logging = enable_logging or bool(os.getenv("NIMBUS_TEST_LOGGING"))

        self.workspace: Optional[Path] = None
        self.agent: Optional[CodeAgent] = None
        self._llm_client: Optional[Any] = None
        self._temp_dir: Optional[str] = None
        self._log_handler_id: Optional[int] = None

    async def __aenter__(self) -> "SandboxRunner":
        """Create workspace and initialize agent."""
        # Create temporary workspace
        self._temp_dir = tempfile.mkdtemp(prefix=self.workspace_prefix)
        self.workspace = Path(self._temp_dir)

        # Create LLM client using unified config
        self._llm_client = self._create_llm_client()

        # Create tool registry
        tool_registry = self._create_tool_registry()

        # Configure logging to file if enabled
        if self._enable_logging:
            self._setup_logging()

        # Create agent (uses task mode by default)
        self.agent = CodeAgent(
            llm_client=self._llm_client,
            tool_registry=tool_registry,
            workspace=self.workspace,
            memory_type=self.memory_type,
            enable_logging=self._enable_logging,
        )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup workspace."""
        if not self.keep_workspace and self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    def _setup_logging(self) -> None:
        """Setup logging to file for debugging.

        Logs are written to .logs/nimbus.log in the project root.
        """
        from loguru import logger
        import sys

        # Find project root (where .logs directory should be)
        project_root = Path(__file__).parent.parent.parent
        log_dir = project_root / ".logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "nimbus.log"

        # Remove default stderr handler to reduce noise
        logger.remove()

        # Add file handler
        self._log_handler_id = logger.add(
            log_file,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}",
            level="DEBUG",
            rotation="10 MB",
            retention="3 days",
        )

        # Add minimal stderr handler for errors only
        logger.add(sys.stderr, level="WARNING", format="{message}")

        logger.info(f"Logging initialized: {log_file}")

    def _create_llm_client(self) -> Any:
        """Create LLM client based on configuration.

        Priority:
        1. Explicit provider/model from constructor
        2. Unified config from ~/.nimbus/config.json agents.core
        3. Default provider from config.json llm.default

        Returns:
            LLM client instance.
        """
        # If explicit provider/model specified, use them directly
        if self.provider or self.model:
            kwargs: Dict[str, Any] = {}
            if self.provider:
                kwargs["provider"] = self.provider
            if self.model:
                kwargs["model"] = self.model
            return create_llm_client(**kwargs)

        # Use unified config: read core agent model from config.json
        from nimbus.core.agents_config import create_llm_client_for_agent
        return create_llm_client_for_agent("core")

    def _create_tool_registry(self) -> ToolRegistry:
        """Create and configure tool registry.

        Returns:
            Configured ToolRegistry instance.
        """
        registry = ToolRegistry()

        # Define available tools
        all_tools = {
            "Read": read_file,
            "Write": write_file,
            "Edit": edit_file,
            "Glob": glob_files,
            "Grep": grep_content,
            "Bash": bash_command,
        }

        # Register requested tools (or all if none specified)
        tools_to_register = self.tools or list(all_tools.keys())
        for tool_name in tools_to_register:
            if tool_name in all_tools:
                registry.register_decorated(all_tools[tool_name])

        # Register Subagent tool if multi_agent mode is enabled
        if self.multi_agent:
            registry.register_decorated(subagent_task)

        return registry

    async def run(self, task: str, **kwargs) -> AgentResponse:
        """Run agent with task.

        Args:
            task: Task description for the agent.
            **kwargs: Additional arguments passed to agent.run()

        Returns:
            AgentResponse from the agent.

        Raises:
            RuntimeError: If runner is not initialized (not in context).
        """
        if self.agent is None:
            raise RuntimeError("SandboxRunner must be used as async context manager")
        return await self.agent.run(task, **kwargs)

    async def run_stream(self, task: str, **kwargs):
        """Run agent with streaming output.

        Args:
            task: Task description for the agent.
            **kwargs: Additional arguments passed to agent.run_stream()

        Yields:
            Status dicts from agent execution.

        Raises:
            RuntimeError: If runner is not initialized.
        """
        if self.agent is None:
            raise RuntimeError("SandboxRunner must be used as async context manager")
        async for status in self.agent.run_stream(task, **kwargs):
            yield status

    def create_file(self, relative_path: str, content: str) -> Path:
        """Create file in workspace.

        Args:
            relative_path: Path relative to workspace root.
            content: File content.

        Returns:
            Absolute path to created file.

        Raises:
            RuntimeError: If runner is not initialized.
        """
        if self.workspace is None:
            raise RuntimeError("SandboxRunner must be used as async context manager")

        file_path = self.workspace / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        return file_path

    def create_files(self, files: Dict[str, str]) -> Dict[str, Path]:
        """Create multiple files in workspace.

        Args:
            files: Dict mapping relative paths to content.

        Returns:
            Dict mapping relative paths to absolute paths.
        """
        return {path: self.create_file(path, content) for path, content in files.items()}

    def read_file(self, relative_path: str) -> str:
        """Read file from workspace.

        Args:
            relative_path: Path relative to workspace root.

        Returns:
            File content as string.

        Raises:
            RuntimeError: If runner is not initialized.
            FileNotFoundError: If file doesn't exist.
        """
        if self.workspace is None:
            raise RuntimeError("SandboxRunner must be used as async context manager")
        return (self.workspace / relative_path).read_text()

    def file_exists(self, relative_path: str) -> bool:
        """Check if file exists in workspace.

        Args:
            relative_path: Path relative to workspace root.

        Returns:
            True if file exists, False otherwise.
        """
        if self.workspace is None:
            return False
        return (self.workspace / relative_path).exists()

    def list_files(self, pattern: str = "**/*") -> List[Path]:
        """List files matching pattern in workspace.

        Args:
            pattern: Glob pattern to match.

        Returns:
            List of matching file paths (absolute).
        """
        if self.workspace is None:
            return []
        return [p for p in self.workspace.glob(pattern) if p.is_file()]

    def get_file_tree(self, max_depth: int = 3) -> str:
        """Get a string representation of workspace file tree.

        Args:
            max_depth: Maximum directory depth to show.

        Returns:
            Formatted file tree string.
        """
        if self.workspace is None:
            return ""

        lines = [str(self.workspace)]

        def _walk(path: Path, prefix: str = "", depth: int = 0):
            if depth >= max_depth:
                return

            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
            for i, entry in enumerate(entries):
                is_last = i == len(entries) - 1
                current_prefix = prefix + ("    " if is_last else "|   ")
                connector = "`-- " if is_last else "|-- "
                lines.append(f"{prefix}{connector}{entry.name}")

                if entry.is_dir():
                    _walk(entry, current_prefix, depth + 1)

        _walk(self.workspace)
        return "\n".join(lines)

    def cleanup(self):
        """Manually clean up workspace.

        Useful if keep_workspace=True but you want to clean up explicitly.
        """
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
            self.workspace = None
