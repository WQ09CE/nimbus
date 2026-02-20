"""
Unit tests for multi-agent orchestration modules.

Covers:
1. GoalDocument (context_protocol.py)
2. Specialist AgentProfiles (profile.py)
3. Write filter enforcement (gate.py)
4. SpecialistTool subclasses (specialist_tools.py)
5. Integration: create_agent_os with orchestrator / core profiles
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# =============================================================================
# Shared Mock LLM Client
# =============================================================================


@dataclass
class MockFunction:
    name: str
    arguments: str


@dataclass
class MockToolCall:
    function: MockFunction
    id: str = ""
    type: str = "function"

    def __post_init__(self):
        if not self.id:
            import uuid
            self.id = f"call_{uuid.uuid4().hex[:8]}"


@dataclass
class MockLLMResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


class MockLLMClient:
    """Minimal mock LLM that always returns a text final answer."""

    def __init__(self):
        self.call_count = 0

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> MockLLMResponse:
        self.call_count += 1
        return MockLLMResponse(content="Task completed.")


# =============================================================================
# 1. Tests: GoalDocument
# =============================================================================


class TestGoalDocument:
    """Tests for GoalDocument in context_protocol.py."""

    def test_goal_document_basic_render(self):
        """mission only: must produce a ## Mission section."""
        from nimbus.orchestration.context_protocol import GoalDocument

        doc = GoalDocument(mission="Fix the bug in login.py")
        rendered = doc.render()

        assert "## Mission" in rendered
        assert "Fix the bug in login.py" in rendered
        # Optional sections should NOT appear
        assert "## Context" not in rendered
        assert "## Workspace" not in rendered
        assert "## Constraints" not in rendered
        assert "## Expected Output" not in rendered

    def test_goal_document_full_render(self):
        """All fields populated: every section must appear."""
        from nimbus.orchestration.context_protocol import GoalDocument

        doc = GoalDocument(
            mission="Implement feature X",
            context="Relevant snippet here",
            workspace="/home/user/project",
            constraints=["No network calls", "Python 3.10 only"],
            expected_output="A working test suite",
        )
        rendered = doc.render()

        assert "## Mission" in rendered
        assert "Implement feature X" in rendered
        assert "## Context" in rendered
        assert "Relevant snippet here" in rendered
        assert "## Workspace" in rendered
        assert "/home/user/project" in rendered
        assert "## Constraints" in rendered
        assert "- No network calls" in rendered
        assert "- Python 3.10 only" in rendered
        assert "## Expected Output" in rendered
        assert "A working test suite" in rendered

    def test_goal_document_context_truncation(self):
        """context > 16000 chars must be truncated with a marker."""
        from nimbus.orchestration.context_protocol import GoalDocument

        long_ctx = "x" * 20_000
        doc = GoalDocument(mission="Task", context=long_ctx)
        rendered = doc.render()

        assert "[Context truncated]" in rendered
        # The rendered context section must not exceed MAX_CONTEXT_CHARS
        # (with some padding for heading text)
        assert len(rendered) < 20_000

    def test_goal_document_empty_optional_fields(self):
        """Empty context/workspace/constraints must not render their sections."""
        from nimbus.orchestration.context_protocol import GoalDocument

        doc = GoalDocument(
            mission="Deploy the service",
            context="",
            workspace="",
            constraints=[],
            expected_output="",
        )
        rendered = doc.render()

        assert "## Mission" in rendered
        assert "## Context" not in rendered
        assert "## Workspace" not in rendered
        assert "## Constraints" not in rendered
        assert "## Expected Output" not in rendered

    def test_goal_document_context_exact_boundary(self):
        """context == MAX_CONTEXT_CHARS must NOT be truncated."""
        from nimbus.orchestration.context_protocol import GoalDocument

        exact_ctx = "a" * GoalDocument.MAX_CONTEXT_CHARS
        doc = GoalDocument(mission="Task", context=exact_ctx)
        rendered = doc.render()

        assert "[Context truncated]" not in rendered

    def test_goal_document_context_one_over_boundary(self):
        """context == MAX_CONTEXT_CHARS + 1 must be truncated."""
        from nimbus.orchestration.context_protocol import GoalDocument

        over_ctx = "a" * (GoalDocument.MAX_CONTEXT_CHARS + 1)
        doc = GoalDocument(mission="Task", context=over_ctx)
        rendered = doc.render()

        assert "[Context truncated]" in rendered


# =============================================================================
# 2. Tests: Specialist Profiles
# =============================================================================


class TestSpecialistProfiles:
    """Tests for specialist AgentProfile factory methods in profile.py."""

    def test_create_explorer_profile(self):
        """Explorer: read-only tools, max_iterations=40."""
        from nimbus.core.profile import AgentProfile

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            profile = AgentProfile.create_explorer()

        assert profile.role == "explorer"
        assert "Read" in profile.allowed_tools
        # NimFS read tools should be included
        assert "NimFSReadArtifact" in profile.allowed_tools
        # Write/Edit must NOT be in explorer tools
        assert "Write" not in profile.allowed_tools
        assert "Edit" not in profile.allowed_tools
        assert profile.max_iterations == 40

    def test_create_implementer_profile(self):
        """Implementer: 6 full tools + NimFS, max_iterations=50."""
        from nimbus.core.profile import AgentProfile

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            profile = AgentProfile.create_implementer()

        assert profile.role == "implementer"
        for t in ["Read", "Write", "Edit", "Bash"]:
            assert t in profile.allowed_tools
        assert "NimFSWriteArtifact" in profile.allowed_tools
        assert profile.max_iterations == 50

    def test_create_architect_profile(self):
        """Architect: write_filter=['.md'], max_iterations=30."""
        from nimbus.core.profile import AgentProfile

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            profile = AgentProfile.create_architect()

        assert profile.role == "architect"
        assert profile.write_filter == [".md"]
        assert profile.max_iterations == 30

    def test_create_tester_profile(self):
        """Tester: read+exec only tools, max_iterations=40."""
        from nimbus.core.profile import AgentProfile

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            profile = AgentProfile.create_tester()

        assert profile.role == "tester"
        assert "Read" in profile.allowed_tools
        assert "Bash" in profile.allowed_tools
        assert "Write" not in profile.allowed_tools
        assert "Edit" not in profile.allowed_tools
        assert profile.max_iterations == 40

    def test_create_orchestrator_profile(self):
        """Orchestrator: max_consecutive_thoughts=1, max_iterations=50."""
        from nimbus.core.profile import AgentProfile

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            profile = AgentProfile.create_orchestrator()

        assert profile.role == "orchestrator"
        assert profile.max_consecutive_thoughts == 2
        assert profile.max_iterations == 50
        # Orchestrator has specialist meta-tools
        assert "Explore" in profile.allowed_tools
        assert "Implement" in profile.allowed_tools
        assert "Design" in profile.allowed_tools
        assert "Test" in profile.allowed_tools

    def test_legacy_profiles_unchanged(self):
        """Legacy create_standard/create_executor still work."""
        from nimbus.core.profile import AgentProfile

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            standard = AgentProfile.create_standard()
            executor = AgentProfile.create_executor()

        assert standard.role == "standard"
        assert executor.role == "executor"

        # Executor must have full write tools
        assert "Write" in executor.allowed_tools
        assert "Edit" in executor.allowed_tools


# =============================================================================
# 3. Tests: Write Filter (gate.py)
# =============================================================================


class TestWriteFilter:
    """Tests for KernelGate write_filter enforcement."""

    def _make_action(self, tool_name: str, file_path: str) -> Any:
        """Helper to create a minimal ActionIR-like object."""
        from nimbus.core.protocol import ActionIR
        return ActionIR(kind="TOOL_CALL", name=tool_name, args={"file_path": file_path, "content": "x", "old_text": "a", "new_text": "b"})

    def _make_gate(self, write_filter: Optional[List[str]] = None):
        """Create a KernelGate with a mock executor."""
        from nimbus.os.gate import KernelGate

        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value="OK")

        return KernelGate(
            pid="test-proc",
            tool_executor=mock_executor,
            write_filter=write_filter,
        )

    @pytest.mark.asyncio
    async def test_write_filter_blocks_py_file(self):
        """write_filter=['.md'] must block Write on .py files with PERMISSION error."""
        gate = self._make_gate(write_filter=[".md"])
        action = self._make_action("Write", "src/main.py")

        result = await gate.syscall_tool(action)

        assert result.status == "ERROR"
        assert result.fault is not None
        assert result.fault.domain == "PERMISSION"
        assert result.fault.code == "WRITE_FILTER"
        assert result.fault.retryable is False
        assert "main.py" in result.output

    @pytest.mark.asyncio
    async def test_write_filter_allows_md_file(self):
        """write_filter=['.md'] must allow Write on .md files (passes to executor)."""
        gate = self._make_gate(write_filter=[".md"])
        action = self._make_action("Write", "docs/README.md")

        result = await gate.syscall_tool(action)

        assert result.status == "OK"
        assert result.fault is None

    @pytest.mark.asyncio
    async def test_write_filter_none_allows_all(self):
        """write_filter=None must allow Write on any file."""
        gate = self._make_gate(write_filter=None)
        action = self._make_action("Write", "src/secret.py")

        result = await gate.syscall_tool(action)

        assert result.status == "OK"

    @pytest.mark.asyncio
    async def test_write_filter_edit_blocked(self):
        """write_filter=['.md'] must also block Edit on non-.md files."""
        gate = self._make_gate(write_filter=[".md"])
        action = self._make_action("Edit", "app/views.js")

        result = await gate.syscall_tool(action)

        assert result.status == "ERROR"
        assert result.fault is not None
        assert result.fault.domain == "PERMISSION"
        assert result.fault.code == "WRITE_FILTER"

    @pytest.mark.asyncio
    async def test_write_filter_read_not_blocked(self):
        """write_filter=['.md'] must NOT block Read on any file."""
        from nimbus.core.protocol import ActionIR

        gate = self._make_gate(write_filter=[".md"])
        action = ActionIR(kind="TOOL_CALL", name="Read", args={"file_path": "src/main.py"})

        result = await gate.syscall_tool(action)

        # Read should pass through (executor returns "OK")
        assert result.status == "OK"

    @pytest.mark.asyncio
    async def test_write_filter_empty_list_allows_all(self):
        """write_filter=[] (empty) must allow Write on any file (same as None)."""
        gate = self._make_gate(write_filter=[])
        action = self._make_action("Write", "src/anything.py")

        result = await gate.syscall_tool(action)

        # Empty list is falsy, so the gate treats it as no filter
        assert result.status == "OK"


# =============================================================================
# 4. Tests: SpecialistTool subclasses
# =============================================================================


class TestSpecialistTools:
    """Tests for ExploreTool, ImplementTool, DesignTool, TestTool."""

    def _make_mock_agent_os(self, output: str = "Specialist result") -> MagicMock:
        """Create a mock AgentOS that tracks spawn/wait calls."""
        mock_os = MagicMock()
        mock_os.spawn = MagicMock(return_value="proc-abc123")

        from nimbus.core.protocol import ToolResult
        mock_result = ToolResult(status="OK", output=output)
        mock_os.wait = AsyncMock(return_value=mock_result)
        return mock_os

    def test_explore_tool_creates_explorer_profile(self):
        """ExploreTool._create_profile must return explorer role with read-only tools."""
        from nimbus.orchestration.specialist_tools import ExploreTool

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            mock_os = self._make_mock_agent_os()
            tool = ExploreTool(agent_os=mock_os, workspace=Path("/tmp"))
            profile = tool._create_profile()

        assert profile.role == "explorer"
        assert "Read" in profile.allowed_tools
        assert "Write" not in profile.allowed_tools

    def test_implement_tool_tracks_diff(self):
        """ImplementTool must set TRACK_DIFF=True."""
        from nimbus.orchestration.specialist_tools import ImplementTool

        assert ImplementTool.TRACK_DIFF is True

    def test_design_tool_has_write_filter(self):
        """DesignTool must create architect profile with write_filter=['.md']."""
        from nimbus.orchestration.specialist_tools import DesignTool

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            mock_os = self._make_mock_agent_os()
            tool = DesignTool(agent_os=mock_os, workspace=Path("/tmp"))
            profile = tool._create_profile()

        assert profile.role == "architect"
        assert ".md" in profile.write_filter

    def test_test_tool_creates_tester_profile(self):
        """TestTool._create_profile must return tester role."""
        from nimbus.orchestration.specialist_tools import TestTool

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            mock_os = self._make_mock_agent_os()
            tool = TestTool(agent_os=mock_os, workspace=Path("/tmp"))
            profile = tool._create_profile()

        assert profile.role == "tester"

    def test_explore_tool_does_not_track_diff(self):
        """ExploreTool must NOT track workspace diff (TRACK_DIFF=False)."""
        from nimbus.orchestration.specialist_tools import ExploreTool

        assert ExploreTool.TRACK_DIFF is False

    @pytest.mark.asyncio
    async def test_specialist_tool_builds_goal_document(self):
        """execute() must render a GoalDocument and pass it to AgentOS.spawn()."""
        from nimbus.orchestration.specialist_tools import ExploreTool

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            mock_os = self._make_mock_agent_os(output="Found 3 files.")
            tool = ExploreTool(agent_os=mock_os, workspace=Path("/workspace"))

            result = await tool.execute(task="List all Python files", context="Project root is /workspace")

        # spawn must have been called exactly once
        assert mock_os.spawn.call_count == 1
        spawn_kwargs = mock_os.spawn.call_args

        # The goal argument must contain the rendered mission
        goal_arg = spawn_kwargs[1].get("goal") or spawn_kwargs[0][0]
        assert "List all Python files" in goal_arg
        assert "## Mission" in goal_arg

    @pytest.mark.asyncio
    async def test_specialist_tool_returns_formatted_result(self):
        """execute() must return a string starting with '## <Role> Result'."""
        from nimbus.orchestration.specialist_tools import ExploreTool

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""):
            mock_os = self._make_mock_agent_os(output="Analysis done.")
            tool = ExploreTool(agent_os=mock_os, workspace=Path("/tmp"))

            result = await tool.execute(task="Explore the project")

        assert result.startswith("## Explorer Result")
        assert "Analysis done." in result

    @pytest.mark.asyncio
    async def test_implement_tool_includes_diff_in_result(self):
        """ImplementTool.execute() must call take_snapshot and include diff info."""
        from nimbus.orchestration.specialist_tools import ImplementTool
        from nimbus.orchestration.workspace_diff import WorkspaceDiff, WorkspaceSnapshot

        fake_snapshot = WorkspaceSnapshot()
        fake_diff = WorkspaceDiff(created=["src/new_file.py"])

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""), \
             patch("nimbus.orchestration.specialist_tools.take_snapshot", return_value=fake_snapshot), \
             patch("nimbus.orchestration.specialist_tools.diff_snapshots", return_value=fake_diff):

            mock_os = self._make_mock_agent_os(output="Implementation done.")
            tool = ImplementTool(agent_os=mock_os, workspace=Path("/tmp"))

            result = await tool.execute(task="Add feature Y")

        assert "## Implementer Result" in result
        # Diff info must be present because the fake diff has_changes
        assert "Files Changed" in result or "new_file.py" in result


# =============================================================================
# 5. Integration Tests: create_agent_os
# =============================================================================


class TestCreateAgentOSIntegration:
    """Integration tests for create_agent_os factory with profile parameter."""

    def test_create_agent_os_orchestrator_profile_registers_specialist_tools(self):
        """create_agent_os(profile='orchestrator') must register Explore/Implement/Design/Test."""
        from nimbus.agentos import create_agent_os

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""), \
             patch("nimbus.orchestration.prompts.AGENTOS_SYSTEM_RULES", ""):
            llm = MockLLMClient()
            os_instance = create_agent_os(llm_client=llm, profile="orchestrator")

        tools = os_instance.list_tools()
        assert "Explore" in tools
        assert "Implement" in tools
        assert "Design" in tools
        assert "Test" in tools
        # Verify tool should also be registered
        assert "Verify" in tools

    def test_create_agent_os_no_profile_unchanged(self):
        """create_agent_os() with no profile registers standard kernel tools."""
        from nimbus.agentos import create_agent_os

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""), \
             patch("nimbus.orchestration.prompts.AGENTOS_SYSTEM_RULES", ""):
            llm = MockLLMClient()
            os_instance = create_agent_os(llm_client=llm)

        tools = os_instance.list_tools()
        # Standard kernel tools must be registered
        assert "Read" in tools
        assert "Write" in tools
        assert "Bash" in tools
        # No specialist tools
        assert "Explore" not in tools
        assert "Dispatch" not in tools

    def test_create_agent_os_orchestrator_vcpu_config(self):
        """create_agent_os(profile='orchestrator') must apply orchestrator runtime config."""
        from nimbus.agentos import create_agent_os

        with patch("nimbus.orchestration.prompts.PromptManager.get_system_prompt", return_value=""), \
             patch("nimbus.orchestration.prompts.AGENTOS_SYSTEM_RULES", ""):
            llm = MockLLMClient()
            os_instance = create_agent_os(llm_client=llm, profile="orchestrator")

        # Orchestrator profile sets max_iterations=50 and max_consecutive_thoughts=2
        assert os_instance.config.vcpu_config.max_iterations == 50
        assert os_instance.config.vcpu_config.max_consecutive_thoughts == 2
