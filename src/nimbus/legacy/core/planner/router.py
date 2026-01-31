"""Task Router for lightweight complexity-based routing.

This module provides TaskRouter for fast task complexity classification
using a short LLM prompt (~400 characters). The router decides whether
a task should be:
- SIMPLE: Direct reply (synthesize)
- MODERATE: Tool DAG (Read/Glob/Grep)
- COMPLEX: Subagent delegation (coder/explorer/reviewer)

Based on ADR-010: Planner Router Design.

Example:
    >>> router = TaskRouter(llm_client)
    >>> result = await router.route("Read main.py")
    >>> print(result.complexity)
    TaskComplexity.MODERATE
    >>> print(result.suggested_tools)
    ["Read"]
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Protocol, Set

from ..types import TaskDAG, TaskSource
from ..logging import get_logger
from .protocol import PlanningContext, PlannerStage

logger = get_logger("planner.router")


# =============================================================================
# Types
# =============================================================================


class TaskComplexity(str, Enum):
    """Task complexity level for routing decisions.

    Attributes:
        SIMPLE: Can be answered directly without tools.
        MODERATE: Requires 1-3 read-only tools (Read/Glob/Grep).
        COMPLEX: Requires code modification, multi-step iteration, or subagent.
    """

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass
class RoutingResult:
    """Result of task routing decision.

    Attributes:
        complexity: Determined complexity level.
        suggested_tools: Suggested tools for MODERATE tasks.
        subagent_type: Subagent type for COMPLEX tasks (coder/explorer/reviewer).
        confidence: Confidence score (0.0-1.0).
        reasoning: Optional reasoning for the decision.
    """

    complexity: TaskComplexity
    suggested_tools: List[str] = field(default_factory=list)
    subagent_type: Optional[str] = None
    confidence: float = 1.0
    reasoning: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "complexity": self.complexity.value,
            "suggested_tools": self.suggested_tools,
            "subagent_type": self.subagent_type,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


class LLMClient(Protocol):
    """Protocol for LLM client interface."""

    async def complete(self, prompt: str) -> str:
        ...


# =============================================================================
# Routing Patterns (Fallback Rules)
# =============================================================================


# Patterns that indicate SIMPLE tasks (direct reply)
SIMPLE_PATTERNS = [
    # Greetings
    r"^(?:你好|hi|hello|hey|嗨|哈喽|早上好|晚上好|good\s*(?:morning|evening|afternoon))[\s\!\?！？。,.]*$",
    # Thanks
    r"^(?:谢谢|thanks?|thank\s*you|感谢|多谢|thx)[\s\!\?！？。,.]*$",
    # Acknowledgements
    r"^(?:好的|ok|okay|好|嗯|got\s*it|understood|明白了|知道了|收到)[\s\!\?！？。,.]*$",
    # Help/info
    r"^(?:help|帮助|你是谁|who\s*are\s*you|what\s*can\s*you\s*do)[\s\?\?！]*$",
]

# Patterns that indicate COMPLEX tasks (subagent required)
COMPLEX_PATTERNS = [
    # Code modification
    r"(?:修改|改|edit|change|update|fix|修复|添加|add|新增|创建|create|write|实现|implement)\s+.{3,}",
    # Refactoring
    r"(?:重构|refactor|rename|重命名|移动|move|extract|提取)",
    # Multi-file operations
    r"(?:所有文件|all\s*files|多个文件|multiple\s*files|batch|批量)",
    # Running commands/tests
    r"(?:运行|run|execute|执行)\s+(?:the\s+)?(?:测试|tests?|命令|command|脚本|script)",
    # Code generation
    r"(?:生成|generate|创建|create)\s+(?:代码|code|函数|function|类|class|测试|test)",
    # Debugging/fixing
    r"(?:调试|debug|修复|fix)\s+(?:bug|错误|问题|issue|error)",
    # Architecture analysis (complex - needs exploration)
    r"(?:分析|analyze|分析)\s+(?:.*(?:架构|architecture|项目|project|结构|structure))",
    # Code review
    r"(?:审查|review|检查)\s+(?:.*(?:代码|code|模块|module|功能|function|feature))",
]

# Patterns that indicate MODERATE tasks (tool DAG)
MODERATE_PATTERNS = [
    # File reading
    (r"(?:读取?|read|查看|view|打开|open|show|显示)\s+(.+\.(?:py|js|ts|json|yaml|md|txt|toml))", ["Read"]),
    # File search with extension (before generic search)
    (r"(?:find|找|locate)\s+\.?(?:py|js|ts|json|yaml)\s*(?:files?|文件)?", ["Glob"]),
    # File listing
    (r"(?:列出|list|ls|目录|dir|文件列表)", ["Glob"]),
    # Pattern search
    (r"(?:哪些?|which|what)\s*(?:文件|files?)\s*(?:包含|contain|有)", ["Grep"]),
    # File search (generic)
    (r"(?:找|find|locate)\s*(?:文件|files?)", ["Glob"]),
    # Code/content search (last as it's more general)
    (r"(?:搜索|search|查找|grep)\s+(.+)", ["Grep"]),
]

# Subagent type keywords
SUBAGENT_TYPE_KEYWORDS = {
    "coder": [
        "edit", "change", "modify", "fix", "add", "implement", "create", "write",
        "修改", "改", "修复", "添加", "实现", "创建", "写",
    ],
    "explorer": [
        "explore", "analyze", "understand", "explain", "architecture",
        "探索", "分析", "理解", "解释", "架构", "结构",
    ],
    "reviewer": [
        "review", "check", "audit", "examine",
        "审查", "检查", "审计", "复查",
    ],
    "researcher": [
        "research", "investigate", "study",
        "调研", "研究", "学习",
    ],
}


# =============================================================================
# TaskRouter
# =============================================================================


class TaskRouter:
    """Lightweight task router for complexity-based routing.

    Uses a short LLM prompt (~400 characters) to classify task complexity
    and route to appropriate handler:
    - SIMPLE: Direct synthesize response
    - MODERATE: Read-only tool DAG (Read/Glob/Grep)
    - COMPLEX: Subagent delegation

    Attributes:
        llm_client: LLM client for completion.
        enable_llm: Whether to use LLM for routing (vs. rules only).
    """

    # Router prompt template - MUST be < 500 characters (excluding {goal})!
    # Uses double braces for literal braces in JSON examples
    ROUTER_PROMPT_TEMPLATE = """判断任务复杂度。只输出一行 JSON。

分类规则:
- SIMPLE: 直接回复（问候、感谢、基于对话能回答）
- MODERATE: 1-3个只读工具（读文件、搜索、列目录）
- COMPLEX: 修改代码、创建文件、运行命令、多步迭代

示例:
"你好" -> {{"level":"SIMPLE"}}
"读取 main.py" -> {{"level":"MODERATE","tools":["Read"]}}
"给函数添加docstring" -> {{"level":"COMPLEX","type":"coder"}}

任务: {goal}
"""

    # Backward compatibility alias
    @property
    def ROUTER_PROMPT(self) -> str:
        """Get the router prompt template (for testing)."""
        return self.ROUTER_PROMPT_TEMPLATE

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        enable_llm: bool = True,
    ):
        """Initialize the router.

        Args:
            llm_client: LLM client for completion. Required if enable_llm=True.
            enable_llm: Whether to use LLM routing. If False, uses rule-based fallback.
        """
        self.llm_client = llm_client
        self.enable_llm = enable_llm and llm_client is not None

    async def route(
        self,
        goal: str,
        context: Optional[str] = None,
    ) -> RoutingResult:
        """Route a task to the appropriate handler.

        Args:
            goal: User's goal/request.
            context: Optional conversation context.

        Returns:
            RoutingResult with complexity and suggestions.
        """
        # Try LLM routing first
        if self.enable_llm and self.llm_client:
            try:
                result = await self._llm_route(goal, context)
                logger.debug(f"LLM routing: {goal[:30]}... -> {result.complexity.value}")
                return result
            except Exception as e:
                logger.warning(f"LLM routing failed, falling back to rules: {e}")

        # Fallback to rule-based routing
        result = self._fallback_routing(goal)
        logger.debug(f"Rule routing: {goal[:30]}... -> {result.complexity.value}")
        return result

    async def _llm_route(
        self,
        goal: str,
        context: Optional[str] = None,
    ) -> RoutingResult:
        """Route using LLM.

        Args:
            goal: User's goal.
            context: Optional context.

        Returns:
            RoutingResult from LLM response.
        """
        # Build prompt
        prompt = self.ROUTER_PROMPT_TEMPLATE.format(goal=goal)

        # Call LLM
        response = await self.llm_client.complete(prompt)

        # Parse response
        return self._parse_response(response)

    def _parse_response(self, response: str) -> RoutingResult:
        """Parse LLM response into RoutingResult.

        Args:
            response: LLM response string.

        Returns:
            Parsed RoutingResult.

        Raises:
            ValueError: If response cannot be parsed.
        """
        # Clean response - extract JSON from potential markdown code block
        response = response.strip()
        if response.startswith("```"):
            # Remove markdown code block
            lines = response.split("\n")
            json_lines = [l for l in lines if not l.startswith("```")]
            response = "\n".join(json_lines).strip()

        # Try to find JSON in response
        json_match = re.search(r"\{[^}]+\}", response)
        if not json_match:
            raise ValueError(f"No JSON found in response: {response[:100]}")

        json_str = json_match.group()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {json_str}") from e

        # Parse level
        level = data.get("level", "MODERATE").upper()
        try:
            complexity = TaskComplexity(level.lower())
        except ValueError:
            complexity = TaskComplexity.MODERATE

        # Parse tools
        tools = data.get("tools", [])
        if isinstance(tools, str):
            tools = [tools]

        # Parse subagent type
        subagent_type = data.get("type")

        return RoutingResult(
            complexity=complexity,
            suggested_tools=tools,
            subagent_type=subagent_type,
            confidence=0.9,  # LLM-based has high confidence
        )

    def _fallback_routing(self, goal: str) -> RoutingResult:
        """Rule-based fallback routing.

        Args:
            goal: User's goal.

        Returns:
            RoutingResult based on pattern matching.
        """
        goal_lower = goal.lower().strip()

        # Check SIMPLE patterns
        for pattern in SIMPLE_PATTERNS:
            if re.match(pattern, goal_lower, re.IGNORECASE):
                return RoutingResult(
                    complexity=TaskComplexity.SIMPLE,
                    confidence=1.0,
                    reasoning="matched_simple_pattern",
                )

        # Check COMPLEX patterns
        for pattern in COMPLEX_PATTERNS:
            if re.search(pattern, goal_lower, re.IGNORECASE):
                # Determine subagent type
                subagent_type = self._determine_subagent_type(goal_lower)
                return RoutingResult(
                    complexity=TaskComplexity.COMPLEX,
                    subagent_type=subagent_type,
                    confidence=0.8,
                    reasoning="matched_complex_pattern",
                )

        # Check MODERATE patterns
        for pattern, tools in MODERATE_PATTERNS:
            if re.search(pattern, goal_lower, re.IGNORECASE):
                return RoutingResult(
                    complexity=TaskComplexity.MODERATE,
                    suggested_tools=tools,
                    confidence=0.8,
                    reasoning="matched_moderate_pattern",
                )

        # Default to MODERATE (conservative)
        return RoutingResult(
            complexity=TaskComplexity.MODERATE,
            suggested_tools=["Glob", "Read"],
            confidence=0.5,
            reasoning="default_moderate",
        )

    def _determine_subagent_type(self, goal: str) -> str:
        """Determine subagent type from goal keywords.

        Args:
            goal: User's goal (lowercased).

        Returns:
            Subagent type string.
        """
        for subagent_type, keywords in SUBAGENT_TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in goal:
                    return subagent_type

        # Default to coder for code modifications
        return "coder"


# =============================================================================
# TaskRouterStage
# =============================================================================


class TaskRouterStage:
    """PlannerPipeline stage wrapper for TaskRouter.

    Integrates TaskRouter into the planning pipeline, allowing it to
    short-circuit planning for SIMPLE and COMPLEX tasks.

    Attributes:
        router: TaskRouter instance.
        name: Stage name for pipeline identification.
    """

    def __init__(self, router: TaskRouter):
        """Initialize the stage.

        Args:
            router: TaskRouter instance.
        """
        self.router = router

    @property
    def name(self) -> str:
        """Stage name."""
        return "task_router"

    async def process(self, ctx: PlanningContext) -> PlanningContext:
        """Process planning context with routing.

        For SIMPLE tasks: Creates a synthesize DAG and sets early_exit.
        For COMPLEX tasks: Creates a Subagent DAG and sets early_exit.
        For MODERATE tasks: Continues to next stage (LLMEnhancer).

        Args:
            ctx: Planning context.

        Returns:
            Updated planning context.
        """
        # Route the task
        result = await self.router.route(ctx.goal, ctx.conversation_context)

        # Store routing result in metadata
        ctx.metadata["routing_result"] = result.to_dict()

        logger.info(
            f"Routing decision: {result.complexity.value} "
            f"(confidence={result.confidence:.2f})"
        )

        if result.complexity == TaskComplexity.SIMPLE:
            # Direct reply - create synthesize DAG
            ctx.final_dag = TaskDAG.create_simple(
                "synthesize",
                {"message": ctx.goal, "direct": True},
            )
            ctx.final_dag.nodes[list(ctx.final_dag.nodes.keys())[0]].source = TaskSource.RULE
            ctx.early_exit = True
            ctx.metadata["routing_action"] = "direct_reply"
            logger.debug("Routing to direct reply (synthesize)")

        elif result.complexity == TaskComplexity.COMPLEX:
            # Subagent delegation
            ctx.final_dag = self._create_subagent_dag(ctx.goal, result)
            ctx.early_exit = True
            ctx.metadata["routing_action"] = "subagent_delegation"
            logger.debug(f"Routing to subagent: {result.subagent_type}")

        else:
            # MODERATE - continue to LLMEnhancer
            # Store suggested tools for LLMEnhancer to use
            ctx.metadata["routing_action"] = "continue"
            ctx.metadata["suggested_tools"] = result.suggested_tools
            logger.debug(f"Routing to tool DAG with tools: {result.suggested_tools}")

        return ctx

    def _create_subagent_dag(self, goal: str, result: RoutingResult) -> TaskDAG:
        """Create a DAG that delegates to a Subagent.

        The subagent is instructed to first explore the file structure before
        making changes, to ensure correct file paths are used.

        Args:
            goal: User's goal.
            result: Routing result.

        Returns:
            TaskDAG with Subagent task.
        """
        subagent_type = result.subagent_type or "coder"

        # Map subagent types to descriptions
        descriptions = {
            "coder": "Code implementation task",
            "explorer": "Code exploration task",
            "reviewer": "Code review task",
            "researcher": "Research task",
        }

        # Enhanced prompt that instructs subagent to explore first
        enhanced_prompt = f"""## Task
{goal}

## Instructions
IMPORTANT: Before making any changes, first use Glob to explore the file structure and find the exact file paths. User-provided filenames may be relative or incomplete.

Steps:
1. Use Glob to find files matching the task (e.g., Glob pattern="**/*.py" for Python files)
2. Use Read to examine the actual file content at the correct path
3. Make the requested changes using Edit or Write"""

        dag = TaskDAG.create(
            goal=goal,
            tasks=[
                {
                    "id": "t1_subagent",
                    "skill": "Subagent",
                    "params": {
                        "prompt": enhanced_prompt,
                        "subagent_type": subagent_type,
                        "description": descriptions.get(subagent_type, "Task"),
                    },
                    "source": "rule",
                },
                {
                    "id": "t2_synthesize",
                    "skill": "synthesize",
                    "params": {"message": "Summarize subagent results"},
                    "depends_on": ["t1_subagent"],
                    "source": "rule",
                },
            ],
        )

        return dag
