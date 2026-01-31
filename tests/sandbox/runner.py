"""Sandbox runner for integration testing with real LLM.

This module provides the SandboxRunner class for running CodeAgent
in an isolated workspace with real LLM backends.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from nimbus.apps.code_agent import CodeAgent


class AgentResponse:
    """Compatibility wrapper for CodeAgent response.

    The new CodeAgent.run() returns a dict, but tests expect an object
    with a .text attribute. This wrapper provides backward compatibility.

    Attributes:
        text: The output text from the agent
        status: "success" or "failed"
        exit_code: 0 for success, non-zero for failure
        error: Error message if failed
        raw: The raw response dict
    """

    def __init__(self, response_dict: Dict[str, Any]):
        """Initialize from response dict.

        Args:
            response_dict: Dict returned by CodeAgent.run() with keys:
                - output: The agent's text output
                - status: "success" or "failed"
                - exit_code: 0 for success
                - error: Error message if any
        """
        self.raw = response_dict
        self.text = response_dict.get("output", "")
        self.status = response_dict.get("status", "unknown")
        self.exit_code = response_dict.get("exit_code", -1)
        self.error = response_dict.get("error")
        self.token_usage = response_dict.get("token_usage", 0)
        self.turns = response_dict.get("turns", 0)

    @property
    def success(self) -> bool:
        """Check if the response indicates success."""
        return self.status == "success" and self.exit_code == 0

    def __repr__(self) -> str:
        """String representation."""
        return f"AgentResponse(status={self.status}, text={self.text[:50]}...)"


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
        self._temp_dir: Optional[str] = None
        self._log_handler_id: Optional[int] = None

    async def __aenter__(self) -> "SandboxRunner":
        """Create workspace and initialize agent."""
        # Create temporary workspace
        self._temp_dir = tempfile.mkdtemp(prefix=self.workspace_prefix)
        self.workspace = Path(self._temp_dir)

        # Configure logging to file if enabled
        if self._enable_logging:
            self._setup_logging()

        # Create agent with new Agent OS-based CodeAgent
        # Signature: CodeAgent(workspace, llm_provider, system_prompt=None, max_iterations=50, **llm_kwargs)
        llm_kwargs: Dict[str, Any] = {}
        if self.model:
            llm_kwargs["model"] = self.model

        self.agent = CodeAgent(
            workspace=str(self.workspace),
            llm_provider=self.provider,
            max_iterations=50,
            **llm_kwargs,
        )

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup workspace and agent resources."""
        # Close agent to release LLM client connections
        if self.agent is not None:
            await self.agent.close()
            self.agent = None

        # Cleanup workspace
        if not self.keep_workspace and self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)

    def _setup_logging(self) -> None:
        """Setup logging to file for debugging.

        Logs are written to .logs/nimbus.log in the project root.
        Uses the unified nimbus logging system which intercepts standard logging.
        """
        from nimbus.core.logging import setup_logging

        # Find project root (where .logs directory should be)
        project_root = Path(__file__).parent.parent.parent
        log_dir = str(project_root / ".logs")

        # Use unified logging system with stdlib interception
        # This captures logs from both loguru and standard logging (kernel layer)
        # Disable enqueue to avoid deadlock with pytest
        setup_logging(
            level="DEBUG",
            log_dir=log_dir,
            log_file="nimbus.log",
            console=False,  # Disable console to reduce test noise
            intercept_stdlib=True,  # Intercept kernel layer logs (vcpu, scheduler)
            enqueue=False,  # Disable to avoid pytest deadlock
        )

    async def run(self, task: str, **kwargs) -> AgentResponse:
        """Run agent with task.

        Args:
            task: Task description for the agent.
            **kwargs: Additional arguments passed to agent.run()

        Returns:
            AgentResponse with .text attribute for backward compatibility.

        Raises:
            RuntimeError: If runner is not initialized (not in context).
        """
        if self.agent is None:
            raise RuntimeError("SandboxRunner must be used as async context manager")
        # New CodeAgent.run() takes goal=task and returns dict
        # Default to all tools if not specified
        if "allowed_tools" not in kwargs:
            kwargs["allowed_tools"] = {"Read", "Glob", "Grep", "Write", "Edit", "Bash"}
        # Wrap in AgentResponse for backward compatibility with tests
        result = await self.agent.run(goal=task, **kwargs)
        return AgentResponse(result)

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
