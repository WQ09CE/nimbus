"""
Integration Test for Skill Tools.

Verifies that:
1. Skills are loaded into the separate _skill_tools registry.
2. The CompositeToolRegistry makes them visible to the AgentOS.
3. They can be executed successfully.
"""

import pytest
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from nimbus.agentos import create_agent_os
from nimbus.core.runtime.vcpu import LLMClient, LLMResponse

@dataclass
class MockLLMResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None

class MockLLM(LLMClient):
    """Mock LLM that returns a specific tool call."""
    def __init__(self, tool_name: str, tool_args: dict):
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.called = False

    async def chat(self, messages, tools=None, on_chunk=None) -> LLMResponse:
        if self.called:
            return MockLLMResponse(content="Done")
        
        self.called = True
        
        # Verify tool visibility
        tool_names = [t["function"]["name"] for t in tools] if tools else []
        if self.tool_name not in tool_names:
            raise RuntimeError(f"Tool {self.tool_name} not visible to LLM! Available: {tool_names}")
            
        return MockLLMResponse(
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": self.tool_name,
                    "arguments": str(self.tool_args).replace("'", '"')
                }
            }]
        )

@pytest.mark.asyncio
async def test_skill_tool_execution():
    """Test executing a tool from the 'code-scout' skill."""
    # Assuming examples/skills/code-scout exists and has ProjectOverview
    skill_path = Path("examples/skills").resolve()
    if not skill_path.exists():
        pytest.skip("examples/skills not found")

    # Mock LLM to call ProjectOverview
    llm = MockLLM(tool_name="ProjectOverview", tool_args={"path": "."})
    
    # Create AgentOS with skill path
    agent_os = create_agent_os(
        llm_client=llm,
        skill_paths=[skill_path],
        workspace=Path.cwd()
    )
    
    # Check if skill loaded
    assert "code-scout" in agent_os._skill_manager.skills
    assert "ProjectOverview" in agent_os._skill_tools # Check internal registry
    assert "ProjectOverview" in agent_os.list_tools() # Check composite view
    
    # Execute
    result = await agent_os.run("Analyze this project", role="standard")
    
    if result.status != "OK":
        print(f"Result Error: {result.fault}")
        print(f"Output: {result.output}")
    
    assert result.status == "OK"
    # If LLM saw the tool and called it, and it executed without error, we are good.
    # The output might be "Done" (from MockLLM second turn) or tool output depending on logic.
    # But the critical part is that MockLLM didn't raise RuntimeError about visibility
    # and AgentOS didn't raise ToolExecutionError about not found.

