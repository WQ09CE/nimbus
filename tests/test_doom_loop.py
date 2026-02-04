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
            result = detector.check("Glob", {"pattern": "*.py"})
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

        glob_guidance = detector.get_guidance("Glob")
        assert "broader pattern" in glob_guidance

    def test_guidance_for_unknown_tool(self):
        """Unknown tools should get generic guidance."""
        detector = DoomLoopDetector()

        guidance = detector.get_guidance("CustomTool")
        assert "GENERAL GUIDANCE" in guidance
        assert "CustomTool" in guidance

    def test_different_args_no_loop(self):
        """Same tool with different args should not trigger loop."""
        detector = DoomLoopDetector(threshold=3)

        r1 = detector.check("Glob", {"pattern": "*.py"})
        r2 = detector.check("Glob", {"pattern": "*.ts"})
        r3 = detector.check("Glob", {"pattern": "*.js"})

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


class TestVCPUDoomLoopIntegration:
    """Test VCPU integration with DoomLoopDetector.
    
    These tests verify that VCPU correctly:
    1. Delegates to DoomLoopDetector for detection
    2. Reads loop_count through the property (READ-ONLY!)
    3. Handles doom loop faults gracefully
    
    The key bug caught here: _doom_loop_count was a read-only property
    but code tried to do `self._doom_loop_count += 1` which would fail.
    """

    @pytest.fixture
    def vcpu_components(self):
        """Create VCPU with all required components."""
        from nimbus.core.memory.mmu import MMU
        from nimbus.core.runtime.decoder import InstructionDecoder
        from nimbus.core.runtime.vcpu import VCPU
        from nimbus.os.gate import KernelGate, SimpleEventStream

        class MockToolExecutor:
            async def execute(self, tool_name: str, args: dict) -> str:
                return f"Executed {tool_name}"

        mock_llm = AsyncMock()
        mmu = MMU()
        decoder = InstructionDecoder()
        event_stream = SimpleEventStream()
        tool_executor = MockToolExecutor()
        gate = KernelGate(
            pid="test-proc",
            tool_executor=tool_executor,
            event_stream=event_stream,
        )

        vcpu = VCPU(alu=mock_llm, mmu=mmu, decoder=decoder, gate=gate)
        return vcpu

    def test_doom_loop_count_is_readonly_property(self, vcpu_components):
        """_doom_loop_count should be a read-only property delegating to DoomLoopDetector.
        
        This test catches the bug where we tried to do `self._doom_loop_count += 1`
        on a read-only property.
        """
        vcpu = vcpu_components

        # Should read from detector (initial value is 0)
        assert vcpu._doom_loop_count == 0

        # Manually increment detector's internal count
        vcpu._doom_detector._loop_count = 5
        assert vcpu._doom_loop_count == 5

        # Should NOT have a setter - attempting to set should raise AttributeError
        # This is the exact bug we fixed: code was doing `self._doom_loop_count += 1`
        with pytest.raises(AttributeError):
            vcpu._doom_loop_count = 10

    def test_doom_detector_tracks_loop_count_automatically(self, vcpu_components):
        """DoomLoopDetector should automatically increment loop_count on detection.
        
        The VCPU should NOT manually increment the count - the detector handles it.
        """
        vcpu = vcpu_components

        # Initial count is 0
        assert vcpu._doom_loop_count == 0

        # Trigger a doom loop through the detector (threshold=3 by default)
        vcpu._doom_detector.check("Read", {"path": "a.py"})
        vcpu._doom_detector.check("Read", {"path": "a.py"})
        result = vcpu._doom_detector.check("Read", {"path": "a.py"})

        # Loop should be detected and count automatically incremented by detector
        assert result.is_loop
        assert vcpu._doom_loop_count == 1

        # Trigger another doom loop with different tool
        vcpu._doom_detector.check("Write", {"path": "b.py"})
        vcpu._doom_detector.check("Write", {"path": "b.py"})
        result2 = vcpu._doom_detector.check("Write", {"path": "b.py"})

        assert result2.is_loop
        assert vcpu._doom_loop_count == 2

    def test_doom_detector_reset_clears_count(self, vcpu_components):
        """reset() should clear the loop count."""
        vcpu = vcpu_components

        # Trigger a doom loop
        for _ in range(3):
            vcpu._doom_detector.check("Read", {"path": "a.py"})

        assert vcpu._doom_loop_count == 1

        # Reset via detector
        vcpu._doom_detector.reset()
        assert vcpu._doom_loop_count == 0

    def test_doom_loop_count_property_delegates_correctly(self, vcpu_components):
        """Verify that _doom_loop_count property correctly delegates to _doom_detector."""
        vcpu = vcpu_components

        # Both should refer to the same value
        assert vcpu._doom_loop_count == vcpu._doom_detector.loop_count

        # Modify through detector
        vcpu._doom_detector._loop_count = 42

        # Property should reflect the change
        assert vcpu._doom_loop_count == 42
        assert vcpu._doom_loop_count == vcpu._doom_detector.loop_count
