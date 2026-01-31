"""
Nimbus Agent OS Kernel - End-to-End Demo

This demonstrates the complete Agent OS stack:
- Layer 0: LLM (ALU) + Tools (ISA)
- Layer 1: vCPU + ProcessManager + AgentOS
- Layer 2: Application (this demo)
"""

import asyncio
from typing import Any, Dict, List

# Layer 0 - Infrastructure (Mock for demo)
from nimbus.llm.base import CompletionResponse, LLMClient, ToolCall
from nimbus.tools.base import ToolRegistry

# Layer 1 - Agent OS
from nimbus.kernel import AgentOS, AgentProcess, ProcessState


# ============================================================================
# Layer 0: Mock Infrastructure (for demonstration)
# ============================================================================

class MockLLMClient:
    """Mock LLM Client for demonstration.

    In production, use:
        from nimbus.llm.factory import create_llm_client
        llm = create_llm_client(provider="gemini")
    """

    def __init__(self):
        self.call_count = 0

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> CompletionResponse:
        """Mock LLM completion."""
        self.call_count += 1

        # Simulate Think-Act-Observe loop
        if self.call_count == 1:
            # First call: Use Read tool
            return CompletionResponse(
                content="I'll read the file first.",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="Read",
                        arguments={"file_path": "/tmp/test.txt"}
                    )
                ]
            )
        elif self.call_count == 2:
            # Second call: After tool result, provide final answer
            return CompletionResponse(
                content="Task completed! I read the file successfully.",
                tool_calls=[]
            )
        else:
            return CompletionResponse(
                content="Done.",
                tool_calls=[]
            )


class MockToolRegistry:
    """Mock Tool Registry for demonstration.

    In production, use:
        from nimbus.tools import ToolRegistry
        tools = ToolRegistry()
        tools.register_decorated(read_file)
        tools.register_decorated(glob_files)
    """

    def get_definition(self, tool_name: str) -> Dict[str, Any]:
        """Get tool definition."""
        return {
            "name": tool_name,
            "description": f"{tool_name} tool",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }

    def get_schemas(self, tool_names: List[str]) -> List[Dict[str, Any]]:
        """Return tool schemas."""
        return [
            {
                "name": "Read",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string"}
                    }
                }
            }
        ]

    async def execute(self, name: str, args: Dict[str, Any]) -> str:
        """Execute tool."""
        if name == "Read":
            return f"File content from {args['file_path']}: Hello World!"
        return f"Tool {name} executed with {args}"


# ============================================================================
# Layer 2: Application Demo
# ============================================================================

async def demo_basic_execution():
    """Demo 1: Basic process execution."""
    print("\n" + "="*70)
    print("DEMO 1: Basic Process Execution")
    print("="*70)

    # Create Agent OS with mock components
    kernel = AgentOS(
        llm_client=MockLLMClient(),
        tool_registry=MockToolRegistry()
    )

    print("\n1. Spawning process...")
    pid = await kernel.spawn(
        role="assistant",
        goal="Read the file /tmp/test.txt and tell me its content",
        allowed_tools={"Read"}
    )
    print(f"   ✓ Process created: {pid}")

    print("\n2. Executing process (Think-Act-Observe loop)...")
    print("   - Iteration 1: LLM decides to use Read tool")
    print("   - Tool execution: Read('/tmp/test.txt')")
    print("   - Iteration 2: LLM provides final answer")

    result = await kernel.wait(pid, timeout=10.0)

    print("\n3. Process completed!")
    print(f"   Exit Code: {result['exit_code']}")
    print(f"   Result: {result['result']}")

    # Show process info
    print("\n4. Process Information:")
    processes = kernel.ps()
    for proc in processes:
        if proc['pid'] == pid:
            print(f"   PID: {proc['pid']}")
            print(f"   Role: {proc['role']}")
            print(f"   State: {proc['state']}")
            print(f"   Token Usage: {proc['token_usage']}")


async def demo_process_tree():
    """Demo 2: Process hierarchy (fork)."""
    print("\n" + "="*70)
    print("DEMO 2: Process Tree and Fork")
    print("="*70)

    kernel = AgentOS(
        llm_client=MockLLMClient(),
        tool_registry=MockToolRegistry()
    )

    print("\n1. Spawning parent process...")
    parent_pid = await kernel.spawn(
        role="coordinator",
        goal="Coordinate tasks",
        allowed_tools={"Read"}
    )
    print(f"   ✓ Parent created: {parent_pid}")

    print("\n2. Forking child processes...")
    child1_pid = kernel.process_manager.fork(
        parent_pid=parent_pid,
        role="worker",
        task="Task 1",
        allowed_tools={"Read"}
    )
    print(f"   ✓ Child 1 created: {child1_pid}")

    child2_pid = kernel.process_manager.fork(
        parent_pid=parent_pid,
        role="worker",
        task="Task 2",
        allowed_tools={"Read"}
    )
    print(f"   ✓ Child 2 created: {child2_pid}")

    print("\n3. Process Tree:")
    print(kernel.process_manager.tree())


async def demo_resource_limits():
    """Demo 3: Resource limits (Token budget, Turn limit)."""
    print("\n" + "="*70)
    print("DEMO 3: Resource Limits")
    print("="*70)

    kernel = AgentOS(
        llm_client=MockLLMClient(),
        tool_registry=MockToolRegistry()
    )

    print("\n1. Creating process with tight resource limits...")
    pid = kernel.process_manager.fork(
        parent_pid=kernel.process_manager.getpid(),
        role="test",
        task="Test task",
        allowed_tools=set(),
        max_token_budget=100  # Very small budget
    )
    print(f"   ✓ Process created: {pid}")
    print(f"   ✓ Token budget: 100 tokens")

    # Get process
    proc = kernel.process_manager._process_table[pid]
    print(f"   ✓ Max turns: {proc.max_turns}")
    print(f"   ✓ Current usage: {proc.token_usage} tokens, {proc.current_turn} turns")


async def demo_permission_check():
    """Demo 4: Permission checks."""
    print("\n" + "="*70)
    print("DEMO 4: Permission Checks")
    print("="*70)

    kernel = AgentOS(
        llm_client=MockLLMClient(),
        tool_registry=MockToolRegistry()
    )

    print("\n1. Creating process with limited tools...")
    pid = await kernel.spawn(
        role="reader",
        goal="Read a file",
        allowed_tools={"Read"}  # Only Read allowed
    )
    print(f"   ✓ Process created: {pid}")
    print(f"   ✓ Allowed tools: {{'Read'}}")

    # Get process
    proc = kernel.process_manager._process_table[pid]
    print(f"\n2. Permission checks:")
    print(f"   ✓ Can use 'Read': {'Read' in proc.allowed_tools}")
    print(f"   ✗ Can use 'Write': {'Write' in proc.allowed_tools}")
    print(f"   ✗ Can use 'Bash': {'Bash' in proc.allowed_tools}")


async def main():
    """Run all demos."""
    print("\n" + "="*70)
    print(" Nimbus Agent OS - Kernel Demo")
    print("="*70)
    print("\nThis demo shows the Agent OS stack in action:")
    print("  Layer 0: LLM (ALU) + Tools (ISA)")
    print("  Layer 1: vCPU + ProcessManager + AgentOS Kernel")
    print("  Layer 2: Application (this demo)")

    await demo_basic_execution()
    await demo_process_tree()
    await demo_resource_limits()
    await demo_permission_check()

    print("\n" + "="*70)
    print(" Demo Complete!")
    print("="*70)
    print("\nNext steps:")
    print("  1. Replace MockLLMClient with real LLM (Gemini, Claude, etc.)")
    print("  2. Register real tools (Read, Write, Bash, etc.)")
    print("  3. Build your application on top of AgentOS!")
    print()


if __name__ == "__main__":
    asyncio.run(main())
