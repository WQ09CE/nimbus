import asyncio
import logging
import traceback
from typing import List, Tuple, Optional

from nimbus.core.protocol import ActionIR, ToolResult
from nimbus.core.runtime.fsm import FSMContext

logger = logging.getLogger("kernel.vcpu.tool_executor")

class ToolExecutor:
    """Executes FSM Actions with concurrency and user interrupt support."""

    async def execute_all(
        self,
        ctx: FSMContext,
        executable_actions: List[ActionIR]
    ) -> Tuple[List[ToolResult], Optional[Exception]]:
        """
        Executes a list of actions and returns their results. 
        If an error is encountered that should trigger FSM recovery, it returns the Exception.
        """
        results: List[ToolResult] = []
        
        # 1. Serial path: 0 or 1 executable action — no concurrency overhead
        if len(executable_actions) <= 1:
            for action in executable_actions:
                logger.info(f"⚙️  [vCPU] Executing Tool: {action.name}")
                result, error = await self._execute_one(action, ctx)
                results.append(result)
                if error:
                    return results, error
            return results, None

        # 2. Concurrent path: 2+ executable actions
        tool_names = [action.name for action in executable_actions]
        logger.info(f"⚡ [vCPU] Executing {len(executable_actions)} tools concurrently: {tool_names}")
        
        # Launch all concurrently and collect results in original order
        outcomes = await asyncio.gather(*[
            self._execute_one(action, ctx) for action in executable_actions
        ])
        
        # 3. Collect results in order and check for errors
        first_error = None
        for result, error in outcomes:
            results.append(result)
            if error is not None and first_error is None:
                first_error = error
                
        return results, first_error

    async def _execute_one(
        self,
        action: ActionIR,
        ctx: FSMContext
    ) -> Tuple[ToolResult, Optional[Exception]]:
        """Execute a single action with independent error handling."""
        try:
            if ctx.config.dry_run:
                logger.info(f"🌵 [Dry-Run] Simulating tool: {action.name}")
                result = ToolResult(
                    status="OK",
                    output=f"[Dry-Run] Successfully simulated execution of {action.name} with args {action.args}"
                )
            else:
                tool_task = asyncio.create_task(ctx.gate.syscall_tool(action))
                interrupt_task = None
                tasks = [tool_task]
                
                if ctx.interrupt_event:
                    interrupt_task = asyncio.create_task(ctx.interrupt_event.wait())
                    tasks.append(interrupt_task)
                    
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                
                if interrupt_task and interrupt_task in done:
                    # Cancel the actual tool task
                    tool_task.cancel()
                    try:
                        await tool_task
                    except asyncio.CancelledError:
                        pass
                        
                    # Only clear once if multiple tasks race to clear
                    if ctx.interrupt_event.is_set():
                        ctx.interrupt_event.clear()
                    raise Exception("Tool execution interrupted by user.")
                    
                # We finished the tool task cleanly, cancel the interrupt waiter if it exists
                if interrupt_task:
                    interrupt_task.cancel()
                    
                result = tool_task.result()
            
            if not hasattr(action, 'result'):
                action.result = result
            return result, None
            
        except Exception as e:
            logger.warning(f"Tool {action.name} failed with Exception: {e}")
            result = ToolResult(
                status="ERROR",
                output=f"Tool failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            )
            action.result = result
            return result, e
