"""
Failure Reporter - 生成用户友好的失败报告

当 Agent 执行失败时，生成自然语言的失败报告，
让用户了解发生了什么以及可能的解决方案。

设计原则：
- 优先使用 LLM 生成自然语言报告
- 提供模板回退，确保始终有输出
- 保持用户友好，避免技术术语
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol
import re


class LLMClient(Protocol):
    """LLM 客户端协议"""
    
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        ...


@dataclass
class FailureContext:
    """
    失败上下文信息
    
    Attributes:
        goal: 用户的原始目标
        fault_code: 错误代码
        fault_message: 错误消息
        iterations: 执行的迭代次数
        recent_errors: 最近的错误列表
    """
    goal: str
    fault_code: str
    fault_message: str
    iterations: int
    recent_errors: Optional[List[str]] = None


class FailureReporter:
    """
    失败报告生成器
    
    支持两种模式：
    1. LLM 模式：使用 LLM 生成自然语言报告
    2. 模板模式：使用预定义模板生成报告
    
    Example:
        reporter = FailureReporter(llm_client)
        
        ctx = FailureContext(
            goal="Fix the bug in main.py",
            fault_code="ITERATION_LIMIT",
            fault_message="Exceeded 50 iterations",
            iterations=50,
        )
        
        report = await reporter.generate_report(ctx)
        print(report)
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        """
        初始化报告生成器
        
        Args:
            llm_client: 可选的 LLM 客户端，用于生成自然语言报告
        """
        self.llm = llm_client
    
    async def generate_report(
        self, 
        ctx: FailureContext,
        conversation_context: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        生成失败报告
        
        优先使用 LLM 生成自然语言报告，失败时使用模板。
        
        Args:
            ctx: 失败上下文
            conversation_context: 可选的对话上下文（用于 LLM）
            
        Returns:
            用户友好的失败报告
        """
        if self.llm:
            try:
                return await self._generate_llm_report(ctx, conversation_context)
            except Exception:
                pass
        
        return self._generate_template_report(ctx)
    
    async def _generate_llm_report(
        self,
        ctx: FailureContext,
        conversation_context: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        使用 LLM 生成自然语言失败报告
        
        让 LLM 基于对话上下文生成一个友好的、总结性的失败报告。
        """
        # 构建提示
        system_prompt = (
            "You are a helpful AI assistant. The task has failed due to an error. "
            "Generate a brief, friendly response explaining what happened and "
            "suggesting next steps. Keep it conversational and under 100 words. "
            "Do NOT use markdown formatting. Respond in the same language as the user's goal."
        )
        
        user_prompt = (
            f"The user asked: \"{ctx.goal}\"\n\n"
            f"After {ctx.iterations} attempts, the task failed with:\n"
            f"Error: [{ctx.fault_code}] {ctx.fault_message}\n\n"
            f"Please generate a friendly failure message."
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        response = await self.llm.chat(messages, tools=None)
        
        # 提取响应内容
        content = getattr(response, 'content', None)
        if content:
            return content.strip()
        
        # 如果响应格式不符，回退到模板
        return self._generate_template_report(ctx)
    
    def _generate_template_report(self, ctx: FailureContext) -> str:
        """
        使用模板生成失败报告
        
        根据错误类型选择合适的模板，生成用户友好的报告。
        """
        fault_code = ctx.fault_code
        fault_message = ctx.fault_message
        goal = ctx.goal
        iterations = ctx.iterations
        
        # 根据错误类型生成不同的报告
        if fault_code == "ITERATION_LIMIT":
            return (
                f"I worked on this for {iterations} steps but couldn't complete it. "
                f"The task \"{self._truncate(goal, 50)}\" turned out to be more complex "
                f"than expected. Would you like me to try a different approach, "
                f"or break this into smaller steps?"
            )
        
        if fault_code == "DOOM_LOOP":
            return (
                f"I got stuck in a loop while working on \"{self._truncate(goal, 50)}\". "
                f"I kept trying the same approach but it wasn't working. "
                f"Could you provide more context or suggest an alternative approach?"
            )
        
        if fault_code == "TIMEOUT":
            return (
                f"The operation timed out while working on \"{self._truncate(goal, 50)}\". "
                f"This might be due to a slow network or a resource-intensive task. "
                f"Would you like me to try again with more time, or take a different approach?"
            )
        
        if fault_code == "PERMISSION_DENIED":
            return (
                f"I don't have permission to complete \"{self._truncate(goal, 50)}\". "
                f"This might require elevated privileges or access to protected resources. "
                f"Please check the permissions and try again."
            )
        
        if fault_code == "LLM_ERROR":
            return (
                f"I encountered a problem with the AI service while working on "
                f"\"{self._truncate(goal, 50)}\". This is usually temporary. "
                f"Would you like me to try again?"
            )
        
        if fault_code == "COMPACTION_FAILED":
            return (
                f"I ran into a memory issue while working on \"{self._truncate(goal, 50)}\". "
                f"The conversation got too long. Let's start fresh with a more focused request."
            )
        
        if fault_code == "EXCESSIVE_FAILURES":
            return (
                f"I tried multiple approaches but couldn't find what I was looking for. "
                f"The files or patterns you mentioned might not exist in this workspace. "
                f"Could you double-check the paths or provide more details?"
            )
        
        # 通用回退
        return (
            f"I ran into some trouble completing this task. "
            f"Error: {fault_message}. "
            f"Let me know if you'd like me to try a different approach."
        )
    
    def _truncate(self, text: str, max_length: int) -> str:
        """截断文本，保持可读性"""
        if len(text) <= max_length:
            return text
        return text[:max_length - 3] + "..."
    
    def format_doom_loop_error(
        self,
        tool_name: str,
        threshold: int,
        guidance: str,
    ) -> str:
        """
        格式化 Doom Loop 错误消息
        
        Args:
            tool_name: 触发 doom loop 的工具名
            threshold: 触发阈值
            guidance: 恢复指导
            
        Returns:
            格式化的错误消息
        """
        return (
            f"[Operation Failed] The {tool_name} operation failed after multiple attempts.\n\n"
            f"What happened: The same operation was tried {threshold} times without success.\n\n"
            f"Recovery guidance:\n{guidance}\n\n"
            f"IMPORTANT: Please call return_result now to report what you were trying to do "
            f"and what obstacle you encountered. Do NOT retry the same operation."
        )
