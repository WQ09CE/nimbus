#!/usr/bin/env python3
"""
AgentOS CLI Test Framework - 情境模拟练兵场

直接通过 CLI 调用 AgentOS 测试各种功能场景。
包含丰富的情境模拟，测试代码理解、重构、Bug修复等能力。

Usage:
    # 运行所有测试
    python tests/test_agentos_cli.py

    # 运行基础测试
    python tests/test_agentos_cli.py --test read_file glob_files

    # 运行情境模拟测试
    python tests/test_agentos_cli.py --scenario

    # 运行特定情境
    python tests/test_agentos_cli.py --scenario --test scenario_bug_fix

    # 交互模式
    python tests/test_agentos_cli.py --interactive

    # 显示详细日志
    python tests/test_agentos_cli.py --verbose
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


console = Console()


# =============================================================================
# Scenario Sandbox - 情境模拟练兵场
# =============================================================================

SANDBOX_DIR = Path(__file__).parent / "sandbox_playground"


def setup_sandbox() -> Path:
    """设置练兵场环境，创建测试文件。"""
    sandbox = SANDBOX_DIR
    sandbox.mkdir(exist_ok=True)

    # 创建各种测试场景文件
    _create_buggy_code(sandbox)
    _create_refactor_target(sandbox)
    _create_test_project(sandbox)
    _create_documentation_target(sandbox)

    return sandbox


def cleanup_sandbox():
    """清理练兵场。"""
    import shutil
    if SANDBOX_DIR.exists():
        shutil.rmtree(SANDBOX_DIR)


def _create_buggy_code(sandbox: Path):
    """创建有 Bug 的代码文件。"""
    buggy_dir = sandbox / "buggy_code"
    buggy_dir.mkdir(exist_ok=True)

    # Bug 1: Off-by-one error
    (buggy_dir / "off_by_one.py").write_text('''"""Calculator with a bug."""

def sum_range(start: int, end: int) -> int:
    """Calculate sum of numbers from start to end (inclusive).

    Example: sum_range(1, 3) should return 1 + 2 + 3 = 6
    """
    total = 0
    for i in range(start, end):  # BUG: should be range(start, end + 1)
        total += i
    return total


def test_sum_range():
    assert sum_range(1, 3) == 6  # This will fail!
    assert sum_range(0, 5) == 15  # This will also fail!
''')

    # Bug 2: Type error
    (buggy_dir / "type_error.py").write_text('''"""String processor with type issues."""

def process_items(items):
    """Process a list of items and return their string representation.

    Args:
        items: A list of items to process

    Returns:
        Processed string with all items joined by comma
    """
    result = ""
    for item in items:
        result += item  # BUG: doesn't handle non-string items
    return result


def get_user_info(user_dict):
    """Get formatted user info.

    Args:
        user_dict: Dictionary with 'name' and 'age' keys

    Returns:
        Formatted string "Name: X, Age: Y"
    """
    # BUG: doesn't handle missing keys
    return f"Name: {user_dict['name']}, Age: {user_dict['age']}"
''')

    # Bug 3: Logic error
    (buggy_dir / "logic_error.py").write_text('''"""Sorting utility with logic issues."""

def find_max(numbers: list) -> int:
    """Find the maximum number in a list.

    Args:
        numbers: Non-empty list of numbers

    Returns:
        The maximum number
    """
    if not numbers:
        return None

    max_val = 0  # BUG: should initialize with numbers[0] or float('-inf')
    for num in numbers:
        if num > max_val:
            max_val = num
    return max_val


def is_sorted(arr: list) -> bool:
    """Check if array is sorted in ascending order."""
    for i in range(len(arr)):  # BUG: should be range(len(arr) - 1)
        if arr[i] > arr[i + 1]:  # Will cause IndexError
            return False
    return True
''')


def _create_refactor_target(sandbox: Path):
    """创建需要重构的代码。"""
    refactor_dir = sandbox / "refactor_target"
    refactor_dir.mkdir(exist_ok=True)

    # 代码重复
    (refactor_dir / "duplicated_code.py").write_text('''"""User management with code duplication."""

class UserManager:
    def __init__(self):
        self.users = {}

    def add_admin(self, user_id, name, email):
        """Add an admin user."""
        if user_id in self.users:
            print(f"User {user_id} already exists")
            return False
        if not name or not email:
            print("Name and email are required")
            return False
        if "@" not in email:
            print("Invalid email format")
            return False
        self.users[user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "role": "admin",
            "created_at": "2024-01-01"
        }
        print(f"Admin {name} added successfully")
        return True

    def add_member(self, user_id, name, email):
        """Add a member user."""
        if user_id in self.users:
            print(f"User {user_id} already exists")
            return False
        if not name or not email:
            print("Name and email are required")
            return False
        if "@" not in email:
            print("Invalid email format")
            return False
        self.users[user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "role": "member",
            "created_at": "2024-01-01"
        }
        print(f"Member {name} added successfully")
        return True

    def add_guest(self, user_id, name, email):
        """Add a guest user."""
        if user_id in self.users:
            print(f"User {user_id} already exists")
            return False
        if not name or not email:
            print("Name and email are required")
            return False
        if "@" not in email:
            print("Invalid email format")
            return False
        self.users[user_id] = {
            "id": user_id,
            "name": name,
            "email": email,
            "role": "guest",
            "created_at": "2024-01-01"
        }
        print(f"Guest {name} added successfully")
        return True
''')

    # 长函数
    (refactor_dir / "long_function.py").write_text('''"""Data processor with overly long function."""

def process_data(data, options=None):
    """Process data with various transformations.

    This function does too many things and should be split up.
    """
    options = options or {}
    result = []

    # Step 1: Validate data
    if data is None:
        return {"error": "Data is None"}
    if not isinstance(data, list):
        return {"error": "Data must be a list"}
    if len(data) == 0:
        return {"error": "Data is empty"}

    # Step 2: Filter data
    filtered = []
    min_val = options.get("min_value", 0)
    max_val = options.get("max_value", 100)
    for item in data:
        if isinstance(item, (int, float)):
            if min_val <= item <= max_val:
                filtered.append(item)

    # Step 3: Transform data
    transformed = []
    scale = options.get("scale", 1)
    offset = options.get("offset", 0)
    for item in filtered:
        new_val = item * scale + offset
        transformed.append(new_val)

    # Step 4: Aggregate data
    if not transformed:
        return {"error": "No valid data after filtering"}
    total = sum(transformed)
    count = len(transformed)
    average = total / count
    minimum = min(transformed)
    maximum = max(transformed)

    # Step 5: Format output
    result = {
        "count": count,
        "sum": total,
        "average": average,
        "min": minimum,
        "max": maximum,
        "data": transformed
    }

    return result
''')


def _create_test_project(sandbox: Path):
    """创建一个迷你测试项目。"""
    project_dir = sandbox / "mini_project"
    project_dir.mkdir(exist_ok=True)

    # 主模块
    (project_dir / "__init__.py").write_text('"""Mini project for testing."""\n')

    (project_dir / "models.py").write_text('''"""Data models."""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Task:
    """A task item."""
    id: str
    title: str
    description: str = ""
    completed: bool = False
    priority: int = 0
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class Project:
    """A project containing tasks."""
    id: str
    name: str
    tasks: List[Task] = None

    def __post_init__(self):
        if self.tasks is None:
            self.tasks = []

    def add_task(self, task: Task):
        self.tasks.append(task)

    def get_completed_tasks(self) -> List[Task]:
        return [t for t in self.tasks if t.completed]

    def get_pending_tasks(self) -> List[Task]:
        return [t for t in self.tasks if not t.completed]
''')

    (project_dir / "service.py").write_text('''"""Business logic service."""
from typing import Dict, List, Optional
from .models import Task, Project


class TaskService:
    """Service for managing tasks."""

    def __init__(self):
        self.projects: Dict[str, Project] = {}

    def create_project(self, project_id: str, name: str) -> Project:
        """Create a new project."""
        project = Project(id=project_id, name=name)
        self.projects[project_id] = project
        return project

    def add_task_to_project(
        self,
        project_id: str,
        task_id: str,
        title: str,
        description: str = "",
        priority: int = 0,
    ) -> Optional[Task]:
        """Add a task to a project."""
        project = self.projects.get(project_id)
        if not project:
            return None

        task = Task(
            id=task_id,
            title=title,
            description=description,
            priority=priority,
        )
        project.add_task(task)
        return task

    def complete_task(self, project_id: str, task_id: str) -> bool:
        """Mark a task as completed."""
        project = self.projects.get(project_id)
        if not project:
            return False

        for task in project.tasks:
            if task.id == task_id:
                task.completed = True
                return True
        return False

    def get_all_tasks(self, project_id: str) -> List[Task]:
        """Get all tasks in a project."""
        project = self.projects.get(project_id)
        return project.tasks if project else []
''')

    (project_dir / "utils.py").write_text('''"""Utility functions."""
import re
from typing import List


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text


def truncate(text: str, max_length: int = 50, suffix: str = "...") -> str:
    """Truncate text to max length."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def parse_tags(tag_string: str) -> List[str]:
    """Parse comma-separated tags."""
    if not tag_string:
        return []
    tags = [t.strip() for t in tag_string.split(",")]
    return [t for t in tags if t]  # Remove empty tags
''')


def _create_documentation_target(sandbox: Path):
    """创建需要写文档的代码。"""
    doc_dir = sandbox / "needs_docs"
    doc_dir.mkdir(exist_ok=True)

    (doc_dir / "api.py").write_text('''"""API module - needs documentation."""

class APIClient:
    def __init__(self, base_url, api_key=None, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = None

    def get(self, endpoint, params=None):
        url = f"{self.base_url}/{endpoint}"
        # Implementation would go here
        pass

    def post(self, endpoint, data=None, json=None):
        url = f"{self.base_url}/{endpoint}"
        # Implementation would go here
        pass

    def delete(self, endpoint):
        url = f"{self.base_url}/{endpoint}"
        # Implementation would go here
        pass


def retry(max_attempts=3, delay=1.0, exceptions=(Exception,)):
    def decorator(func):
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        import time
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator
''')


@dataclass
class TestResult:
    """Test result."""
    name: str
    passed: bool
    message: str
    duration_ms: float
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class AgentOSTestFramework:
    """AgentOS CLI Test Framework."""

    def __init__(
        self,
        workspace: Path,
        verbose: bool = False,
        model: str = "gemini-3-flash-preview",
    ):
        self.workspace = workspace
        self.verbose = verbose
        self.model = model
        self.agent_os = None
        self.llm = None
        self.results: List[TestResult] = []

    async def setup(self) -> bool:
        """Initialize AgentOS."""
        console.print("[bold cyan]Setting up AgentOS...[/bold cyan]")

        # Determine which LLM client to use based on model name
        config_path = Path.home() / ".nimbus" / "config.json"
        config = {}
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)

        # Check if using OpenRouter model (contains /)
        if "/" in self.model:
            # OpenRouter model (e.g., anthropic/claude-opus-4)
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                api_key = config.get("llm", {}).get("providers", {}).get("openrouter", {}).get("api_key")

            if not api_key:
                console.print("[red]Error: No OpenRouter API key found[/red]")
                console.print("Set OPENROUTER_API_KEY env var or add to ~/.nimbus/config.json")
                return False

            try:
                from nimbus.v2.agentos import create_agent_os
                from nimbus.v2.llm import OpenRouterV2Client

                console.print(f"[dim]Using OpenRouter with model: {self.model}[/dim]")
                self.llm = OpenRouterV2Client(api_key=api_key, model=self.model)
            except Exception as e:
                console.print(f"[red]Failed to create OpenRouter client: {e}[/red]")
                if self.verbose:
                    traceback.print_exc()
                return False
        else:
            # Gemini model
            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                api_key = config.get("llm", {}).get("providers", {}).get("gemini", {}).get("api_key")

            if not api_key:
                console.print("[red]Error: No Gemini API key found[/red]")
                console.print("Set GEMINI_API_KEY env var or add to ~/.nimbus/config.json")
                return False

            try:
                from nimbus.v2.agentos import create_agent_os
                from nimbus.v2.llm import GeminiV2Client

                console.print(f"[dim]Using Gemini with model: {self.model}[/dim]")
                self.llm = GeminiV2Client(api_key=api_key, model=self.model)
            except Exception as e:
                console.print(f"[red]Failed to create Gemini client: {e}[/red]")
                if self.verbose:
                    traceback.print_exc()
                return False

        try:
            from nimbus.v2.agentos import create_agent_os

            self.agent_os = create_agent_os(
                llm_client=self.llm,
                workspace=self.workspace,
                register_defaults=True,
                system_rules="""You are a code assistant with access to tools.

CRITICAL RULES:
1. You MUST use the function calling API to invoke tools. NEVER simulate tool calls in text.
2. When you need to read a file, call the Read function directly.
3. When you need to search files, call Glob or Grep functions.
4. When you have completed the task, call the return_result function with the final answer.
5. Be concise and direct.""",
            )

            console.print(f"[green]AgentOS initialized with {len(self.agent_os.list_tools())} tools[/green]")
            console.print(f"[dim]Tools: {', '.join(self.agent_os.list_tools())}[/dim]")
            return True

        except Exception as e:
            console.print(f"[red]Setup failed: {e}[/red]")
            if self.verbose:
                traceback.print_exc()
            return False

    async def cleanup(self):
        """Cleanup resources."""
        if self.llm:
            await self.llm.close()

    async def chat(self, message: str, session_id: str = None) -> Dict[str, Any]:
        """Send a chat message and get response."""
        start_time = datetime.now()

        if self.verbose:
            console.print(f"\n[dim]>>> Sending: {message[:100]}...[/dim]")

        try:
            result = await self.agent_os.chat(message, session_id=session_id)
            duration = (datetime.now() - start_time).total_seconds() * 1000

            response = {
                "status": result.status,
                "output": result.output if result.status == "OK" else None,
                "error": result.fault.message if result.fault else None,
                "duration_ms": duration,
                "session_id": self.agent_os._current_session_id,
            }

            if self.verbose:
                if result.status == "OK":
                    output_preview = str(result.output)[:200] if result.output else "(empty)"
                    console.print(f"[dim]<<< Response ({duration:.0f}ms): {output_preview}...[/dim]")
                else:
                    console.print(f"[dim]<<< Error: {result.fault.message if result.fault else 'Unknown'}[/dim]")

            return response

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            if self.verbose:
                console.print(f"[red]<<< Exception: {e}[/red]")
                traceback.print_exc()
            return {
                "status": "EXCEPTION",
                "output": None,
                "error": str(e),
                "duration_ms": duration,
                "session_id": None,
            }

    async def run_test(
        self,
        name: str,
        description: str,
        messages: List[str],
        validators: List[Callable[[Dict], bool]] = None,
    ) -> TestResult:
        """Run a single test case."""
        console.print(f"\n[bold]Test: {name}[/bold]")
        console.print(f"[dim]{description}[/dim]")

        start_time = datetime.now()
        session_id = None
        all_responses = []

        try:
            for i, msg in enumerate(messages):
                console.print(f"  [cyan]Step {i+1}:[/cyan] {msg[:80]}...")
                response = await self.chat(msg, session_id=session_id)
                all_responses.append(response)
                session_id = response.get("session_id")

                if response["status"] != "OK":
                    duration = (datetime.now() - start_time).total_seconds() * 1000
                    result = TestResult(
                        name=name,
                        passed=False,
                        message=f"Step {i+1} failed: {response.get('error', 'Unknown error')}",
                        duration_ms=duration,
                        details={"responses": all_responses},
                    )
                    self._print_result(result)
                    return result

                # Print output preview
                output = response.get("output", "")
                if output:
                    preview = str(output)[:150].replace("\n", " ")
                    console.print(f"    [green]OK[/green]: {preview}...")

            # Run validators
            if validators:
                for validator in validators:
                    if not validator(all_responses[-1]):
                        duration = (datetime.now() - start_time).total_seconds() * 1000
                        result = TestResult(
                            name=name,
                            passed=False,
                            message="Validation failed",
                            duration_ms=duration,
                            details={"responses": all_responses},
                        )
                        self._print_result(result)
                        return result

            duration = (datetime.now() - start_time).total_seconds() * 1000
            result = TestResult(
                name=name,
                passed=True,
                message="All steps passed",
                duration_ms=duration,
                details={"responses": all_responses},
            )
            self._print_result(result)
            return result

        except Exception as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration,
                details={"exception": str(e), "traceback": traceback.format_exc()},
            )
            self._print_result(result)
            return result

    def _print_result(self, result: TestResult):
        """Print test result."""
        if result.passed:
            console.print(f"  [bold green]PASSED[/bold green] ({result.duration_ms:.0f}ms)")
        else:
            console.print(f"  [bold red]FAILED[/bold red] ({result.duration_ms:.0f}ms)")
            console.print(f"    [red]{result.message}[/red]")

    # =========================================================================
    # Test Cases
    # =========================================================================

    async def test_simple_chat(self) -> TestResult:
        """Test simple chat without tools."""
        return await self.run_test(
            name="simple_chat",
            description="Test basic chat response",
            messages=["What is 2 + 2? Just answer with the number."],
            validators=[lambda r: r["status"] == "OK" and r["output"] is not None],
        )

    async def test_read_file(self) -> TestResult:
        """Test reading a file."""
        test_file = self.workspace / "pyproject.toml"
        return await self.run_test(
            name="read_file",
            description=f"Test reading {test_file.name}",
            messages=[f"Read the file {test_file} and tell me the project name."],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def test_glob_files(self) -> TestResult:
        """Test globbing files."""
        return await self.run_test(
            name="glob_files",
            description="Test finding Python files",
            messages=["Find all Python files in src/nimbus/v2/tools/ directory. Just list the file names."],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def test_grep_content(self) -> TestResult:
        """Test grepping content."""
        return await self.run_test(
            name="grep_content",
            description="Test searching for content",
            messages=["Search for 'class VCPU' in the src/nimbus/v2 directory. Tell me which file contains it."],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def test_code_understanding(self) -> TestResult:
        """Test code understanding."""
        return await self.run_test(
            name="code_understanding",
            description="Test understanding code structure",
            messages=[
                "Read src/nimbus/v2/core/runtime/vcpu.py and explain what the VCPU class does in 2-3 sentences."
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def test_multi_turn_chat(self) -> TestResult:
        """Test multi-turn conversation."""
        return await self.run_test(
            name="multi_turn_chat",
            description="Test conversation context persistence",
            messages=[
                "Read pyproject.toml and tell me the project name.",
                "What dependencies does it have? List the first 3.",
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def test_bash_command(self) -> TestResult:
        """Test bash command execution."""
        return await self.run_test(
            name="bash_command",
            description="Test running bash commands",
            messages=["Run 'pwd' command and tell me the current directory."],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def test_complex_task(self) -> TestResult:
        """Test a more complex task requiring multiple tools."""
        return await self.run_test(
            name="complex_task",
            description="Test multi-step task",
            messages=[
                "Find all tool files in src/nimbus/v2/tools/, read the first one, and summarize what it does."
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    # =========================================================================
    # Scenario Tests - 情境模拟测试
    # =========================================================================

    async def scenario_bug_detection(self) -> TestResult:
        """场景: 发现代码中的 Bug。"""
        sandbox = SANDBOX_DIR
        return await self.run_test(
            name="scenario_bug_detection",
            description="场景: 阅读代码并发现其中的 Bug",
            messages=[
                f"Read {sandbox}/buggy_code/off_by_one.py and identify the bug in the sum_range function. "
                "Just explain what the bug is - DO NOT fix it or edit the file."
            ],
            validators=[
                lambda r: r["status"] == "OK",
                lambda r: "end + 1" in str(r.get("output", "")).lower()
                         or "inclusive" in str(r.get("output", "")).lower()
                         or "range" in str(r.get("output", "")).lower(),
            ],
        )

    async def scenario_bug_fix(self) -> TestResult:
        """场景: 修复代码中的 Bug。"""
        sandbox = SANDBOX_DIR
        buggy_file = sandbox / "buggy_code" / "logic_error.py"

        return await self.run_test(
            name="scenario_bug_fix",
            description="场景: 修复 find_max 函数中的 Bug",
            messages=[
                f"Read {buggy_file} and fix the bug in the find_max function. "
                "The bug is that it doesn't handle negative numbers correctly. "
                "Use the Edit tool to fix it."
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def scenario_code_review(self) -> TestResult:
        """场景: 代码审查，识别代码异味。"""
        sandbox = SANDBOX_DIR
        return await self.run_test(
            name="scenario_code_review",
            description="场景: 审查代码并识别问题",
            messages=[
                f"Read {sandbox}/refactor_target/duplicated_code.py and review it. "
                "Identify the main code smell. What is the problem with add_admin, add_member, add_guest methods? "
                "Just explain the issue, don't modify the code."
            ],
            validators=[
                lambda r: r["status"] == "OK",
                lambda r: any(word in str(r.get("output", "")).lower()
                             for word in ["duplicat", "repetit", "same", "similar", "common", "identical"]),
            ],
        )

    async def scenario_code_refactor(self) -> TestResult:
        """场景: 重构代码，消除重复。"""
        sandbox = SANDBOX_DIR
        target_file = sandbox / "refactor_target" / "duplicated_code.py"
        return await self.run_test(
            name="scenario_code_refactor",
            description="场景: 重构代码消除重复",
            messages=[
                f"Read {target_file}. Then use the Edit tool to add a new helper method called '_validate_user' "
                "at the beginning of the UserManager class that checks: 1) user_id not in self.users, "
                "2) name and email are not empty, 3) email contains @. Just add this one method, nothing else."
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def scenario_understand_project(self) -> TestResult:
        """场景: 理解项目结构。"""
        sandbox = SANDBOX_DIR
        project_dir = sandbox / "mini_project"
        return await self.run_test(
            name="scenario_understand_project",
            description="场景: 理解项目结构和各模块职责",
            messages=[
                f"Read {project_dir}/models.py and explain what data models it defines. Be brief."
            ],
            validators=[
                lambda r: r["status"] == "OK",
                lambda r: any(word in str(r.get("output", "")).lower()
                             for word in ["task", "project", "dataclass", "model"]),
            ],
        )

    async def scenario_add_feature(self) -> TestResult:
        """场景: 添加新功能。"""
        sandbox = SANDBOX_DIR
        service_file = sandbox / "mini_project" / "service.py"
        return await self.run_test(
            name="scenario_add_feature",
            description="场景: 为 TaskService 添加新方法",
            messages=[
                f"Read {service_file}. Add a new method called 'get_high_priority_tasks' "
                "that returns all tasks with priority >= 5 from a given project. "
                "Use the Edit tool to add this method to the TaskService class."
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def scenario_write_tests(self) -> TestResult:
        """场景: 为代码编写测试。"""
        sandbox = SANDBOX_DIR
        utils_file = sandbox / "mini_project" / "utils.py"
        test_file = sandbox / "mini_project" / "test_utils.py"
        return await self.run_test(
            name="scenario_write_tests",
            description="场景: 为 slugify 函数编写单元测试",
            messages=[
                f"Read {utils_file}, then create {test_file} with 2-3 pytest test cases for the slugify function only."
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def scenario_analyze_type_issues(self) -> TestResult:
        """场景: 分析类型问题。"""
        sandbox = SANDBOX_DIR
        return await self.run_test(
            name="scenario_analyze_type_issues",
            description="场景: 分析代码中的类型安全问题",
            messages=[
                f"Read {sandbox}/buggy_code/type_error.py and identify the type safety issues. "
                "For each function, explain what could go wrong and suggest fixes with proper type handling."
            ],
            validators=[
                lambda r: r["status"] == "OK",
                lambda r: any(word in str(r.get("output", "")).lower()
                             for word in ["str", "type", "convert", "check", "keyerror", "get"]),
            ],
        )

    async def scenario_document_api(self) -> TestResult:
        """场景: 编写 API 文档。"""
        sandbox = SANDBOX_DIR
        api_file = sandbox / "needs_docs" / "api.py"
        return await self.run_test(
            name="scenario_document_api",
            description="场景: 为 APIClient.__init__ 添加文档",
            messages=[
                f"Read {api_file}. Add a docstring to the __init__ method of APIClient class. "
                "Include description of parameters (base_url, api_key, timeout). Use the Edit tool."
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def scenario_find_dependencies(self) -> TestResult:
        """场景: 分析代码依赖关系。"""
        sandbox = SANDBOX_DIR
        project_dir = sandbox / "mini_project"
        return await self.run_test(
            name="scenario_find_dependencies",
            description="场景: 分析模块间的依赖关系",
            messages=[
                f"Analyze the imports in {project_dir}. "
                "Read all Python files and create a dependency graph showing which modules depend on which. "
                "Format: 'module_a -> module_b' means module_a imports from module_b."
            ],
            validators=[lambda r: r["status"] == "OK"],
        )

    async def scenario_split_function(self) -> TestResult:
        """场景: 拆分过长的函数。"""
        sandbox = SANDBOX_DIR
        long_func_file = sandbox / "refactor_target" / "long_function.py"
        return await self.run_test(
            name="scenario_split_function",
            description="场景: 识别长函数中的问题",
            messages=[
                f"Read {long_func_file}. The process_data function is too long. "
                "Identify and list the distinct steps/responsibilities in this function. "
                "Don't modify the code, just analyze and list them."
            ],
            validators=[
                lambda r: r["status"] == "OK",
                lambda r: any(word in str(r.get("output", "")).lower()
                             for word in ["validat", "filter", "transform", "aggregate", "step"]),
            ],
        )

    async def scenario_multi_file_search(self) -> TestResult:
        """场景: 跨文件搜索。"""
        sandbox = SANDBOX_DIR
        return await self.run_test(
            name="scenario_multi_file_search",
            description="场景: 跨多个文件搜索特定模式",
            messages=[
                f"Search for all functions that have 'BUG' in their comments in {sandbox}/buggy_code/. "
                "List each file and function name where you find BUG comments."
            ],
            validators=[
                lambda r: r["status"] == "OK",
                lambda r: "off_by_one" in str(r.get("output", "")).lower()
                         or "logic_error" in str(r.get("output", "")).lower()
                         or "type_error" in str(r.get("output", "")).lower(),
            ],
        )

    # =========================================================================
    # Test Runner
    # =========================================================================

    def get_basic_tests(self) -> Dict[str, Callable]:
        """Get basic functionality tests."""
        return {
            "simple_chat": self.test_simple_chat,
            "read_file": self.test_read_file,
            "glob_files": self.test_glob_files,
            "grep_content": self.test_grep_content,
            "code_understanding": self.test_code_understanding,
            "multi_turn_chat": self.test_multi_turn_chat,
            "bash_command": self.test_bash_command,
            "complex_task": self.test_complex_task,
        }

    def get_scenario_tests(self) -> Dict[str, Callable]:
        """Get scenario simulation tests."""
        return {
            "scenario_bug_detection": self.scenario_bug_detection,
            "scenario_bug_fix": self.scenario_bug_fix,
            "scenario_code_review": self.scenario_code_review,
            "scenario_code_refactor": self.scenario_code_refactor,
            "scenario_understand_project": self.scenario_understand_project,
            "scenario_add_feature": self.scenario_add_feature,
            "scenario_write_tests": self.scenario_write_tests,
            "scenario_analyze_type_issues": self.scenario_analyze_type_issues,
            "scenario_document_api": self.scenario_document_api,
            "scenario_find_dependencies": self.scenario_find_dependencies,
            "scenario_split_function": self.scenario_split_function,
            "scenario_multi_file_search": self.scenario_multi_file_search,
        }

    def get_all_tests(self) -> Dict[str, Callable]:
        """Get all test methods (basic + scenarios)."""
        tests = {}
        tests.update(self.get_basic_tests())
        tests.update(self.get_scenario_tests())
        return tests

    async def run_all_tests(self, test_names: List[str] = None):
        """Run all or selected tests."""
        all_tests = self.get_all_tests()

        if test_names:
            tests_to_run = {k: v for k, v in all_tests.items() if k in test_names}
        else:
            tests_to_run = all_tests

        console.print(f"\n[bold cyan]Running {len(tests_to_run)} tests...[/bold cyan]\n")

        for name, test_fn in tests_to_run.items():
            result = await test_fn()
            self.results.append(result)

        self._print_summary()

    def _print_summary(self):
        """Print test summary."""
        console.print("\n")
        console.print(Panel("[bold]Test Summary[/bold]", box=box.DOUBLE))

        table = Table(show_header=True, header_style="bold")
        table.add_column("Test", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Duration", justify="right")
        table.add_column("Message")

        passed = 0
        failed = 0

        for result in self.results:
            status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
            duration = f"{result.duration_ms:.0f}ms"
            message = result.message[:50] + "..." if len(result.message) > 50 else result.message

            table.add_row(result.name, status, duration, message)

            if result.passed:
                passed += 1
            else:
                failed += 1

        console.print(table)
        console.print()

        if failed == 0:
            console.print(f"[bold green]All {passed} tests passed![/bold green]")
        else:
            console.print(f"[bold]Results: [green]{passed} passed[/green], [red]{failed} failed[/red][/bold]")

    async def interactive_mode(self):
        """Run interactive chat mode."""
        console.print("\n[bold cyan]Interactive Mode[/bold cyan]")
        console.print("[dim]Type your messages. Commands: /quit, /new, /tests[/dim]\n")

        session_id = None

        while True:
            try:
                user_input = console.input("[bold green]> [/bold green]")
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Goodbye![/dim]")
                break

            if not user_input.strip():
                continue

            if user_input.strip().lower() in ("/quit", "/exit", "/q"):
                console.print("[dim]Goodbye![/dim]")
                break

            if user_input.strip().lower() == "/new":
                session_id = None
                console.print("[dim]Started new session[/dim]")
                continue

            if user_input.strip().lower() == "/tests":
                await self.run_all_tests()
                continue

            response = await self.chat(user_input, session_id=session_id)
            session_id = response.get("session_id")

            if response["status"] == "OK":
                console.print(f"\n[cyan]{response['output']}[/cyan]\n")
            else:
                console.print(f"\n[red]Error: {response.get('error', 'Unknown')}[/red]\n")


async def main():
    parser = argparse.ArgumentParser(
        description="AgentOS CLI Test Framework - 情境模拟练兵场",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tests/test_agentos_cli.py                     # Run basic tests
  python tests/test_agentos_cli.py --scenario          # Run scenario tests
  python tests/test_agentos_cli.py --all               # Run all tests
  python tests/test_agentos_cli.py -t read_file        # Run specific test
  python tests/test_agentos_cli.py --scenario -t scenario_bug_fix
  python tests/test_agentos_cli.py -i                  # Interactive mode
  python tests/test_agentos_cli.py --list              # List all tests
        """
    )
    parser.add_argument("--test", "-t", nargs="+", help="Run specific tests")
    parser.add_argument("--scenario", "-s", action="store_true", help="Run scenario tests (情境模拟)")
    parser.add_argument("--all", "-a", action="store_true", help="Run all tests (basic + scenario)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--model", "-m", default="gemini-3-flash-preview", help="Model to use")
    parser.add_argument("--workspace", "-w", type=Path, default=None, help="Workspace directory")
    parser.add_argument("--list", "-l", action="store_true", help="List available tests")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup sandbox and exit")

    args = parser.parse_args()

    # Handle cleanup
    if args.cleanup:
        console.print("[dim]Cleaning up sandbox...[/dim]")
        cleanup_sandbox()
        console.print("[green]Sandbox cleaned up.[/green]")
        return

    # Determine workspace
    workspace = args.workspace
    if workspace is None:
        # Try to find project root
        current = Path(__file__).parent
        while current != current.parent:
            if (current / "pyproject.toml").exists():
                workspace = current
                break
            current = current.parent
        if workspace is None:
            workspace = Path.cwd()

    console.print(f"[dim]Workspace: {workspace}[/dim]")

    framework = AgentOSTestFramework(
        workspace=workspace,
        verbose=args.verbose,
        model=args.model,
    )

    if args.list:
        console.print("\n[bold cyan]Basic Tests:[/bold cyan]")
        for name in framework.get_basic_tests().keys():
            console.print(f"  - {name}")
        console.print("\n[bold magenta]Scenario Tests (情境模拟):[/bold magenta]")
        for name in framework.get_scenario_tests().keys():
            console.print(f"  - {name}")
        return

    # Setup sandbox for scenario tests
    if args.scenario or args.all or (args.test and any("scenario" in t for t in args.test)):
        console.print("[dim]Setting up sandbox playground...[/dim]")
        setup_sandbox()
        console.print(f"[green]Sandbox ready at {SANDBOX_DIR}[/green]")

    # Setup AgentOS
    if not await framework.setup():
        sys.exit(1)

    try:
        if args.interactive:
            await framework.interactive_mode()
        else:
            # Determine which tests to run
            if args.test:
                test_names = args.test
            elif args.all:
                test_names = list(framework.get_all_tests().keys())
            elif args.scenario:
                test_names = list(framework.get_scenario_tests().keys())
            else:
                test_names = list(framework.get_basic_tests().keys())

            await framework.run_all_tests(test_names=test_names)

            # Exit with error code if any test failed
            if any(not r.passed for r in framework.results):
                sys.exit(1)

    finally:
        await framework.cleanup()
        # Don't auto-cleanup sandbox so user can inspect results
        # cleanup_sandbox()


if __name__ == "__main__":
    asyncio.run(main())
