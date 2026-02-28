import asyncio
import os
import sys
import logging
logging.basicConfig(level=logging.INFO, format='%(name)s - %(message)s')

from nimbus.adapters.llm_factory import create_llm_client
from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.runtime.vcpu import VCPUConfig
from nimbus.core.profile import AgentProfile

async def main():
    print("🔧 Initializing Nimbus AgentOS MVP (FSM Edition) ...\n")
    
    # 1. Instantiate the LLM Client using the adapter factory
    llm = await create_llm_client("anthropic/claude-sonnet-4-6")
    
    # 2. Configure AgentOS
    config = AgentOSConfig(
        vcpu_config=VCPUConfig(max_iterations=15),
        kernel_tools=True,
        enable_session=False, # Disable session to run purely ephemeral for the MVP
    )
    
    # 3. Initialize AgentOS
    agent_os = AgentOS(llm_client=llm, config=config)
    
    print("\033[92m🚀 AgentOS MVP Initialized with Claude-Sonnet-4.6. Type 'exit' to quit.\033[0m\n")
    
    session_id = None
    
    while True:
        try:
            user_input = input("\033[94mUser: \033[0m")
            if user_input.lower() in ("exit", "quit"):
                print("Exiting MVP...")
                break
            if not user_input.strip():
                continue
            
            print("\033[90mAgentOS is executing FSM Stream...\033[0m")
            
            # We will use the stream logic inside `agentos.py` directly to show off the SSE equivalent
            
            # Note: We do not call `agent_os.chat` because it returns a full ToolResult
            # synchronously. For the MVP, we use the `run_stream` generator to show intermediate steps.
            
            # But wait: `chat` returns a ToolResult in Interactive mode.
            # To test Streaming (run_stream), we have to use run_stream. Let's spawn via chat, 
            # or just use run_stream since it yields pieces.
            
            has_error = False
            final_output = ""
            
            async for event in agent_os.run_stream(goal=user_input, role="default"):
                event_type = event.get("type", "unknown")
                msg = event.get("message", "")
                
                if event_type == "text":
                    # Thinking/Text
                    print(f"\033[90m[Think]: {event.get('content')}\033[0m")
                elif event_type == "tool_call":
                    # Action
                    name = event.get('name')
                    args = event.get('args')
                    print(f"\033[93m[Action]: Call {name} with args: {args}\033[0m")
                elif event_type == "tool_result":
                    # Observation
                    status = event.get('status')
                    dur = event.get('duration_ms')
                    print(f"\033[90m[Observe]: {name} returned {status} in {dur}ms\033[0m")
                elif event_type == "done":
                    # Final Completion
                    result = event.get('result', {})
                    status = result.get('status')
                    if status == "OK":
                        final_output = result.get('output', '')
                    else:
                        has_error = True
                        final_output = f"Error: {result.get('error')}"
                elif event_type == "error":
                    has_error = True
                    final_output = msg
                    
            if has_error:
                print(f"\n\033[91mError:\033[0m {final_output}\n")
            else:
                print(f"\n\033[92mAssistant:\033[0m {final_output}\n")
            
        except KeyboardInterrupt:
            print("\nExiting MVP...")
            break
        except Exception as e:
            print(f"\n\033[91mFatal Crash:\033[0m {str(e)}")
            break

if __name__ == "__main__":
    asyncio.run(main())
