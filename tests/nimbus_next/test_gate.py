"""Tests for nimbus_next.gate — the syscall layer."""

import asyncio

import pytest

from nimbus_next.gate import (
    DoomLoopDetector,
    KernelGate,
    _normalize_args,
    _truncate_output,
)
from nimbus_next.protocol import ActionIR, Event


# =============================================================================
# Doom Loop Detector
# =============================================================================


class TestDoomLoopDetector:
    def test_no_loop_different_args(self):
        d = DoomLoopDetector(threshold=3)
        assert d.check("Read", {"file_path": "a.py"}) is None
        assert d.check("Read", {"file_path": "b.py"}) is None
        assert d.check("Read", {"file_path": "c.py"}) is None

    def test_detects_loop(self):
        d = DoomLoopDetector(threshold=3)
        assert d.check("Edit", {"file_path": "x", "old_text": "a"}) is None
        assert d.check("Edit", {"file_path": "x", "old_text": "a"}) is None
        result = d.check("Edit", {"file_path": "x", "old_text": "a"})
        assert result is not None
        assert "Read the file" in result

    def test_trip_count(self):
        d = DoomLoopDetector(threshold=2)
        d.check("Bash", {"command": "fail"})
        d.check("Bash", {"command": "fail"})
        assert d.trip_count == 1

    def test_reset_after_detection(self):
        d = DoomLoopDetector(threshold=2)
        d.check("Read", {"file_path": "x"})
        d.check("Read", {"file_path": "x"})  # triggers
        # After detection, history is cleared
        assert d.check("Read", {"file_path": "x"}) is None


# =============================================================================
# Arg Normalization
# =============================================================================


class TestArgNormalization:
    def test_read_path_alias(self):
        result = _normalize_args("Read", {"path": "/tmp/x"})
        assert result == {"file_path": "/tmp/x"}

    def test_edit_aliases(self):
        result = _normalize_args("Edit", {"file": "x", "old": "a", "new": "b"})
        assert result == {"file_path": "x", "old_text": "a", "new_text": "b"}

    def test_bash_cmd_alias(self):
        result = _normalize_args("Bash", {"cmd": "ls"})
        assert result == {"command": "ls"}

    def test_no_overwrite_canonical(self):
        """If canonical name already present, don't overwrite."""
        result = _normalize_args("Read", {"path": "wrong", "file_path": "right"})
        assert result["file_path"] == "right"

    def test_unknown_tool_passes_through(self):
        result = _normalize_args("CustomTool", {"x": 1})
        assert result == {"x": 1}

    def test_grep_alias(self):
        result = _normalize_args("Grep", {"query": "def.*", "dir": "/src"})
        assert result == {"pattern": "def.*", "path": "/src"}


# =============================================================================
# Output Truncation
# =============================================================================


class TestTruncateOutput:
    def test_small_output_unchanged(self):
        assert _truncate_output("hello") == "hello"

    def test_non_string_unchanged(self):
        assert _truncate_output(42) == 42
        assert _truncate_output(None) is None

    def test_large_output_truncated(self):
        large = "x" * 300_000
        result = _truncate_output(large)
        assert len(result) < 300_000
        assert "Truncated" in result


# =============================================================================
# KernelGate Integration
# =============================================================================


class TestKernelGate:
    @pytest.mark.asyncio
    async def test_successful_execution(self):
        async def executor(name, args):
            return f"executed {name}"

        gate = KernelGate("p1", executor)
        action = ActionIR(kind="TOOL_CALL", name="Bash", args={"command": "ls"})
        result = await gate.syscall_tool(action)
        assert result.status == "OK"
        assert "executed Bash" in result.output

    @pytest.mark.asyncio
    async def test_timeout(self):
        async def slow_executor(name, args):
            await asyncio.sleep(10)

        gate = KernelGate("p1", slow_executor, default_timeout=0.1)
        action = ActionIR(kind="TOOL_CALL", name="Bash", args={"command": "sleep"})
        result = await gate.syscall_tool(action)
        assert result.status == "TIMEOUT"
        assert result.fault.code == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_execution_error(self):
        async def failing_executor(name, args):
            raise RuntimeError("boom")

        gate = KernelGate("p1", failing_executor)
        action = ActionIR(kind="TOOL_CALL", name="Read", args={"file_path": "x"})
        result = await gate.syscall_tool(action)
        assert result.status == "ERROR"
        assert "boom" in result.output

    @pytest.mark.asyncio
    async def test_arg_normalization_in_gate(self):
        received_args = {}

        async def capturing_executor(name, args):
            received_args.update(args)
            return "ok"

        gate = KernelGate("p1", capturing_executor)
        action = ActionIR(kind="TOOL_CALL", name="Read", args={"path": "/tmp/x"})
        await gate.syscall_tool(action)
        assert "file_path" in received_args

    @pytest.mark.asyncio
    async def test_event_emission(self):
        events = []

        async def executor(name, args):
            return "ok"

        gate = KernelGate("p1", executor, event_callback=lambda e: events.append(e))
        action = ActionIR(kind="TOOL_CALL", name="Bash", args={"command": "echo"})
        await gate.syscall_tool(action)
        assert len(events) == 2
        assert events[0].type == "TOOL_STARTED"
        assert events[1].type == "TOOL_FINISHED"

    @pytest.mark.asyncio
    async def test_doom_loop_fatal(self):
        call_count = 0

        async def executor(name, args):
            nonlocal call_count
            call_count += 1
            return "ok"

        gate = KernelGate("p1", executor)
        action = ActionIR(kind="TOOL_CALL", name="Edit", args={"file_path": "x", "old_text": "a", "new_text": "b"})

        # First 3 calls trigger first doom loop (warning only, still executes)
        for _ in range(3):
            await gate.syscall_tool(action)

        # Next 3 calls trigger second doom loop (fatal)
        for _ in range(2):
            await gate.syscall_tool(action)
        result = await gate.syscall_tool(action)
        assert result.status == "ERROR"
        assert "Doom loop" in result.output

    @pytest.mark.asyncio
    async def test_timing_recorded(self):
        async def executor(name, args):
            return "ok"

        gate = KernelGate("p1", executor)
        action = ActionIR(kind="TOOL_CALL", name="Bash", args={"command": "echo"})
        result = await gate.syscall_tool(action)
        assert "exec" in result.timing_ms

    @pytest.mark.asyncio
    async def test_split_tool_result(self):
        """Gate should handle pi-style split results {output, ui_detail}."""
        async def executor(name, args):
            return {
                "output": "executed ok",
                "ui_detail": {"exit_code": 0, "lines": 5},
            }

        gate = KernelGate("p1", executor)
        action = ActionIR(kind="TOOL_CALL", name="Bash", args={"command": "echo"})
        result = await gate.syscall_tool(action)
        assert result.status == "OK"
        assert result.output == "executed ok"
        assert result.ui_detail == {"exit_code": 0, "lines": 5}

    @pytest.mark.asyncio
    async def test_plain_result_no_ui_detail(self):
        """Plain string results should have ui_detail=None."""
        async def executor(name, args):
            return "plain string"

        gate = KernelGate("p1", executor)
        action = ActionIR(kind="TOOL_CALL", name="Read", args={"file_path": "x"})
        result = await gate.syscall_tool(action)
        assert result.output == "plain string"
        assert result.ui_detail is None

    @pytest.mark.asyncio
    async def test_streaming_callback_injection(self):
        """Gate should inject on_update into Bash args when on_tool_output is set."""
        chunks: list[tuple[str, str]] = []

        async def executor(name, args):
            # on_update should be injected by gate
            if "on_update" in args:
                args["on_update"]("hello chunk")
            return {"output": "done", "ui_detail": {}}

        gate = KernelGate(
            "p1", executor,
            on_tool_output=lambda tool, chunk: chunks.append((tool, chunk)),
        )
        action = ActionIR(kind="TOOL_CALL", name="Bash", args={"command": "echo"})
        await gate.syscall_tool(action)
        assert len(chunks) == 1
        assert chunks[0] == ("Bash", "hello chunk")
