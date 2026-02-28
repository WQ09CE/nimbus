"""
Tests for ExecutionState

验证执行状态管理的正确性。
"""

from nimbus.core.runtime.states import FSMExecutionState as ExecutionState


class TestExecutionState:
    """Test ExecutionState functionality."""

    def test_initial_state(self):
        """Initial state should have correct defaults."""
        state = ExecutionState()

        assert state.iteration == 0
        assert state.is_running == False
        assert state.is_done == False
        assert state.final_result is None
        assert state.consecutive_thoughts == 0
        assert state.compaction_count == 0

    def test_reset(self):
        """reset() should restore all values to initial state."""
        state = ExecutionState()

        # Modify state
        state.iteration = 50
        state.is_running = True
        state.consecutive_thoughts = 5
        state.tool_failure_counts["Bash"] = 3

        # Reset
        state.reset()

        assert state.iteration == 0
        assert state.is_running == False
        assert state.consecutive_thoughts == 0
        assert len(state.tool_failure_counts) == 0

    def test_start_execution(self):
        """start_execution() should set is_running and reset state."""
        state = ExecutionState()
        state.iteration = 10

        state.start_execution()

        assert state.is_running
        assert state.iteration == 0

    def test_finish_execution(self):
        """finish_execution() should mark as done with result."""
        state = ExecutionState()
        state.is_running = True

        state.finish_execution("success")

        assert state.is_done
        assert not state.is_running
        assert state.final_result == "success"

    def test_increment_iteration(self):
        """increment_iteration() should return new value."""
        state = ExecutionState()

        assert state.increment_iteration() == 1
        assert state.increment_iteration() == 2
        assert state.increment_iteration() == 3

    def test_should_compact(self):
        """should_compact() should check iteration and compaction limits."""
        state = ExecutionState.from_config(
            max_iterations=50,
            max_compactions=10
        )

        # Not at limit
        state.iteration = 49
        assert not state.should_compact()

        # At limit
        state.iteration = 50
        assert state.should_compact()

        # At compaction limit
        state.compaction_count = 10
        assert not state.should_compact()

    def test_record_compaction(self):
        """record_compaction() should increment count and reset iteration."""
        state = ExecutionState()
        state.iteration = 50

        new_count = state.record_compaction()

        assert new_count == 1
        assert state.iteration == 0
        assert state.compaction_count == 1

    def test_on_thought(self):
        """on_thought() should track consecutive thoughts."""
        state = ExecutionState()

        assert state.on_thought() == 1
        assert state.on_thought() == 2
        assert state.on_thought() == 3

    def test_on_action(self):
        """on_action() should reset thought counter."""
        state = ExecutionState()
        state.on_thought()
        state.on_thought()

        state.on_action()

        assert state.consecutive_thoughts == 0

    def test_on_tool_success(self):
        """on_tool_success() should reset error counters."""
        state = ExecutionState()
        state.consecutive_errors = 3
        state.tool_failure_counts["Read"] = 2

        state.on_tool_success("Read")

        assert state.consecutive_errors == 0
        assert state.tool_failure_counts["Read"] == 0

    def test_on_tool_failure(self):
        """on_tool_failure() should track failures per tool."""
        state = ExecutionState()

        assert state.on_tool_failure("Bash") == 1
        assert state.on_tool_failure("Bash") == 2
        assert state.on_tool_failure("Read") == 1
        assert state.on_tool_failure("Bash") == 3

    def test_is_tool_failing_too_much(self):
        """is_tool_failing_too_much() should check against threshold."""
        state = ExecutionState.from_config(max_tool_failures=3)

        state.on_tool_failure("Bash")
        state.on_tool_failure("Bash")
        assert not state.is_tool_failing_too_much("Bash")

        state.on_tool_failure("Bash")
        assert state.is_tool_failing_too_much("Bash")

    def test_empty_response_tracking(self):
        """on_empty_response() and on_valid_response() should track correctly."""
        state = ExecutionState()

        assert state.on_empty_response() == 1
        assert state.on_empty_response() == 2

        state.on_valid_response()

        assert state.consecutive_empty_responses == 0

    def test_to_dict(self):
        """to_dict() should return serializable state."""
        state = ExecutionState()
        state.iteration = 10
        state.is_running = True

        d = state.to_dict()

        assert d["iteration"] == 10
        assert d["is_running"] == True
        assert "tool_failure_counts" in d

    def test_from_config(self):
        """from_config() should create state with custom limits."""
        state = ExecutionState.from_config(
            max_iterations=100,
            max_compactions=5,
            max_tool_failures=10
        )

        assert state.max_iterations == 100
        assert state.max_compactions == 5
        assert state.max_tool_failures == 10
