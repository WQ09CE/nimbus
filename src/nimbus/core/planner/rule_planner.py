"""Rule-based planning stage.

This module provides a fast, deterministic rule-based planner that
can handle common patterns without invoking the LLM.
"""

import re
import uuid
from typing import List, Dict, Any, Optional

from ..types import TaskDAG, TaskNode, TaskSource
from ..logging import get_logger
from .protocol import PlannerStage, PlanningContext, PlanningMode

logger = get_logger("planner.rule")


# Default planning rules
PLANNING_RULES: List[Dict[str, Any]] = [
    # ==========================================================================
    # Greetings and Common Phrases
    # ==========================================================================
    {
        "name": "greeting",
        "pattern": r"^(你好|hello|hi|hey|嗨|哈喽)\s*[!！。.]*\s*$",
        "mode": "direct",
        "response_template": "你好！有什么可以帮你的吗？",
    },
    {
        "name": "thanks",
        "pattern": r"^(谢谢|thanks|thank you|感谢)\s*[!！。.]*\s*$",
        "mode": "direct",
        "response_template": "不客气！还有什么可以帮你的吗？",
    },
    {
        "name": "goodbye",
        "pattern": r"^(再见|bye|goodbye|拜拜)\s*[!！。.]*\s*$",
        "mode": "direct",
        "response_template": "再见！期待下次与你交流。",
    },

    # ==========================================================================
    # File Operations (Read)
    # ==========================================================================
    # Read specific file - Chinese
    {
        "name": "read_file_cn",
        "pattern": r"^(?:读取|读|查看|打开|看看|显示)\s*(?:一下\s*)?(?:文件\s*)?([^\s]+\.[\w]+)\s*(?:文件)?(?:的)?(?:内容)?$",
        "mode": "dag",
        "tasks": [
            {"skill": "Read", "params_template": {"file_path": "$1"}},
        ],
    },
    # Read specific file - English
    {
        "name": "read_file_en",
        "pattern": r"^(?:read|show|display|open|cat|view)\s+(?:the\s+)?(?:file\s+)?([^\s]+\.[\w]+)(?:\s+file)?(?:\s+content)?$",
        "mode": "dag",
        "tasks": [
            {"skill": "Read", "params_template": {"file_path": "$1"}},
        ],
    },
    # Read file with "content of" pattern
    {
        "name": "read_file_content_of",
        "pattern": r"^(?:read\s+)?(?:the\s+)?content\s+of\s+([^\s]+)$",
        "mode": "dag",
        "tasks": [
            {"skill": "Read", "params_template": {"file_path": "$1"}},
        ],
    },

    # ==========================================================================
    # File Operations (Glob - List files)
    # ==========================================================================
    # List files in directory - Chinese
    {
        "name": "list_files_cn",
        "pattern": r"^(?:列出|列|显示|查看)\s*(?:一下\s*)?([^\s]+?)(?:目录|文件夹)?(?:下|里|中)?(?:的)?(?:所有)?(?:文件|\.py文件|Python文件)$",
        "mode": "dag",
        "tasks": [
            {"skill": "Glob", "params_template": {"pattern": "*", "path": "$1"}},
        ],
    },
    # List Python files - English
    {
        "name": "list_python_files",
        "pattern": r"^(?:list|show|find)\s+(?:the\s+)?(?:all\s+)?(?:python\s+)?files?\s+(?:in\s+)?(?:the\s+)?(?:current\s+)?(?:directory)?(.*)$",
        "mode": "dag",
        "tasks": [
            {"skill": "Glob", "params_template": {"pattern": "*.py", "path": "."}},
        ],
    },
    # List all files in directory
    {
        "name": "list_all_files",
        "pattern": r"^(?:list|ls|show)\s+(?:all\s+)?(?:files?\s+)?(?:in\s+)?([^\s]+)$",
        "mode": "dag",
        "tasks": [
            {"skill": "Glob", "params_template": {"pattern": "*", "path": "$1"}},
        ],
    },

    # ==========================================================================
    # Code Search (Grep)
    # ==========================================================================
    # Search for pattern in code - Chinese
    {
        "name": "grep_code_cn",
        "pattern": r"^(?:搜索|查找|找)\s*(?:代码中)?['\"]?(.+?)['\"]?\s*(?:的)?(?:定义)?$",
        "mode": "dag",
        "tasks": [
            {"skill": "Grep", "params_template": {"pattern": "$1", "type": "py"}},
        ],
    },
    # Search for pattern - English
    {
        "name": "grep_code_en",
        "pattern": r"^(?:search|grep|find)\s+(?:for\s+)?(?:the\s+)?(?:definition\s+of\s+)?['\"]?(.+?)['\"]?\s*(?:in\s+(?:the\s+)?(?:code(?:base)?)?)?$",
        "mode": "dag",
        "tasks": [
            {"skill": "Grep", "params_template": {"pattern": "$1", "type": "py"}},
        ],
    },

    # ==========================================================================
    # Search and Summarize
    # ==========================================================================
    {
        "name": "search",
        "pattern": r"^(?:搜索|查询|search)\s+(.+)$",
        "mode": "dag",
        "tasks": [
            {"skill": "search", "params_template": {"query": "$1"}},
        ],
    },
    {
        "name": "search_and_summarize",
        "pattern": r"^(?:搜索|查找)\s+(.+)\s*[,，]\s*(?:然后)?(?:总结|概括).*$",
        "mode": "dag",
        "tasks": [
            {"skill": "search", "params_template": {"query": "$1"}},
            {"skill": "summarize", "params_template": {"source": "$t1"}, "depends_on": ["$t1"]},
        ],
    },
    {
        "name": "summarize",
        "pattern": r"^(?:总结|概括|summarize)\s*[:：]?\s*(.+)$",
        "mode": "dag",
        "tasks": [
            {"skill": "summarize", "params_template": {"text": "$1"}},
        ],
    },
    {
        "name": "draft",
        "pattern": r"^(?:写|撰写|起草|draft|write)\s*(?:一篇|一份|一个)?\s*(.+)$",
        "mode": "dag",
        "tasks": [
            {"skill": "draft", "params_template": {"topic": "$1"}},
        ],
    },
]


class RulePlanner:
    """Rule-based planning stage.

    This stage attempts to match the user's goal against a set of
    predefined patterns. If a match is found, it creates a DAG
    directly without LLM invocation.

    Advantages:
    - Fast (no LLM call needed)
    - Deterministic (same input = same output)
    - Cost-effective

    Limitations:
    - Can only handle predefined patterns
    - No understanding of context
    - May miss nuanced requests

    Example:
        ```python
        planner = RulePlanner()
        ctx = PlanningContext(goal="你好", ...)

        ctx = await planner.process(ctx)
        if ctx.rule_dag:
            # Rule matched, DAG created
            pass
        ```
    """

    def __init__(self, rules: Optional[List[Dict[str, Any]]] = None):
        """Initialize the rule planner.

        Args:
            rules: List of rule dictionaries. Uses default rules if None.
        """
        self.rules = rules if rules is not None else PLANNING_RULES
        self._compiled_rules: List[tuple] = []
        self._compile_rules()

    def _compile_rules(self) -> None:
        """Pre-compile regex patterns for efficiency."""
        self._compiled_rules = []
        for rule in self.rules:
            try:
                pattern = re.compile(rule["pattern"], re.IGNORECASE)
                self._compiled_rules.append((pattern, rule))
            except re.error as e:
                logger.warning(f"Invalid regex in rule '{rule.get('name', 'unknown')}': {e}")

    @property
    def name(self) -> str:
        """Stage name for logging/tracing."""
        return "rule_planner"

    async def process(self, ctx: PlanningContext) -> PlanningContext:
        """Match rules and create DAG if matched.

        Args:
            ctx: The planning context.

        Returns:
            Updated planning context with rule_dag set if matched.
        """
        goal = ctx.goal.strip()
        if not goal:
            return ctx

        for pattern, rule in self._compiled_rules:
            match = pattern.match(goal)
            if match:
                logger.debug(f"Rule matched: {rule.get('name', 'unknown')}")

                dag = self._create_dag_from_rule(rule, match, ctx)
                if dag:
                    ctx.rule_dag = dag
                    ctx.metadata["matched_rule"] = rule.get("name", "unknown")

                    # If mode is direct, set as final DAG and mark for early exit
                    if rule.get("mode") == "direct":
                        ctx.final_dag = dag
                        if ctx.planning_mode == PlanningMode.RULE_ONLY:
                            ctx.early_exit = True

                    # For RULE_ONLY mode, also exit on DAG matches
                    if ctx.planning_mode == PlanningMode.RULE_ONLY:
                        ctx.final_dag = dag
                        ctx.early_exit = True

                    logger.info(f"Created rule-based DAG for '{rule.get('name')}'")
                    return ctx

        logger.debug("No rule matched")
        return ctx

    def _create_dag_from_rule(
        self,
        rule: Dict[str, Any],
        match: re.Match,
        ctx: PlanningContext,
    ) -> Optional[TaskDAG]:
        """Create a DAG from a matched rule.

        Args:
            rule: The matched rule dictionary.
            match: The regex match object.
            ctx: Planning context for skill validation.

        Returns:
            TaskDAG if creation successful, None otherwise.
        """
        mode = rule.get("mode", "direct")

        if mode == "direct":
            # Direct response - create simple chat DAG
            response = rule.get("response_template", "")
            return TaskDAG.create_simple("chat", {"message": response})

        if mode == "dag":
            # Create DAG with tasks
            tasks_template = rule.get("tasks", [])
            if not tasks_template:
                return None

            tasks = []
            task_id_map: Dict[str, str] = {}  # Map template refs to actual IDs

            for i, task_template in enumerate(tasks_template):
                task_id = f"t{i+1}"
                task_id_map[f"$t{i+1}"] = task_id

                # Process params template
                params = self._process_params(
                    task_template.get("params_template", {}),
                    match,
                    task_id_map,
                )

                # Process depends_on
                depends_on = self._process_depends_on(
                    task_template.get("depends_on", []),
                    task_id_map,
                )

                skill = task_template.get("skill", "chat")

                # Validate skill is available
                if ctx.available_skills and skill not in ctx.available_skills:
                    if skill != "chat":
                        logger.warning(f"Skill '{skill}' not available, skipping rule")
                        return None

                task = {
                    "id": task_id,
                    "skill": skill,
                    "params": params,
                    "depends_on": depends_on,
                    "source": TaskSource.RULE.value,
                    "confidence": 1.0,  # Rule-based tasks are deterministic
                }
                tasks.append(task)

            if not tasks:
                return None

            # Mark search tasks as checkpoints
            for task in tasks:
                if task["skill"] in {"search", "web_search", "rag_search"}:
                    task["is_checkpoint"] = True

            return TaskDAG.create(ctx.goal, tasks)

        return None

    def _process_params(
        self,
        params_template: Dict[str, Any],
        match: re.Match,
        task_id_map: Dict[str, str],
    ) -> Dict[str, Any]:
        """Process parameter template with match groups.

        Args:
            params_template: Template dict with placeholders like "$1", "$2".
            match: Regex match object.
            task_id_map: Map of "$t1" style refs to actual task IDs.

        Returns:
            Processed parameters dictionary.
        """
        params = {}
        for key, value in params_template.items():
            if isinstance(value, str):
                # Replace match group references ($1, $2, etc.)
                if value.startswith("$") and value[1:].isdigit():
                    group_num = int(value[1:])
                    try:
                        params[key] = match.group(group_num)
                    except IndexError:
                        params[key] = value
                # Replace task ID references ($t1, $t2, etc.)
                elif value in task_id_map:
                    params[key] = task_id_map[value]
                else:
                    params[key] = value
            else:
                params[key] = value
        return params

    def _process_depends_on(
        self,
        depends_template: List[str],
        task_id_map: Dict[str, str],
    ) -> List[str]:
        """Process depends_on template.

        Args:
            depends_template: List of template refs like "$t1".
            task_id_map: Map of "$t1" style refs to actual task IDs.

        Returns:
            List of actual task IDs.
        """
        result = []
        for ref in depends_template:
            if ref in task_id_map:
                result.append(task_id_map[ref])
            else:
                result.append(ref)
        return result

    def add_rule(self, rule: Dict[str, Any]) -> None:
        """Add a new rule to the planner.

        Args:
            rule: Rule dictionary with pattern, mode, and response/tasks.
        """
        self.rules.append(rule)
        try:
            pattern = re.compile(rule["pattern"], re.IGNORECASE)
            self._compiled_rules.append((pattern, rule))
        except re.error as e:
            logger.warning(f"Invalid regex in added rule: {e}")

    def remove_rule(self, name: str) -> bool:
        """Remove a rule by name.

        Args:
            name: Name of the rule to remove.

        Returns:
            True if rule was found and removed.
        """
        original_count = len(self.rules)
        self.rules = [r for r in self.rules if r.get("name") != name]
        self._compile_rules()
        return len(self.rules) < original_count
