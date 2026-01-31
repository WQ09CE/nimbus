"""Rule-based planning stage.

This module provides a fast, deterministic rule-based planner that
can handle common patterns without invoking the LLM.
"""

import re
import uuid
from typing import List, Dict, Any, Optional

from ..types import TaskDAG, TaskNode, TaskSource, RetryLoopConfig
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
        "pattern": r"^(?:列出|列|显示|查看)\s*(?:一下\s*)?([^\s]+?)\s*(?:目录|文件夹)?(?:下|里|中)?(?:的)?(?:所有)?(?:文件|\.py文件|Python文件)$",
        "mode": "dag",
        "tasks": [
            {"skill": "Glob", "params_template": {"pattern": "*", "path": "$1"}},
        ],
    },
    # List Python files - English
    {
        "name": "list_python_files",
        "pattern": r"^(?:list|show|find)\s+(?:the\s+)?(?:all\s+)?python\s+files?\s+(?:in\s+)?(?:the\s+)?(?:current\s+)?(?:directory)?(.*)$",
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
    # List files in current directory (generic)
    {
        "name": "list_files_current_dir",
        "pattern": r"^(?:list|ls|show)\s+(?:the\s+)?(?:all\s+)?files?\s+(?:in\s+)?(?:the\s+)?(?:current\s+)?(?:directory|folder|dir)$",
        "mode": "dag",
        "tasks": [
            {"skill": "Glob", "params_template": {"pattern": "*", "path": "."}},
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
    # Search for pattern - English (only match simple patterns, not natural language queries)
    # e.g., "search for foo", "grep bar", "find baz", "find 'async def'" but NOT "find all files that contain..."
    {
        "name": "grep_code_en",
        "pattern": r"^(?:search|grep|find)\s+(?:for\s+)?(?:the\s+)?(?:definition\s+of\s+)?['\"]([^'\"]+)['\"](?:\s+in\s+(?:the\s+)?(?:code(?:base)?)?)?$",
        "mode": "dag",
        "tasks": [
            {"skill": "Grep", "params_template": {"pattern": "$1", "type": "py"}},
        ],
    },
    # Search for simple word patterns without quotes
    # e.g., "search for foo", "find bar", "grep baz"
    {
        "name": "grep_code_en_simple",
        "pattern": r"^(?:search|grep|find)\s+(?:for\s+)?(?:the\s+)?(?:definition\s+of\s+)?(\w+(?:\.\w+)?)\s*(?:in\s+(?:the\s+)?(?:code(?:base)?)?)?$",
        "mode": "dag",
        "tasks": [
            {"skill": "Grep", "params_template": {"pattern": "$1", "type": "py"}},
        ],
    },
    # ==========================================================================
    # Bash Command Execution
    # ==========================================================================
    # Run/Execute command - English
    {
        "name": "bash_run_command_en",
        "pattern": r"^(?:run|execute|exec)\s+(?:the\s+)?(?:command[:\s]+)?(.+)$",
        "mode": "dag",
        "tasks": [
            {"skill": "Bash", "params_template": {"command": "$1"}},
        ],
    },
    # Run/Execute command - Chinese
    {
        "name": "bash_run_command_cn",
        "pattern": r"^(?:运行|执行|跑)\s*(?:命令)?[:\s：]*(.+)$",
        "mode": "dag",
        "tasks": [
            {"skill": "Bash", "params_template": {"command": "$1"}},
        ],
    },
    # Echo command explicit
    {
        "name": "bash_echo",
        "pattern": r"^.*(?:echo)\s+['\"]?(.+?)['\"]?\s*$",
        "mode": "dag",
        "tasks": [
            {"skill": "Bash", "params_template": {"command": "echo '$1'"}},
        ],
    },
    # ==========================================================================
    # Code Summarization (Read + Summarize)
    # ==========================================================================
    # Summarize file - English
    {
        "name": "summarize_file_en",
        "pattern": r"^(?:read\s+)?([^\s]+\.(?:py|js|ts|go|rs|java|c|cpp|h|hpp|rb|php|swift|kt))\s+(?:and\s+)?(?:summarize|summarise|explain|describe)\s*(?:its?\s+)?(?:main\s+)?(?:purpose|content|functionality)?.*$",
        "mode": "dag",
        "tasks": [
            {"skill": "Read", "params_template": {"file_path": "$1"}},
        ],
    },
    # Summarize file - pattern 2 (summarize first)
    {
        "name": "summarize_file_en2",
        "pattern": r"^(?:summarize|summarise|explain|describe)\s+(?:the\s+)?(?:file\s+)?([^\s]+\.(?:py|js|ts|go|rs|java|c|cpp|h|hpp|rb|php|swift|kt)).*$",
        "mode": "dag",
        "tasks": [
            {"skill": "Read", "params_template": {"file_path": "$1"}},
        ],
    },
    # Summarize file - Chinese
    {
        "name": "summarize_file_cn",
        "pattern": r"^(?:读取)?(?:并)?(?:总结|概括|解释|描述)\s*(?:一下\s*)?(?:文件\s*)?([^\s]+\.(?:py|js|ts|go|rs|java|c|cpp|h|hpp|rb|php|swift|kt)).*$",
        "mode": "dag",
        "tasks": [
            {"skill": "Read", "params_template": {"file_path": "$1"}},
        ],
    },
    # ==========================================================================
    # Read and Understand (Read file first)
    # ==========================================================================
    # Read and tell/understand - English
    {
        "name": "read_and_understand_en",
        "pattern": r"^(?:read|open)\s+(?:the\s+)?(?:file\s+)?([^\s]+\.[\w]+)\s+(?:and\s+)?(?:tell\s+me|explain|describe|understand|analyze)\s+(?:what|how|its?).*$",
        "mode": "dag",
        "tasks": [
            {"skill": "Read", "params_template": {"file_path": "$1"}},
        ],
    },
    # What function/class does file contain
    {
        "name": "read_and_identify_en",
        "pattern": r"^(?:read\s+)?(?:the\s+)?(?:file\s+)?([^\s]+\.[\w]+)\s+(?:and\s+)?(?:tell\s+me|identify|list|show)\s+(?:what|which)\s+(?:function|class|method|variable)s?\s+(?:it\s+)?(?:contains?|has|defines?).*$",
        "mode": "dag",
        "tasks": [
            {"skill": "Read", "params_template": {"file_path": "$1"}},
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
            {"skill": "Grep", "params_template": {"pattern": "$1", "type": "py"}},
        ],
    },
    {
        "name": "search_and_summarize",
        "pattern": r"^(?:搜索|查找)\s+(.+)\s*[,，]\s*(?:然后)?(?:总结|概括).*$",
        "mode": "dag",
        "tasks": [
            {"skill": "Grep", "params_template": {"pattern": "$1", "type": "py"}},
            {"skill": "summarize", "params_template": {"source": "$t1"}, "depends_on": ["$t1"]},
        ],
    },
    {
        "name": "summarize",
        # Only match when explicit long content is provided after colon
        # e.g., "总结: <long text here>"
        # Don't match vague references like "总结一下这个" or "总结它"
        "pattern": r"^(?:总结|概括|summarize)\s*[:：]\s*(.{50,})$",
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
    # ==========================================================================
    # Code Edit Delegation to Subagent (ADR: Single-pass planning limitation)
    # ==========================================================================
    # When code editing is required, delegate to Coder subagent which can
    # iteratively Read -> Edit without needing to know file content upfront.
    # This solves the "Edit requires old_string" problem in single-pass planning.

    # Add error handling - English (pattern 1: "add error handling to file.py")
    {
        "name": "code_edit_add_error_handling",
        "pattern": r"^(?:add|implement)\s+error\s+handling\s+(?:to|for|in)\s+(?:the\s+)?(?:file\s+)?([^\s]+\.py).*$",
        "mode": "dag",
        "tasks": [
            {
                "skill": "Subagent",
                "params_template": {
                    "prompt": "Add appropriate error handling to file $1. Read the file first to understand the code, then add try-except blocks or input validation as needed. Return a summary of changes made.",
                    "subagent_type": "coder",
                    "description": "Add error handling",
                },
            },
        ],
    },
    # Edit file to add error handling - English (pattern 2: "edit file.py to add error handling")
    {
        "name": "code_edit_file_add_error_handling",
        "pattern": r"^(?:edit|modify|update)\s+(?:the\s+)?(?:file\s+)?([^\s]+\.py)\s+(?:to\s+)?(?:add|implement)\s+error\s+handling.*$",
        "mode": "dag",
        "tasks": [
            {
                "skill": "Subagent",
                "params_template": {
                    "prompt": "Add error handling to file $1. Read the file first to understand the code, then add try-except blocks or input validation as needed. Return a summary of changes made.",
                    "subagent_type": "coder",
                    "description": "Add error handling",
                },
            },
        ],
    },
    # Add error handling - Chinese
    {
        "name": "code_edit_add_error_handling_cn",
        "pattern": r"^(?:给|为|对)\s*(?:文件\s*)?([^\s]+\.py)\s*(?:添加|增加|加上)\s*(?:错误处理|异常处理).*$",
        "mode": "dag",
        "tasks": [
            {
                "skill": "Subagent",
                "params_template": {
                    "prompt": "给文件 $1 添加适当的错误处理。先读取文件理解代码，然后添加 try-except 或输入验证。返回修改摘要。",
                    "subagent_type": "coder",
                    "description": "添加错误处理",
                },
            },
        ],
    },
    # Generic code modification - English (add X to file)
    {
        "name": "code_edit_add_generic",
        "pattern": r"^(?:add|implement|insert)\s+(.+?)\s+(?:to|into|in)\s+(?:the\s+)?(?:file\s+)?([^\s]+\.(?:py|js|ts))(?:\s+file)?$",
        "mode": "dag",
        "tasks": [
            {
                "skill": "Subagent",
                "params_template": {
                    "prompt": "Add $1 to file $2. Read the file first to understand the code structure, then make the appropriate modifications. Return a summary of changes made.",
                    "subagent_type": "coder",
                    "description": "Code modification",
                },
            },
        ],
    },
    # Generic code modification - Chinese
    {
        "name": "code_edit_add_generic_cn",
        "pattern": r"^(?:给|为|对)\s*(?:文件\s*)?([^\s]+\.(?:py|js|ts))\s*(?:添加|增加|加上)\s*(.+)$",
        "mode": "dag",
        "tasks": [
            {
                "skill": "Subagent",
                "params_template": {
                    "prompt": "给文件 $1 添加 $2。先读取文件理解代码结构，然后进行适当修改。返回修改摘要。",
                    "subagent_type": "coder",
                    "description": "代码修改",
                },
            },
        ],
    },
    # Fix code issue - English
    {
        "name": "code_edit_fix",
        "pattern": r"^(?:fix|repair|correct)\s+(?:the\s+)?(.+?)\s+(?:bug\s+)?(?:in|of)\s+(?:the\s+)?(?:file\s+)?([^\s]+\.(?:py|js|ts)).*$",
        "mode": "dag",
        "tasks": [
            {
                "skill": "Subagent",
                "params_template": {
                    "prompt": "Fix $1 in file $2. Read the file first to understand the issue, then make the appropriate fix. Return a summary of changes made.",
                    "subagent_type": "coder",
                    "description": "Fix code",
                },
            },
        ],
    },
    # Refactor code - English
    {
        "name": "code_edit_refactor",
        "pattern": r"^(?:refactor|improve|optimize)\s+(?:the\s+)?(?:code\s+)?(?:in\s+)?(?:file\s+)?([^\s]+\.(?:py|js|ts)).*$",
        "mode": "dag",
        "tasks": [
            {
                "skill": "Subagent",
                "params_template": {
                    "prompt": "Refactor the code in file $1. Read the file first to understand the current structure, then improve code quality, readability, or performance. Return a summary of changes made.",
                    "subagent_type": "coder",
                    "description": "Refactor code",
                },
            },
        ],
    },
    # ==========================================================================
    # Auto Test Writer Pattern (ADR-007: Conditional branching and retry loop)
    # ==========================================================================
    {
        "name": "auto_test_writer",
        "pattern": r"^(?:为|给|对)\s*(.+\.py)\s*(?:写|编写|生成|创建)\s*(?:单元)?测试.*$",
        "mode": "dag",
        "tasks": [
            {
                "id": "t1_read",
                "skill": "Read",
                "params_template": {"file_path": "$1"},
                "description": "Read source code",
            },
            {
                "id": "t2_gen",
                "skill": "synthesize",
                "params_template": {
                    "prompt": "Generate unit tests for the following code",
                    "context": "$t1_read",
                },
                "depends_on": ["$t1_read"],
                "description": "Generate test code",
            },
            {
                "id": "t3_verify",
                "skill": "Bash",
                "params_template": {"command": "python -m pytest tests/ -x --tb=short"},
                "depends_on": ["$t2_gen"],
                "on_failure": "t4_fix",
                "max_retries": 3,
                "is_checkpoint": True,
                "description": "Run tests to verify",
            },
            {
                "id": "t4_fix",
                "skill": "synthesize",
                "params_template": {
                    "prompt": "Fix the test errors: $error",
                    "context": "$t3_verify",
                },
                "retry_target": "t3_verify",
                "inactive": True,
                "description": "Fix test errors",
            },
        ],
        "retry_loops": [
            {
                "verify_task": "t3_verify",
                "fix_task": "t4_fix",
                "max_attempts": 3,
            }
        ],
    },
    # ==========================================================================
    # Code Fix with Retry Pattern (ADR-007)
    # ==========================================================================
    {
        "name": "code_fix_with_retry",
        "pattern": r"^(?:修复|fix)\s+(.+?)\s+(?:并|and)\s*(?:验证|verify|测试|test).*$",
        "mode": "dag",
        "tasks": [
            {
                "id": "t1_fix",
                "skill": "synthesize",
                "params_template": {"prompt": "Fix the code in file: $1"},
            },
            {
                "id": "t2_verify",
                "skill": "Bash",
                "params_template": {"command": "python -m pytest tests/ -x"},
                "depends_on": ["$t1_fix"],
                "on_failure": "t3_refix",
                "max_retries": 2,
            },
            {
                "id": "t3_refix",
                "skill": "synthesize",
                "params_template": {
                    "prompt": "Fix the remaining errors: $error",
                    "file_path": "$1",
                },
                "retry_target": "t2_verify",
                "inactive": True,
            },
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
            return TaskDAG.create_simple("synthesize", {"message": response, "direct": True})

        if mode == "dag":
            # Create DAG with tasks
            tasks_template = rule.get("tasks", [])
            if not tasks_template:
                return None

            tasks = []
            task_id_map: Dict[str, str] = {}  # Map template refs to actual IDs

            # First pass: collect task IDs
            for i, task_template in enumerate(tasks_template):
                # Use explicit id if provided, otherwise generate
                explicit_id = task_template.get("id")
                if explicit_id:
                    task_id = explicit_id
                    task_id_map[f"${explicit_id}"] = task_id
                else:
                    task_id = f"t{i + 1}"
                task_id_map[f"$t{i + 1}"] = task_id

            # Second pass: create tasks
            for i, task_template in enumerate(tasks_template):
                # Use explicit id if provided, otherwise generate
                explicit_id = task_template.get("id")
                task_id = explicit_id if explicit_id else f"t{i + 1}"

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

                skill = task_template.get("skill", "synthesize")

                # Validate skill is available
                if ctx.available_skills and skill not in ctx.available_skills:
                    if skill != "synthesize":
                        logger.warning(f"Skill '{skill}' not available, skipping rule")
                        return None

                # NEW: Process failure handling fields (ADR-007)
                on_failure = task_template.get("on_failure")
                if on_failure and f"${on_failure}" in task_id_map:
                    on_failure = task_id_map[f"${on_failure}"]

                retry_target = task_template.get("retry_target")
                if retry_target and f"${retry_target}" in task_id_map:
                    retry_target = task_id_map[f"${retry_target}"]

                task = {
                    "id": task_id,
                    "skill": skill,
                    "params": params,
                    "depends_on": depends_on,
                    "source": TaskSource.RULE.value,
                    "confidence": 1.0,  # Rule-based tasks are deterministic
                    # NEW: Failure handling fields (ADR-007)
                    "on_failure": on_failure,
                    "retry_target": retry_target,
                    "max_retries": task_template.get("max_retries", 0),
                    "inactive": task_template.get("inactive", False),
                }

                # Handle is_checkpoint if specified in template
                if task_template.get("is_checkpoint"):
                    task["is_checkpoint"] = True

                tasks.append(task)

            if not tasks:
                return None

            # Mark search tasks as checkpoints
            for task in tasks:
                if task["skill"] in {"search", "web_search", "rag_search"}:
                    task["is_checkpoint"] = True

            dag = TaskDAG.create(ctx.goal, tasks)

            # NEW: Process retry loops (ADR-007)
            retry_loops_template = rule.get("retry_loops", [])
            for loop_config in retry_loops_template:
                verify_id = loop_config.get("verify_task")
                fix_id = loop_config.get("fix_task")

                # Map template refs to actual IDs
                if f"${verify_id}" in task_id_map:
                    verify_id = task_id_map[f"${verify_id}"]
                if f"${fix_id}" in task_id_map:
                    fix_id = task_id_map[f"${fix_id}"]

                dag.retry_loops.append(RetryLoopConfig(
                    verify_task=verify_id,
                    fix_task=fix_id,
                    max_attempts=loop_config.get("max_attempts", 3),
                    backoff_seconds=loop_config.get("backoff_seconds", 0.0),
                ))

            return dag

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
                # Replace all match group references ($1, $2, etc.) in the string
                processed = value
                for i in range(1, 10):  # Support $1 through $9
                    placeholder = f"${i}"
                    if placeholder in processed:
                        try:
                            replacement = match.group(i)
                            if replacement:
                                processed = processed.replace(placeholder, replacement)
                        except IndexError:
                            pass  # Keep placeholder if group doesn't exist

                # Replace task ID references ($t1, $t2, etc.)
                for ref, actual_id in task_id_map.items():
                    if ref in processed:
                        processed = processed.replace(ref, actual_id)

                params[key] = processed
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
