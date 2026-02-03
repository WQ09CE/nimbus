"""
E2E test for checkpoint (hibernate/wake) functionality.

Tests that session state can be:
1. Saved to persistent storage (checkpoint)
2. Restored after "closing" the session
3. Continued from where it left off
"""

import asyncio
import pytest
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.protocol import ToolResult
from nimbus.storage.sqlite import SQLiteStorage


@dataclass
class MockLLMResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


@dataclass 
class MockToolCall:
    id: str = "call_1"
    function: Any = None


@dataclass
class MockFunction:
    name: str = ""
    arguments: str = "{}"


class MockLLMClient:
    """Mock LLM that tracks calls and returns predetermined responses."""

    def __init__(self):
        self.call_count = 0
        self.messages_received: List[List[Dict[str, Any]]] = []

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        on_chunk: Optional[Any] = None,
    ) -> MockLLMResponse:
        self.messages_received.append(messages)
        self.call_count += 1

        # Return result after a few iterations
        if self.call_count >= 3:
            return MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        id=f"call_{self.call_count}",
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Task completed"}'
                        ),
                    )
                ]
            )

        # Otherwise return a tool call
        return MockLLMResponse(
            content=f"Thinking step {self.call_count}...",
            tool_calls=[
                MockToolCall(
                    id=f"call_{self.call_count}",
                    function=MockFunction(
                        name="Bash",
                        arguments=f'{{"command": "echo step {self.call_count}"}}'
                    ),
                )
            ]
        )


class MockEventStream:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class MockGate:
    """Mock Gate for testing."""

    def __init__(self):
        self.events = MockEventStream()
        self.pid = "test-pid"
        self.call_history = []

    async def syscall_tool(self, action, timeout_sec: float = 60.0) -> ToolResult:
        self.call_history.append(action.name)
        return ToolResult(status="OK", output=f"Output of {action.name}")


class TestCheckpointBasic:
    """Test basic checkpoint create/restore functionality."""

    @pytest.mark.asyncio
    async def test_vcpu_checkpoint_create_restore(self):
        """Test that vCPU can create and restore checkpoint."""
        # Setup
        llm = MockLLMClient()
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))
        decoder = InstructionDecoder()
        gate = MockGate()

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=10),
            tools=[
                {"type": "function", "function": {"name": "Bash", "parameters": {}}},
            ],
        )

        # Add some messages to memory
        mmu.add_user_message("Do something")
        mmu.add_assistant_message("I'll help you with that.")
        mmu.add_user_message("Great, continue")

        # Execute a step to change state
        await vcpu.step()

        # Record state before checkpoint
        iteration_before = vcpu._state.iteration
        messages_before = len(mmu.current_frame.messages)

        print(f"\n=== Before Checkpoint ===")
        print(f"Iteration: {iteration_before}")
        print(f"Messages: {messages_before}")
        print(f"Is running: {vcpu._state.is_running}")

        # Create checkpoint
        checkpoint = vcpu.create_checkpoint(session_id="test_session", reason="test")

        print(f"\n=== Checkpoint Created ===")
        print(f"Session ID: {checkpoint.session_id}")
        print(f"Step Index: {checkpoint.step_index}")
        print(f"Reason: {checkpoint.reason}")

        # Simulate "closing" - create new VCPU and MMU
        llm2 = MockLLMClient()
        mmu2 = MMU(config=MMUConfig(max_context_tokens=10000))
        gate2 = MockGate()

        vcpu2 = VCPU(
            alu=llm2,
            decoder=decoder,
            gate=gate2,
            mmu=mmu2,
            config=VCPUConfig(max_iterations=10),
            tools=[
                {"type": "function", "function": {"name": "Bash", "parameters": {}}},
            ],
        )

        # Verify new VCPU starts fresh
        assert vcpu2._state.iteration == 0
        assert len(mmu2.current_frame.messages) == 0

        # Restore from checkpoint
        vcpu2.restore_from_checkpoint(checkpoint)

        print(f"\n=== After Restore ===")
        print(f"Iteration: {vcpu2._state.iteration}")
        print(f"Messages: {len(mmu2.current_frame.messages)}")
        print(f"Is running: {vcpu2._state.is_running}")

        # Verify state restored
        assert vcpu2._state.iteration == iteration_before
        assert len(mmu2.current_frame.messages) == messages_before

    @pytest.mark.asyncio
    async def test_checkpoint_preserves_conversation(self):
        """Test that checkpoint preserves full conversation history."""
        llm = MockLLMClient()
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))
        decoder = InstructionDecoder()
        gate = MockGate()

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=10),
            tools=[],
        )

        # Build conversation
        messages_to_add = [
            ("user", "Hello, I need help with Python"),
            ("assistant", "Sure, I can help with Python!"),
            ("user", "How do I read a file?"),
            ("assistant", "Use open() function..."),
            ("user", "Thanks! Now how do I write?"),
        ]

        for role, content in messages_to_add:
            if role == "user":
                mmu.add_user_message(content)
            else:
                mmu.add_assistant_message(content)

        # Create checkpoint
        checkpoint = vcpu.create_checkpoint(session_id="conv_test", reason="test")

        # Create fresh instances
        mmu2 = MMU(config=MMUConfig(max_context_tokens=10000))
        vcpu2 = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu2,
            config=VCPUConfig(max_iterations=10),
            tools=[],
        )

        # Restore
        vcpu2.restore_from_checkpoint(checkpoint)

        # Verify all messages restored
        restored_messages = mmu2.current_frame.messages
        assert len(restored_messages) == len(messages_to_add)

        for i, (role, content) in enumerate(messages_to_add):
            assert restored_messages[i].role == role
            assert restored_messages[i].content == content

        print(f"\n=== Conversation Restored ===")
        for msg in restored_messages:
            print(f"  [{msg.role}]: {msg.content[:50]}...")


class TestCheckpointWithStorage:
    """Test checkpoint with SQLite storage."""

    @pytest.mark.asyncio
    async def test_checkpoint_save_load_sqlite(self, tmp_path):
        """Test saving and loading checkpoint from SQLite."""
        db_path = tmp_path / "test_checkpoint.db"
        storage = SQLiteStorage(str(db_path))
        await storage.initialize()

        # Setup VCPU
        llm = MockLLMClient()
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))
        decoder = InstructionDecoder()
        gate = MockGate()

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=10),
            tools=[],
        )

        # Add conversation
        mmu.add_user_message("Save this to database")
        mmu.add_assistant_message("I'll remember this conversation")

        # Execute to change iteration
        vcpu._state.iteration = 5
        vcpu._state.consecutive_errors = 2

        # Create session in DB first (FK constraint)
        session_id = "sqlite_test_session"
        await storage.create_session(session_id=session_id)

        # Create and save checkpoint
        checkpoint = vcpu.create_checkpoint(session_id=session_id, reason="hibernate")

        checkpoint_id = await storage.save_session_checkpoint(checkpoint)
        print(f"\n=== Checkpoint Saved ===")
        print(f"Checkpoint ID: {checkpoint_id}")

        # Load checkpoint
        loaded = await storage.load_latest_session_checkpoint(session_id)

        assert loaded is not None
        print(f"\n=== Checkpoint Loaded ===")
        print(f"Session ID: {loaded.session_id}")
        print(f"Step Index: {loaded.step_index}")
        print(f"Reason: {loaded.reason}")

        # Create new VCPU and restore
        mmu2 = MMU(config=MMUConfig(max_context_tokens=10000))
        vcpu2 = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu2,
            config=VCPUConfig(max_iterations=10),
            tools=[],
        )

        vcpu2.restore_from_checkpoint(loaded)

        # Verify
        assert vcpu2._state.iteration == 5
        assert len(mmu2.current_frame.messages) == 2

        await storage.close()


class TestCheckpointEdgeCases:
    """Test checkpoint edge cases."""

    @pytest.mark.asyncio
    async def test_checkpoint_with_tool_calls_in_progress(self):
        """Test checkpoint when there are pending tool calls."""
        llm = MockLLMClient()
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))
        decoder = InstructionDecoder()
        gate = MockGate()

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=10),
            tools=[
                {"type": "function", "function": {"name": "Bash", "parameters": {}}},
            ],
        )

        # Simulate mid-execution state
        mmu.add_user_message("Run a command")
        mmu.add_assistant_with_tool_calls(
            content="I'll run that for you",
            tool_calls=[{
                "id": "pending_call",
                "type": "function",
                "function": {"name": "Bash", "arguments": '{"command": "ls"}'}
            }]
        )
        # Note: tool result NOT added yet - simulating interrupt mid-execution

        vcpu._state.iteration = 3
        vcpu._state.is_running = True

        # Create checkpoint during execution
        checkpoint = vcpu.create_checkpoint(session_id="mid_exec", reason="interrupt")

        # Restore to new instance
        mmu2 = MMU(config=MMUConfig(max_context_tokens=10000))
        vcpu2 = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu2,
            config=VCPUConfig(max_iterations=10),
            tools=[],
        )

        vcpu2.restore_from_checkpoint(checkpoint)

        # Verify state
        assert vcpu2._state.iteration == 3
        # is_running should be reset to False on restore (ready to continue)
        assert vcpu2._state.is_running == False

        # Verify messages including pending tool call
        messages = mmu2.current_frame.messages
        assert len(messages) == 2
        assert messages[1].tool_calls is not None

        print(f"\n=== Mid-Execution Checkpoint Restored ===")
        print(f"Messages: {len(messages)}")
        print(f"Has pending tool calls: {messages[1].tool_calls is not None}")

    @pytest.mark.asyncio
    async def test_multiple_checkpoints_load_latest(self, tmp_path):
        """Test that loading gets the latest checkpoint."""
        db_path = tmp_path / "multi_checkpoint.db"
        storage = SQLiteStorage(str(db_path))
        await storage.initialize()

        llm = MockLLMClient()
        mmu = MMU(config=MMUConfig(max_context_tokens=10000))
        decoder = InstructionDecoder()
        gate = MockGate()

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=VCPUConfig(max_iterations=10),
            tools=[],
        )

        session_id = "multi_checkpoint_session"
        await storage.create_session(session_id=session_id)

        # Create first checkpoint
        mmu.add_user_message("First message")
        vcpu._state.iteration = 1
        cp1 = vcpu.create_checkpoint(session_id=session_id, reason="checkpoint_1")
        await storage.save_session_checkpoint(cp1)

        # Wait a bit and create second checkpoint
        await asyncio.sleep(0.1)
        mmu.add_user_message("Second message")
        vcpu._state.iteration = 5
        cp2 = vcpu.create_checkpoint(session_id=session_id, reason="checkpoint_2")
        await storage.save_session_checkpoint(cp2)

        # Wait and create third checkpoint
        await asyncio.sleep(0.1)
        mmu.add_user_message("Third message")
        vcpu._state.iteration = 10
        cp3 = vcpu.create_checkpoint(session_id=session_id, reason="checkpoint_3")
        await storage.save_session_checkpoint(cp3)

        # Load latest - should be checkpoint 3
        loaded = await storage.load_latest_session_checkpoint(session_id)

        assert loaded is not None
        assert loaded.step_index == 10
        assert loaded.reason == "checkpoint_3"

        print(f"\n=== Latest Checkpoint Loaded ===")
        print(f"Step Index: {loaded.step_index} (expected 10)")
        print(f"Reason: {loaded.reason}")

        await storage.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
