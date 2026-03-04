"""
Tests for DoomLoopDetector

验证 doom loop 检测逻辑的正确性。
"""

from unittest.mock import AsyncMock

import pytest

from nimbus.core.runtime.doom_loop import DoomLoopDetector, DoomLoopResult


class TestDoomLoopDetector:
    """Test DoomLoopDetector functionality."""

    def test_no_loop_different_calls(self):
        """Different tool calls should not trigger doom loop."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Read", {"path": "a.py"})
        r2 = detector.check("Read", {"path": "b.py"})
        r3 = detector.check("Write", {"path": "c.py"})

        assert not r1.is_loop
        assert not r2.is_loop
        assert not r3.is_loop

    def test_loop_detected_on_threshold(self):
        """Same call repeated threshold times should trigger doom loop."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Read", {"path": "foo.py"})
        r2 = detector.check("Read", {"path": "foo.py"})
        r3 = detector.check("Read", {"path": "foo.py"})

        assert not r1.is_loop
        assert not r2.is_loop
        assert r3.is_loop
        assert r3.consecutive_count == 3
        assert r3.tool_name == "Read"

    def test_loop_not_triggered_before_threshold(self):
        """Doom loop should not trigger before reaching threshold."""
        detector = DoomLoopDetector(threshold=5)

        for i in range(4):
            result = detector.check("Bash", {"command": "find . -name '*.py'"})
            assert not result.is_loop

    def test_reset_after_detection(self):
        """After detection, new sequence should start fresh."""
        detector = DoomLoopDetector(threshold=3)

        # Trigger doom loop
        detector.check("Read", {"path": "a.py"})
        detector.check("Read", {"path": "a.py"})
        r = detector.check("Read", {"path": "a.py"})
        assert r.is_loop

        # After detection, should be reset
        r2 = detector.check("Read", {"path": "a.py"})
        assert not r2.is_loop

    def test_reset_method(self):
        """reset() should clear all state."""
        detector = DoomLoopDetector(threshold=3)

        detector.check("Read", {"path": "a.py"})
        detector.check("Read", {"path": "a.py"})

        detector.reset()

        # Should start fresh after reset
        assert len(detector.recent_calls) == 0
        assert detector.loop_count == 0

    def test_loop_count_increments(self):
        """loop_count should increment on each detection."""
        detector = DoomLoopDetector(threshold=2)

        # First doom loop
        detector.check("A", {})
        detector.check("A", {})
        assert detector.loop_count == 1

        # Second doom loop
        detector.check("B", {})
        detector.check("B", {})
        assert detector.loop_count == 2

    def test_guidance_for_known_tools(self):
        """Known tools should have specific guidance."""
        detector = DoomLoopDetector()

        edit_guidance = detector.get_guidance("Edit")
        assert "Read tool FIRST" in edit_guidance

        bash_guidance = detector.get_guidance("Bash")
        assert "command syntax" in bash_guidance

    def test_guidance_for_unknown_tool(self):
        """Unknown tools should get generic guidance."""
        detector = DoomLoopDetector()

        guidance = detector.get_guidance("CustomTool")
        assert "GENERAL GUIDANCE" in guidance
        assert "CustomTool" in guidance

    def test_different_args_no_loop(self):
        """Same tool with different args should not trigger loop."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Bash", {"command": "find . -name '*.py'"})
        r2 = detector.check("Bash", {"command": "find . -name '*.ts'"})
        r3 = detector.check("Bash", {"command": "find . -name '*.js'"})


        assert not r1.is_loop
        assert not r2.is_loop
        assert not r3.is_loop

    def test_read_same_file_different_offset_no_loop(self):
        """Read with same file_path but different offset should NOT trigger doom loop (paginated reads)."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Read", {"file_path": "docs/design.md", "limit": 80})
        r2 = detector.check("Read", {"file_path": "docs/design.md", "offset": 81, "limit": 100})
        r3 = detector.check("Read", {"file_path": "docs/design.md", "offset": 181})

        assert not r1.is_loop
        assert not r2.is_loop
        assert not r3.is_loop

    def test_read_same_file_same_offset_triggers_loop(self):
        """Read with same file_path and same offset should trigger doom loop."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Read", {"file_path": "docs/design.md", "limit": 50})
        r2 = detector.check("Read", {"file_path": "docs/design.md", "limit": 100})
        r3 = detector.check("Read", {"file_path": "docs/design.md", "limit": 200})

        # All three have offset=0 (default), so they are the same read position
        assert not r1.is_loop
        assert not r2.is_loop
        assert r3.is_loop
        assert r3.tool_name == "Read"

    def test_read_different_files_no_loop(self):
        """Read with different file_path should not trigger doom loop."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Read", {"file_path": "a.py", "limit": 50})
        r2 = detector.check("Read", {"file_path": "b.py", "limit": 50})
        r3 = detector.check("Read", {"file_path": "c.py", "limit": 50})

        assert not r1.is_loop
        assert not r2.is_loop
        assert not r3.is_loop

    def test_edit_same_file_and_old_string_different_new_string_triggers_loop(self):
        """Edit with same file_path+old_string but different new_string should trigger loop."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Edit", {"file_path": "a.py", "old_string": "foo", "new_string": "bar"})
        r2 = detector.check("Edit", {"file_path": "a.py", "old_string": "foo", "new_string": "baz"})
        r3 = detector.check("Edit", {"file_path": "a.py", "old_string": "foo", "new_string": "qux"})

        assert not r1.is_loop
        assert not r2.is_loop
        assert r3.is_loop
        assert r3.tool_name == "Edit"

    def test_non_normalized_tool_still_requires_exact_match(self):
        """Tools without normalization (e.g., Bash) should still require exact args match."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Bash", {"command": "ls", "timeout": 1000})
        r2 = detector.check("Bash", {"command": "ls", "timeout": 2000})
        r3 = detector.check("Bash", {"command": "ls", "timeout": 3000})

        assert not r1.is_loop
        assert not r2.is_loop
        assert not r3.is_loop


class TestDoomLoopResult:
    """Test DoomLoopResult helpers."""

    def test_ok_factory(self):
        """ok() should create non-loop result."""
        result = DoomLoopResult.ok()
        assert not result.is_loop
        assert result.consecutive_count == 0
        assert result.tool_name is None

    def test_detected_factory(self):
        """detected() should create loop result."""
        result = DoomLoopResult.detected("Read", 3, "Do something else")
        assert result.is_loop
        assert result.consecutive_count == 3
        assert result.tool_name == "Read"
        assert result.guidance == "Do something else"


