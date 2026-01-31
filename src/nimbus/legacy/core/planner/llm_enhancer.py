"""LLM-based planning enhancement stage.

This module provides the LLM-based planning stage that can generate
or enhance DAGs using an LLM. It wraps the existing DAGPlanner logic.
"""

import json
import re
import uuid
from typing import Protocol, Set, Optional, Dict, Any, List

from ..types import TaskDAG, TaskNode, TaskSource
from ..logging import get_logger
from .protocol import PlannerStage, PlanningContext, PlanningMode
from .validator import DAGValidator, ValidationResult

logger = get_logger("planner.llm")


class LLMClient(Protocol):
    """Protocol for LLM client interface."""

    async def complete(self, prompt: str) -> str:
        """Generate completion for the given prompt."""
        ...


# LLM Planning prompt
LLM_PLANNING_PROMPT = """你是一个任务规划器。根据用户目标，生成一个可并行执行的任务 DAG（有向无环图）。

【角色澄清 - 在回答前必须执行】
当前正在和你对话的是 User（人类用户）。你是 Assistant（AI助手）。

⚠️ 代词转换规则（用户消息中）：
- "我" = User（人类用户，不是你AI）
- "你" = Assistant（你，AI）
- "我问你的第一个问题" = User 发给 Assistant 的第一条消息
- "你说了什么" = Assistant 的回复

⚠️ 回复时的规则：
当你用 direct 模式回复时，记住你是 Assistant，要从 Assistant 的视角回复：
- 用户问"我问的第一个问题是什么" → 你应该找 User 的第一条消息，回答"你问的第一个问题是'xxx'"
- 用户问"你说了什么" → 你应该找你（Assistant）的消息

示例：
- 对话历史: User: "你好"  Assistant: "有什么可以帮你的吗？"
- 用户问: "我问你的第一个问题是什么？"
- 分析: "我" = User，所以要找 User 的第一条消息 = "你好"
- 正确回复: {{"mode": "direct", "response": "你问我的第一个问题是'你好'。"}}
- 错误回复: {{"mode": "direct", "response": "我问你的第一个问题是..."}} ← 这里混淆了角色

【重要规则】
1. 只输出 JSON，不要输出任何其他内容。
2. **优先使用上下文回答** - 如果用户的问题可以基于对话历史中的信息直接回答，使用 direct 模式直接回答，无需调用工具。
3. 如果用户使用指代词（"它"、"这个"、"那个"、"刚才"、"其中"等），先从对话历史中找到指代对象。
4. **工具优先原则** - 以下情况必须使用工具，禁止用 direct 模式：
   - 用户要求执行命令（run/execute/echo）→ 必须使用 Bash 工具
   - 用户要求读取文件/代码 → 必须使用 Read 工具
   - 用户要求总结文件内容 → 必须先用 Read 工具读取文件
   - 用户要求查找文件 → 必须使用 Glob 工具
   - 用户要求搜索代码 → 必须使用 Grep 工具
   - 用户要求创建文件 → 必须使用 Write 工具
   - 用户要求编辑/修改文件（添加docstring、修复错误等）→ 必须先 Read 后 Edit

## 可用技能
{skills}

## 可用工具 (用于代码探索和命令执行)

- **Read**: 读取文件内容
  - file_path (string, required): 文件路径
  - offset (integer, optional): 起始行号，默认 0
  - limit (integer, optional): 最大行数，默认 2000

- **Glob**: 查找匹配模式的文件
  - pattern (string, required): glob 模式，如 "*.py" 或 "**/*.py"
  - path (string, optional): 搜索目录，默认 "."
  - limit (integer, optional): 最大结果数，默认 100

- **Grep**: 在文件中搜索正则表达式
  - pattern (string, required): 正则表达式
  - path (string, optional): 搜索目录，默认 "."
  - glob (string, optional): 文件模式过滤
  - type (string, optional): 文件类型 (py, js, ts, go...)
  - max_matches (integer, optional): 最大匹配数，默认 50

- **Write**: 写入或创建文件
  - file_path (string, required): 文件路径
  - content (string, required): 文件内容

- **Edit**: 编辑现有文件（精确字符串替换）
  - file_path (string, required): 文件路径
  - old_string (string, required): 要替换的原始文本
  - new_string (string, required): 替换后的新文本
  - replace_all (boolean, optional): 是否替换所有匹配项，默认 false

- **Bash**: 执行 shell 命令
  - command (string, required): 要执行的 shell 命令
  - timeout (integer, optional): 超时时间(毫秒)，默认 120000
  - cwd (string, optional): 工作目录

- **Synthesize**: 【仅用于综合分析工具结果】生成人类可读的分析报告
  - message (string, required): 用户的原始问题（直接复制用户的问题，不要自己回答）
  - context (string, optional): 依赖任务的结果会自动注入
  - **重要**: Synthesize 只在有工具依赖时使用，简单对话问题必须用 direct 模式

## 对话历史
{context}

## 当前用户目标
{goal}

{existing_plan_section}

## 判断流程
1. 用户的问题能否从对话历史中直接回答？
   - 能 → 使用 direct 模式
   - 不能 → 使用 dag 模式

## ⚠️ 必须用 direct 模式的情况（不需要任何工具）
- 问候语：你好、hi、hello、早上好、晚上好
- 感谢语：谢谢、thanks、感谢
- 简单对话：闲聊、确认、道别
- 基于对话历史能回答的问题：比如"我说了几次你好"
- **重要**：这些情况绝对不能使用 Synthesize 或任何工具！

## 输出格式

简单对话或基于上下文可直接回答的问题:
{{"mode": "direct", "response": "你的回复"}}

需要执行工具/技能的任务:
{{
  "mode": "dag",
  "tasks": [
    {{"id": "t1", "skill": "Read", "params": {{"file_path": "pyproject.toml"}}, "depends_on": []}},
    {{"id": "t2", "skill": "Glob", "params": {{"pattern": "**/*.py"}}, "depends_on": []}}
  ]
}}

## 规则
1. 如果任务可以并行执行，depends_on 设为空数组 []
2. skill 必须从可用技能或工具列表中选择
3. 工具参数必须按照上面的说明格式
4. **基于上下文能回答的问题，必须使用 direct 模式**
5. **分析/总结任务**: 当用户要求分析、总结、解释某个内容时，应该：
   - 先用工具(Read/Glob/Grep等)收集信息
   - 然后添加一个 Synthesize 任务来综合分析，并设置 depends_on 指向信息收集任务

## ⚠️ 文件路径规则（重要！）
1. **用户提供的文件名可能不完整** - 如 `user_manager.py` 可能实际是 `src/user_manager.py`
2. **编辑/修改文件前必须先确认路径**:
   - 先用 Glob 搜索文件（如 `**/*user_manager*.py`）
   - 找到准确路径后再用 Read 读取
   - 最后用 Edit 修改
3. **禁止使用模板变量** - 不要使用 `{{t1.results[0]}}` 这样的语法，框架不支持
4. **使用具体路径** - 如果不确定路径，用 Glob 搜索模式而不是猜测路径

## 示例

示例1：用户说"读取 pyproject.toml"
{{"mode": "dag", "tasks": [{{"id": "t1", "skill": "Read", "params": {{"file_path": "pyproject.toml"}}, "depends_on": []}}]}}

示例2：用户说"列出 src 目录的 Python 文件"
{{"mode": "dag", "tasks": [{{"id": "t1", "skill": "Glob", "params": {{"pattern": "**/*.py", "path": "src"}}, "depends_on": []}}]}}

示例3：（对话历史中已读取了 pyproject.toml 显示 name="nimbus"）用户问"这个项目叫什么"
{{"mode": "direct", "response": "根据 pyproject.toml，这个项目叫 nimbus。"}}

示例4：用户说"run the command: echo 'hello world'"
{{"mode": "dag", "tasks": [{{"id": "t1", "skill": "Bash", "params": {{"command": "echo 'hello world'"}}, "depends_on": []}}]}}

示例5：用户说"读取 src/agent.py 并总结它的主要功能"
{{"mode": "dag", "tasks": [{{"id": "t1", "skill": "Read", "params": {{"file_path": "src/agent.py"}}, "depends_on": []}}]}}

示例6：用户说"读取 test.py 并告诉我它包含什么函数"
{{"mode": "dag", "tasks": [{{"id": "t1", "skill": "Read", "params": {{"file_path": "test.py"}}, "depends_on": []}}]}}

示例7：用户说"创建一个 hello.py 文件，包含 say_hello 函数"
{{"mode": "dag", "tasks": [{{"id": "t1", "skill": "Write", "params": {{"file_path": "hello.py", "content": "def say_hello():\\n    print('Hello World')"}}, "depends_on": []}}]}}

示例8：用户说"给 main.py 中的 hello 函数添加文档字符串"
{{"mode": "dag", "tasks": [
  {{"id": "t1", "skill": "Read", "params": {{"file_path": "main.py"}}, "depends_on": []}},
  {{"id": "t2", "skill": "Edit", "params": {{"file_path": "main.py", "old_string": "def hello(name):", "new_string": "def hello(name):\\n    \\\"\\\"\\\"Say hello to someone.\\\"\\\"\\\""}}, "depends_on": ["t1"]}}
]}}

示例9：用户说"修复 broken.py 中的语法错误"（注意：先用 Glob 找到准确路径）
{{"mode": "dag", "tasks": [
  {{"id": "t1", "skill": "Glob", "params": {{"pattern": "**/broken.py"}}, "depends_on": []}},
  {{"id": "t2", "skill": "Read", "params": {{"file_path": "src/broken.py"}}, "depends_on": ["t1"]}},
  {{"id": "t3", "skill": "Edit", "params": {{"file_path": "src/broken.py", "old_string": "错误的代码", "new_string": "修复后的代码"}}, "depends_on": ["t2"]}}
]}}

示例9b：用户说"在 user_manager.py 的 UserManager 类中重命名方法"
{{"mode": "dag", "tasks": [
  {{"id": "t1", "skill": "Glob", "params": {{"pattern": "**/*user_manager*.py"}}, "depends_on": []}},
  {{"id": "t2", "skill": "Read", "params": {{"file_path": "src/user_manager.py"}}, "depends_on": ["t1"]}},
  {{"id": "t3", "skill": "Edit", "params": {{"file_path": "src/user_manager.py", "old_string": "def old_name(", "new_string": "def new_name("}}, "depends_on": ["t2"]}}
]}}

示例10：用户说"分析一下项目的架构"
{{"mode": "dag", "tasks": [
  {{"id": "t1", "skill": "Glob", "params": {{"pattern": "**/*.py", "limit": 50}}, "depends_on": []}},
  {{"id": "t2", "skill": "Synthesize", "params": {{"message": "分析一下项目的架构"}}, "depends_on": ["t1"]}}
]}}

示例11：用户说"我一共发了几次你好"（简单对话问题，从历史可以回答）
{{"mode": "direct", "response": "根据对话历史，你发了 X 次'你好'。"}}

示例12：用户说"你好"（简单问候）
{{"mode": "direct", "response": "你好！有什么我可以帮助你的吗？"}}

JSON:"""

EXISTING_PLAN_SECTION = """
## 已有规则计划
以下是规则引擎生成的初步计划，请评估是否需要增强或修改：
{rule_plan}

如果规则计划已经完整，直接返回 {{"mode": "continue"}}
如果需要增强或修改，返回完整的新计划。
"""

REPLAN_SECTION = """
## ⚠️ 重规划模式 (Attempt {attempt})

上次执行失败，需要重新规划。以下是失败信息：

{failure_summary}

### 已完成的任务及其结果
{completed_tasks_with_results}

### 重规划要求
1. **分析失败原因** - 理解为什么上次执行失败
2. **使用已获得的结果** - 上面显示的任务结果包含正确的文件路径，请在新计划中使用这些路径
3. **修复问题** - 调整参数、更换方案或添加错误处理
4. **避免重复错误** - 不要使用相同的参数重试明显会失败的操作

### 常见问题和解决方案
- **文件不存在**：使用上面 Glob 任务找到的正确路径，不要猜测路径
- **old_string 不匹配**（Edit 失败）：
  1. 仔细查看上面 Read 任务的结果，从中**精确复制**要替换的文本（包括所有空格、缩进、换行）
  2. 如果 Edit 已经失败 2 次以上，**放弃 Edit**，改用 **Write** 工具完全重写整个文件
- 权限错误：检查是否有访问权限
- 超时：考虑简化操作或分解任务
- 参数错误：检查参数格式是否正确

⚠️ **重要**:
- 不要使用模板变量如 `{{{{t1.results[0]}}}}`, 请直接使用上面显示的实际结果值
- Edit 的 old_string 必须与文件内容**完全相同**，包括空格和缩进！

请输出修正后的新计划 JSON。
"""


class LLMEnhancer:
    """LLM-based planning stage - wraps existing DAGPlanner logic.

    This stage uses an LLM to generate or enhance execution plans.
    It can work in three modes:
    1. Full planning: Generate entire DAG from scratch
    2. Enhancement: Enhance an existing rule-based DAG
    3. Skip: If rule DAG is complete, skip LLM call

    Example:
        ```python
        enhancer = LLMEnhancer(llm_client)
        ctx = PlanningContext(goal="...", ...)

        ctx = await enhancer.process(ctx)
        if ctx.llm_dag:
            # LLM generated or enhanced a DAG
            pass
        ```
    """

    def __init__(
        self,
        llm_client: LLMClient,
        validator: Optional[DAGValidator] = None,
    ):
        """Initialize the LLM enhancer.

        Args:
            llm_client: LLM client for generating completions.
            validator: Optional DAG validator for validating LLM output.
        """
        self.llm_client = llm_client
        self.validator = validator or DAGValidator()

    @property
    def name(self) -> str:
        """Stage name for logging/tracing."""
        return "llm_enhancer"

    async def process(self, ctx: PlanningContext) -> PlanningContext:
        """Generate or enhance DAG using LLM.

        Processing logic:
        1. If mode is RULE_ONLY, skip LLM call
        2. If rule_dag exists and is complete, optionally skip
        3. If rule_dag is partial, ask LLM to enhance
        4. If no rule_dag, ask LLM for full planning

        Args:
            ctx: The planning context.

        Returns:
            Updated planning context with llm_dag set.
        """
        # Skip if rule-only mode
        if ctx.planning_mode == PlanningMode.RULE_ONLY:
            logger.debug("Skipping LLM: rule_only mode")
            return ctx

        # Skip if we already have early_exit set
        if ctx.early_exit:
            logger.debug("Skipping LLM: early_exit flag set")
            return ctx

        # Determine if we should skip LLM
        if ctx.rule_dag and self._is_complete_plan(ctx.rule_dag, ctx):
            logger.debug("Skipping LLM: rule DAG is complete")
            ctx.llm_dag = ctx.rule_dag
            ctx.final_dag = ctx.rule_dag
            return ctx

        # Build prompt
        prompt = self._build_prompt(ctx)

        try:
            logger.debug(f"Invoking LLM for goal: {ctx.goal[:50]}...")
            response = await self.llm_client.complete(prompt)

            # Parse LLM response
            dag = self._parse_response(response, ctx)

            if dag:
                # Mark all tasks as LLM-generated
                for node in dag.nodes.values():
                    if node.source == TaskSource.RULE:
                        node.source = TaskSource.LLM

                # Validate DAG
                if ctx.skill_whitelist:
                    self.validator.skill_whitelist = ctx.skill_whitelist

                result = self.validator.validate(dag)
                if not result.valid:
                    if result.repaired_dag:
                        dag = result.repaired_dag
                        ctx.warnings.extend(result.warnings)
                    else:
                        ctx.errors.extend(result.errors)
                        logger.warning(f"LLM DAG validation failed: {result.errors}")
                        # Fallback to rule DAG if available
                        if ctx.rule_dag:
                            dag = ctx.rule_dag
                        else:
                            dag = self._create_fallback_dag(ctx)

                ctx.llm_dag = dag
                ctx.final_dag = dag
                logger.info(f"LLM generated DAG with {len(dag.nodes)} tasks")

        except Exception as e:
            logger.error(f"LLM planning failed: {e}")
            ctx.add_error(f"LLM planning failed: {str(e)}")

            # Fallback
            if ctx.rule_dag:
                ctx.final_dag = ctx.rule_dag
            else:
                ctx.final_dag = self._create_fallback_dag(ctx)

        return ctx

    def _build_prompt(self, ctx: PlanningContext) -> str:
        """Build the LLM prompt.

        Args:
            ctx: Planning context.

        Returns:
            Formatted prompt string.
        """
        skills_desc = self._format_skills(ctx.available_skills)

        existing_plan_section = ""

        # Handle replanning case
        if ctx.is_replan and ctx.failed_tasks:
            failure_summary = ctx.get_failure_summary()
            completed_tasks_with_results = self._format_completed_tasks(ctx)
            existing_plan_section = REPLAN_SECTION.format(
                attempt=ctx.replan_attempt,
                failure_summary=failure_summary,
                completed_tasks_with_results=completed_tasks_with_results,
            )
        elif ctx.rule_dag and ctx.planning_mode == PlanningMode.HYBRID:
            # Include existing rule plan for enhancement
            rule_plan_summary = self._summarize_dag(ctx.rule_dag)
            existing_plan_section = EXISTING_PLAN_SECTION.format(rule_plan=rule_plan_summary)

        return LLM_PLANNING_PROMPT.format(
            skills=skills_desc,
            context=ctx.conversation_context or "无上下文",
            goal=ctx.goal,
            existing_plan_section=existing_plan_section,
        )

    def _format_skills(self, skills: Set[str]) -> str:
        """Format available skills for the prompt."""
        if not skills:
            return "synthesize (默认综合分析)"
        return ", ".join(sorted(skills))

    def _format_completed_tasks(self, ctx: PlanningContext) -> str:
        """Format completed tasks with their results for replanning.

        Args:
            ctx: Planning context.

        Returns:
            Formatted string showing task IDs and their results.
        """
        if not ctx.completed_task_ids:
            return "None"

        lines = []
        for task_id in sorted(ctx.completed_task_ids):
            result = ctx.completed_task_results.get(task_id)
            if result is not None:
                # Format result for display
                result_str = str(result)
                if len(result_str) > 300:
                    result_str = result_str[:300] + "..."
                lines.append(f"- `{task_id}`: {result_str}")
            else:
                lines.append(f"- `{task_id}`: (completed)")

        return "\n".join(lines) if lines else "None"

    def _summarize_dag(self, dag: TaskDAG) -> str:
        """Create a summary of a DAG for the LLM.

        Args:
            dag: The DAG to summarize.

        Returns:
            Human-readable summary.
        """
        lines = []
        for node in dag.nodes.values():
            deps = f" (依赖: {', '.join(node.depends_on)})" if node.depends_on else ""
            lines.append(f"- {node.id}: {node.skill} {node.params}{deps}")
        return "\n".join(lines)

    def _is_complete_plan(self, dag: TaskDAG, ctx: PlanningContext) -> bool:
        """Check if a rule DAG is complete (doesn't need LLM enhancement).

        Args:
            dag: The DAG to check.
            ctx: Planning context.

        Returns:
            True if the DAG is complete.
        """
        # A direct response is always complete
        if len(dag.nodes) == 1:
            node = list(dag.nodes.values())[0]
            if node.skill == "synthesize" and "message" in node.params:
                return True

            # Single-step tool tasks from rule planner can be treated as complete
            if ctx.planning_mode == PlanningMode.HYBRID:
                if node.skill in {"Read", "Glob", "Grep"} and not node.depends_on:
                    return True

        # If hybrid mode, default to LLM unless rule plan is trivially complete
        if ctx.planning_mode == PlanningMode.HYBRID:
            return False

        # For LLM_FULL mode, rule DAG is just a starting point
        return False

    def _parse_response(
        self,
        response: str,
        ctx: PlanningContext,
    ) -> Optional[TaskDAG]:
        """Parse LLM response into a TaskDAG.

        Args:
            response: Raw LLM response text.
            ctx: Planning context.

        Returns:
            Parsed TaskDAG or None if parsing failed.
        """
        try:
            data = self._extract_json(response)

            # Check for "continue" mode (keep existing plan)
            if data.get("mode") == "continue":
                if ctx.rule_dag:
                    return ctx.rule_dag
                return None

            # Direct response - use direct=True to skip LLM re-processing
            if data.get("mode") == "direct":
                direct_text = data.get("response", "")
                return TaskDAG.create_simple("synthesize", {"message": direct_text, "direct": True})

            # DAG mode
            tasks = []
            for task_data in data.get("tasks", []):
                task = {
                    "id": task_data.get("id", f"t{len(tasks) + 1}"),
                    "skill": task_data.get("skill", "synthesize"),
                    "params": task_data.get("params", {}),
                    "depends_on": task_data.get("depends_on", []),
                    "is_checkpoint": task_data.get("is_checkpoint", False),
                    "source": TaskSource.LLM.value,
                }
                tasks.append(task)

            if not tasks:
                return None

            # Auto-mark checkpoints
            self._auto_mark_checkpoints(tasks)

            return TaskDAG.create(ctx.goal, tasks)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            return None

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from text that may contain other content.

        Args:
            text: Text potentially containing JSON.

        Returns:
            Parsed JSON as dictionary.

        Raises:
            json.JSONDecodeError: If no valid JSON found.
        """
        text = text.strip()

        # Try direct parse
        if text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

        # Try markdown code block
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            try:
                return json.loads(code_block.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        raise json.JSONDecodeError("No JSON found", text, 0)

    def _auto_mark_checkpoints(self, tasks: List[Dict[str, Any]]) -> None:
        """Auto-mark tasks as checkpoints based on heuristics.

        Args:
            tasks: List of task dictionaries to modify in-place.
        """
        checkpoint_skills = {"search", "web_search", "rag_search"}

        # Build dependency count
        downstream_count: Dict[str, int] = {}
        for task in tasks:
            for dep_id in task.get("depends_on", []):
                downstream_count[dep_id] = downstream_count.get(dep_id, 0) + 1

        for task in tasks:
            task_id = task.get("id", "")
            skill = task.get("skill", "")

            if skill in checkpoint_skills:
                task["is_checkpoint"] = True
            elif downstream_count.get(task_id, 0) >= 2:
                task["is_checkpoint"] = True

    def _create_fallback_dag(self, ctx: PlanningContext) -> TaskDAG:
        """Create a fallback DAG when planning fails.

        Args:
            ctx: Planning context.

        Returns:
            Simple chat DAG as fallback.
        """
        return TaskDAG.create_simple("synthesize", {"message": ctx.goal})
