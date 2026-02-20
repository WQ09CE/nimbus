#!/usr/bin/env python3
"""
Nimbus Server E2E Test - DAG Parallel Execution Verification

This script tests the Nimbus Server's DAG parallel execution capabilities:
1. Parallel file reading - verify multiple tool.start events have close timestamps
2. Parallel Bash + Read - Bash file listing then read multiple in parallel
3. DAG dependency execution - verify execution order is correct

Key verification points:
- Check SSE events for multiple tool.start events with close timestamps (parallel)
- Verify final results are correct
- Confirm dependency ordering is respected

API Endpoints:
- GET /health - Health check
- POST /session - Create session
- POST /session/{session_id}/message - Send message (SSE stream)

Usage:
    python tests/e2e_dag_parallel.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_dag_parallel.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

# Check for httpx
try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# Configuration
SERVER_URL = os.environ.get("NIMBUS_SERVER_URL", "http://127.0.0.1:8080")

# Threshold for considering events as "parallel" (in milliseconds)
PARALLEL_THRESHOLD_MS = 500.0


@dataclass
class SSEEvent:
    """Represents a Server-Sent Event with timestamp."""
    event: str
    data: dict[str, Any]
    raw_data: str = ""
    timestamp_ms: float = 0.0  # Timestamp when event was received


@dataclass
class ParallelTestResult:
    """Represents the result of a parallel execution test."""
    name: str
    description: str
    passed: bool
    verdict: str
    events: List[SSEEvent] = field(default_factory=list)
    tool_start_times: List[Tuple[str, float]] = field(default_factory=list)  # (tool_id, timestamp)
    tool_done_times: List[Tuple[str, float]] = field(default_factory=list)   # (tool_id, timestamp)
    duration_ms: float = 0.0


class SSEParser:
    """Parser for Server-Sent Events stream with timestamp tracking."""

    def __init__(self, start_time: float):
        self.current_event: Optional[str] = None
        self.current_data: str = ""
        self.events: List[SSEEvent] = []
        self.start_time = start_time

    def feed_line(self, line: str) -> Optional[SSEEvent]:
        """Feed a line and return an event if complete."""
        line = line.strip()

        if line.startswith("event:"):
            self.current_event = line[6:].strip()
            return None
        elif line.startswith("data:"):
            self.current_data = line[5:].strip()
            return None
        elif line == "" and self.current_event and self.current_data:
            # Empty line signals end of event
            try:
                event_data = json.loads(self.current_data)
            except json.JSONDecodeError:
                event_data = {"raw": self.current_data}

            # Calculate timestamp relative to start
            timestamp_ms = (time.time() - self.start_time) * 1000

            event = SSEEvent(
                event=self.current_event,
                data=event_data,
                raw_data=self.current_data,
                timestamp_ms=timestamp_ms
            )
            self.events.append(event)

            # Reset for next event
            self.current_event = None
            self.current_data = ""

            return event

        return None

    def reset(self):
        """Reset parser state."""
        self.current_event = None
        self.current_data = ""
        self.events = []


class DAGParallelTest:
    """E2E test runner for DAG parallel execution verification."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.session_id: Optional[str] = None
        self.results: List[ParallelTestResult] = []

    def print_header(self, text: str, char: str = "="):
        """Print a section header."""
        print("\n" + char * 70)
        print(text)
        print(char * 70)

    def print_subheader(self, text: str):
        """Print a sub-section header."""
        print(f"\n--- {text} ---")

    def print_info(self, text: str):
        """Print info message."""
        print(f"[INFO] {text}")

    def print_ok(self, text: str):
        """Print success message."""
        print(f"[OK] {text}")

    def print_fail(self, text: str):
        """Print failure message."""
        print(f"[FAIL] {text}")

    def print_warn(self, text: str):
        """Print warning message."""
        print(f"[WARN] {text}")

    def print_event(self, event: SSEEvent, max_len: int = 80):
        """Print an SSE event with timestamp."""
        data_str = json.dumps(event.data)
        if len(data_str) > max_len:
            data_str = data_str[:max_len] + "..."
        print(f"    [{event.timestamp_ms:>7.1f}ms] [SSE:{event.event}] {data_str}")

    async def check_health(self) -> bool:
        """Check if server is healthy."""
        self.print_subheader("Health Check")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server_url}/health",
                    timeout=5.0
                )

                if response.status_code == 200:
                    data = response.json()
                    healthy = data.get("healthy", False)
                    if healthy:
                        self.print_ok(f"Server healthy: {data}")
                        return True
                    else:
                        self.print_fail(f"Server unhealthy: {data}")
                        return False
                else:
                    self.print_fail(f"Health check returned {response.status_code}")
                    return False
        except httpx.ConnectError:
            self.print_fail(f"Cannot connect to {self.server_url}")
            self.print_info("Is the server running? Start with: uv run nimbus serve")
            return False
        except Exception as e:
            self.print_fail(f"Health check failed: {e}")
            return False

    async def create_session(self, planner_type: str = "dag") -> Optional[str]:
        """Create a new session with DAG planner."""
        self.print_subheader(f"Create Session (planner_type={planner_type})")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.server_url}/session",
                    json={"planner_type": planner_type},
                    timeout=10.0
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    session_id = data.get("id")
                    if session_id:
                        self.session_id = session_id
                        self.print_ok(f"Session created: {session_id}")
                        self.print_info(f"Planner type: {data.get('planner_type', planner_type)}")
                        return session_id
                    else:
                        self.print_fail("Response missing 'id' field")
                        return None
                else:
                    self.print_fail(f"Create session returned {response.status_code}")
                    self.print_info(f"Response: {response.text[:200]}")
                    return None
        except Exception as e:
            self.print_fail(f"Create session failed: {e}")
            return None

    async def send_message(
        self,
        message: str,
        timeout: float = 120.0,
        verbose: bool = True
    ) -> Tuple[List[SSEEvent], str]:
        """
        Send a message and collect SSE events with timestamps.

        Returns:
            Tuple of (events, final_response_text)
        """
        if not self.session_id:
            raise ValueError("No session created")

        url = f"{self.server_url}/session/{self.session_id}/message"

        # Use the parts format that OpenCode TUI expects
        request_body = {
            "parts": [{"type": "text", "text": message}]
        }

        start_time = time.time()
        parser = SSEParser(start_time)
        response_text = ""

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                url,
                json=request_body,
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(timeout, connect=10.0)
            ) as response:
                async for line in response.aiter_lines():
                    event = parser.feed_line(line)
                    if event:
                        if verbose:
                            self.print_event(event)

                        # Collect response text from content.delta events
                        if event.event == "content.delta":
                            text = event.data.get("text", "")
                            response_text += text

        return parser.events, response_text

    def extract_tool_events(
        self, events: List[SSEEvent]
    ) -> Tuple[List[Tuple[str, str, float]], List[Tuple[str, str, float]]]:
        """
        Extract tool start and done events with their timestamps.

        Returns:
            Tuple of (tool_starts, tool_dones)
            Each is a list of (task_id, tool_name, timestamp_ms)
        """
        tool_starts: List[Tuple[str, str, float]] = []
        tool_dones: List[Tuple[str, str, float]] = []

        for event in events:
            if event.event in ("tool.start", "task_start"):
                task_id = event.data.get("taskID", event.data.get("task_id", ""))
                tool_name = event.data.get("name", event.data.get("tool", ""))
                tool_starts.append((task_id, tool_name, event.timestamp_ms))
            elif event.event in ("tool.done", "task_done"):
                task_id = event.data.get("taskID", event.data.get("task_id", ""))
                tool_name = event.data.get("name", event.data.get("tool", ""))
                tool_dones.append((task_id, tool_name, event.timestamp_ms))

        return tool_starts, tool_dones

    def check_parallel_execution(
        self,
        tool_starts: List[Tuple[str, str, float]],
        min_parallel: int = 2,
        threshold_ms: float = PARALLEL_THRESHOLD_MS
    ) -> Tuple[bool, str]:
        """
        Check if tools were executed in parallel.

        Args:
            tool_starts: List of (task_id, tool_name, timestamp_ms)
            min_parallel: Minimum number of tools expected to start in parallel
            threshold_ms: Maximum time difference to consider as parallel

        Returns:
            Tuple of (is_parallel, explanation)
        """
        if len(tool_starts) < min_parallel:
            return False, f"Only {len(tool_starts)} tool.start events, expected at least {min_parallel}"

        # Sort by timestamp
        sorted_starts = sorted(tool_starts, key=lambda x: x[2])

        # Find groups of parallel starts
        parallel_groups: List[List[Tuple[str, str, float]]] = []
        current_group: List[Tuple[str, str, float]] = [sorted_starts[0]]

        for i in range(1, len(sorted_starts)):
            time_diff = sorted_starts[i][2] - current_group[0][2]
            if time_diff <= threshold_ms:
                current_group.append(sorted_starts[i])
            else:
                if len(current_group) >= min_parallel:
                    parallel_groups.append(current_group)
                current_group = [sorted_starts[i]]

        # Check last group
        if len(current_group) >= min_parallel:
            parallel_groups.append(current_group)

        if parallel_groups:
            # Report the largest parallel group
            largest_group = max(parallel_groups, key=len)
            tools_in_group = [t[1] for t in largest_group]
            time_span = largest_group[-1][2] - largest_group[0][2]
            return True, (
                f"Found {len(largest_group)} parallel tools "
                f"({', '.join(tools_in_group)}) within {time_span:.1f}ms"
            )
        else:
            # Calculate time differences for debugging
            time_diffs = []
            for i in range(1, len(sorted_starts)):
                diff = sorted_starts[i][2] - sorted_starts[i-1][2]
                time_diffs.append(f"{diff:.1f}ms")
            return False, (
                f"No parallel group found. Time gaps between starts: {', '.join(time_diffs)}. "
                f"Threshold: {threshold_ms}ms"
            )

    def check_dependency_order(
        self,
        tool_starts: List[Tuple[str, str, float]],
        tool_dones: List[Tuple[str, str, float]],
        dependencies: List[Tuple[str, str]]  # List of (predecessor_tool, successor_tool)
    ) -> Tuple[bool, str]:
        """
        Check if dependency ordering was respected.

        Args:
            tool_starts: List of (task_id, tool_name, timestamp_ms)
            tool_dones: List of (task_id, tool_name, timestamp_ms)
            dependencies: List of (predecessor_tool, successor_tool) pairs

        Returns:
            Tuple of (order_correct, explanation)
        """
        # Build maps for quick lookup
        start_by_tool: dict[str, float] = {}
        done_by_tool: dict[str, float] = {}

        for task_id, tool_name, ts in tool_starts:
            start_by_tool[tool_name] = ts
        for task_id, tool_name, ts in tool_dones:
            done_by_tool[tool_name] = ts

        violations = []
        for pred, succ in dependencies:
            pred_done = done_by_tool.get(pred)
            succ_start = start_by_tool.get(succ)

            if pred_done is None:
                violations.append(f"Predecessor '{pred}' never completed")
            elif succ_start is None:
                violations.append(f"Successor '{succ}' never started")
            elif pred_done > succ_start:
                violations.append(
                    f"'{succ}' started at {succ_start:.1f}ms but "
                    f"'{pred}' completed at {pred_done:.1f}ms"
                )

        if violations:
            return False, "; ".join(violations)
        else:
            order_details = []
            for pred, succ in dependencies:
                pred_done = done_by_tool.get(pred, 0)
                succ_start = start_by_tool.get(succ, 0)
                gap = succ_start - pred_done
                order_details.append(f"'{pred}' -> '{succ}' (gap: {gap:.1f}ms)")
            return True, "Dependency order respected: " + ", ".join(order_details)

    async def test_parallel_file_read(self) -> ParallelTestResult:
        """
        Test 1: Parallel File Reading

        Request reading of 3 files simultaneously and verify:
        - Multiple tool.start events occur with close timestamps
        - Total time is less than serial execution would take
        """
        self.print_header("Test 1: Parallel File Reading")
        self.print_info("Request reading 3 files and verify parallel execution")
        print()

        message = (
            "Read the following 3 files in parallel and tell me what each contains:\n"
            "1. pyproject.toml\n"
            "2. README.md\n"
            "3. tests/__init__.py\n"
            "Be brief in your response."
        )

        print(f"Message: {message[:100]}...")
        print()

        start_time = time.time()

        try:
            events, response_text = await self.send_message(message, timeout=120.0)
            duration_ms = (time.time() - start_time) * 1000

            # Extract tool events
            tool_starts, tool_dones = self.extract_tool_events(events)

            self.print_subheader("Analysis")
            print(f"Total duration: {duration_ms:.1f}ms")
            print(f"Tool starts: {len(tool_starts)}")
            print(f"Tool dones: {len(tool_dones)}")

            # Print tool start timeline
            if tool_starts:
                print("\nTool Start Timeline:")
                for task_id, tool_name, ts in sorted(tool_starts, key=lambda x: x[2]):
                    print(f"  {ts:>7.1f}ms: {tool_name} ({task_id[:8]}...)")

            # Check for parallel execution
            is_parallel, parallel_verdict = self.check_parallel_execution(
                tool_starts, min_parallel=2, threshold_ms=PARALLEL_THRESHOLD_MS
            )

            # Additional check: response should mention all 3 files
            response_lower = response_text.lower()
            files_mentioned = sum([
                "pyproject" in response_lower,
                "readme" in response_lower,
                "init" in response_lower or "tests" in response_lower
            ])

            content_ok = files_mentioned >= 2
            content_verdict = f"Response mentions {files_mentioned}/3 files"

            passed = is_parallel and content_ok
            verdict = f"{parallel_verdict}; {content_verdict}"

            if passed:
                self.print_ok(verdict)
            else:
                self.print_fail(verdict)

            result = ParallelTestResult(
                name="Parallel File Reading",
                description="Read 3 files in parallel, verify tool.start timestamps are close",
                passed=passed,
                verdict=verdict,
                events=events,
                tool_start_times=[(t[0], t[2]) for t in tool_starts],
                tool_done_times=[(t[0], t[2]) for t in tool_dones],
                duration_ms=duration_ms
            )
            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Test failed with exception: {e}")
            result = ParallelTestResult(
                name="Parallel File Reading",
                description="Read 3 files in parallel",
                passed=False,
                verdict=f"Exception: {e}",
                duration_ms=duration_ms
            )
            self.results.append(result)
            return result

    async def test_parallel_bash_then_read(self) -> ParallelTestResult:
        """
        Test 2: Parallel Bash + Read

        First Bash to find files, then read multiple files in parallel.
        Verify the Read operations happen in parallel after Bash completes.
        """
        self.print_header("Test 2: Parallel Bash then Read")
        self.print_info("Bash file listing first, then read multiple in parallel")
        print()

        message = (
            "First, use Bash to find all Python files in the tests/ directory "
            "(e.g., find tests -name '*.py'). Then read the first 3 test files you find "
            "and briefly describe what each tests."
        )

        print(f"Message: {message[:100]}...")
        print()

        start_time = time.time()

        try:
            events, response_text = await self.send_message(message, timeout=180.0)
            duration_ms = (time.time() - start_time) * 1000

            # Extract tool events
            tool_starts, tool_dones = self.extract_tool_events(events)

            self.print_subheader("Analysis")
            print(f"Total duration: {duration_ms:.1f}ms")
            print(f"Tool starts: {len(tool_starts)}")

            # Separate Bash and Read events
            bash_starts = [t for t in tool_starts if "bash" in t[1].lower()]
            read_starts = [t for t in tool_starts if "read" in t[1].lower()]

            print(f"Bash starts: {len(bash_starts)}")
            print(f"Read starts: {len(read_starts)}")

            # Print timeline
            if tool_starts:
                print("\nTool Timeline:")
                for task_id, tool_name, ts in sorted(tool_starts, key=lambda x: x[2]):
                    print(f"  {ts:>7.1f}ms: {tool_name}")

            # Check that Bash completed before Reads started
            bash_dones = [t for t in tool_dones if "bash" in t[1].lower()]

            order_ok = True
            order_verdict = ""
            if bash_starts and bash_dones and read_starts:
                bash_done_time = max(t[2] for t in bash_dones)
                first_read_time = min(t[2] for t in read_starts)
                if bash_done_time <= first_read_time:
                    order_verdict = f"Bash completed at {bash_done_time:.1f}ms, first Read at {first_read_time:.1f}ms"
                else:
                    order_ok = False
                    order_verdict = f"Read started at {first_read_time:.1f}ms before Bash done at {bash_done_time:.1f}ms"
            elif not bash_starts:
                order_verdict = "No Bash operation detected"
            elif not read_starts:
                order_verdict = "No Read operations detected"
            else:
                order_verdict = "Could not determine execution order"

            # Check parallel reads
            is_parallel = False
            parallel_verdict = "No parallel Read operations"
            if len(read_starts) >= 2:
                is_parallel, parallel_verdict = self.check_parallel_execution(
                    read_starts, min_parallel=2, threshold_ms=PARALLEL_THRESHOLD_MS
                )

            passed = order_ok and (is_parallel or len(read_starts) < 2)
            verdict = f"{order_verdict}; {parallel_verdict}"

            if passed:
                self.print_ok(verdict)
            else:
                self.print_fail(verdict)

            result = ParallelTestResult(
                name="Parallel Bash then Read",
                description="Bash file listing first, then parallel read",
                passed=passed,
                verdict=verdict,
                events=events,
                tool_start_times=[(t[0], t[2]) for t in tool_starts],
                tool_done_times=[(t[0], t[2]) for t in tool_dones],
                duration_ms=duration_ms
            )
            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Test failed with exception: {e}")
            result = ParallelTestResult(
                name="Parallel Bash then Read",
                description="Bash then parallel read",
                passed=False,
                verdict=f"Exception: {e}",
                duration_ms=duration_ms
            )
            self.results.append(result)
            return result

    async def test_dag_dependency_execution(self) -> ParallelTestResult:
        """
        Test 3: DAG Dependency Execution

        Create a task with clear dependencies and verify execution order:
        1. Read pyproject.toml (no dependency)
        2. Based on reading, search for something specific (depends on 1)
        3. Summarize findings (depends on 1 and 2)
        """
        self.print_header("Test 3: DAG Dependency Execution")
        self.print_info("Verify tasks with dependencies execute in correct order")
        print()

        message = (
            "Please do the following tasks in order:\n"
            "1. First, read pyproject.toml to find the project name\n"
            "2. Then, use Bash with grep to search for where that project name is imported in the codebase\n"
            "3. Finally, summarize what you found\n"
            "Execute these in the correct order respecting dependencies."
        )

        print(f"Message: {message[:100]}...")
        print()

        start_time = time.time()

        try:
            events, response_text = await self.send_message(message, timeout=180.0)
            duration_ms = (time.time() - start_time) * 1000

            # Extract tool events
            tool_starts, tool_dones = self.extract_tool_events(events)

            self.print_subheader("Analysis")
            print(f"Total duration: {duration_ms:.1f}ms")
            print(f"Tool starts: {len(tool_starts)}")
            print(f"Tool dones: {len(tool_dones)}")

            # Print timeline
            if tool_starts:
                print("\nExecution Timeline:")
                all_events = []
                for task_id, tool_name, ts in tool_starts:
                    all_events.append((ts, "START", tool_name, task_id))
                for task_id, tool_name, ts in tool_dones:
                    all_events.append((ts, "DONE ", tool_name, task_id))

                for ts, event_type, tool_name, task_id in sorted(all_events, key=lambda x: x[0]):
                    print(f"  {ts:>7.1f}ms: {event_type} {tool_name}")

            # Check for Read -> Bash (search) dependency
            read_starts = [t for t in tool_starts if "read" in t[1].lower()]
            read_dones = [t for t in tool_dones if "read" in t[1].lower()]
            grep_starts = [t for t in tool_starts if "bash" in t[1].lower()]

            dependency_ok = True
            dependency_verdict = ""

            if read_dones and grep_starts:
                # Find the Read for pyproject.toml
                pyproject_read_done = None
                for task_id, tool_name, ts in read_dones:
                    # Check if this was the pyproject.toml read by looking at events
                    for e in events:
                        if e.event in ("tool.start", "task_start"):
                            if e.data.get("taskID", e.data.get("task_id", "")) == task_id:
                                input_data = e.data.get("input", {})
                                file_path = input_data.get("file_path", input_data.get("path", ""))
                                if "pyproject" in file_path.lower():
                                    pyproject_read_done = ts
                                    break

                if pyproject_read_done:
                    first_grep_start = min(t[2] for t in grep_starts)
                    if pyproject_read_done <= first_grep_start:
                        dependency_verdict = (
                            f"Correct: Read completed at {pyproject_read_done:.1f}ms, "
                            f"Bash search started at {first_grep_start:.1f}ms"
                        )
                    else:
                        dependency_ok = False
                        dependency_verdict = (
                            f"Violation: Bash search started at {first_grep_start:.1f}ms "
                            f"before Read done at {pyproject_read_done:.1f}ms"
                        )
                else:
                    dependency_verdict = "Could not find pyproject.toml Read timing"
            elif not read_starts:
                dependency_verdict = "No Read operation found"
            elif not grep_starts:
                dependency_verdict = "No Bash search operation found"
            else:
                dependency_verdict = "Incomplete tool execution data"

            # Check that response contains meaningful content
            response_lower = response_text.lower()
            has_project_name = "nimbus" in response_lower
            has_summary = len(response_text) > 50

            content_ok = has_project_name and has_summary
            content_verdict = f"Response contains project name: {has_project_name}, has summary: {has_summary}"

            passed = dependency_ok and content_ok
            verdict = f"{dependency_verdict}; {content_verdict}"

            if passed:
                self.print_ok(verdict)
            else:
                self.print_fail(verdict)

            result = ParallelTestResult(
                name="DAG Dependency Execution",
                description="Verify dependent tasks execute in correct order",
                passed=passed,
                verdict=verdict,
                events=events,
                tool_start_times=[(t[0], t[2]) for t in tool_starts],
                tool_done_times=[(t[0], t[2]) for t in tool_dones],
                duration_ms=duration_ms
            )
            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Test failed with exception: {e}")
            result = ParallelTestResult(
                name="DAG Dependency Execution",
                description="Verify dependency ordering",
                passed=False,
                verdict=f"Exception: {e}",
                duration_ms=duration_ms
            )
            self.results.append(result)
            return result

    async def run_all_tests(self) -> bool:
        """Run all DAG parallel execution tests."""
        self.print_header("Nimbus E2E Test - DAG Parallel Execution", "=")
        self.print_info(f"Server: {self.server_url}")
        self.print_info("Testing DAG parallel execution and dependency ordering")
        self.print_info(f"Parallel threshold: {PARALLEL_THRESHOLD_MS}ms")
        print()

        # Health check
        if not await self.check_health():
            self.print_fail("Server not available, aborting tests")
            return False

        # Define test functions
        tests = [
            self.test_parallel_file_read,
            self.test_parallel_glob_then_read,
            self.test_dag_dependency_execution,
        ]

        for test_func in tests:
            # Create fresh session for each test
            self.print_subheader("Creating fresh session for test isolation")
            session_id = await self.create_session(planner_type="dag")
            if not session_id:
                self.print_warn("Could not create session, skipping test")
                continue

            await test_func()
            # Pause between tests
            await asyncio.sleep(1)

        # Print summary
        self.print_summary()

        return all(r.passed for r in self.results)

    def print_summary(self):
        """Print test summary."""
        self.print_header("DAG Parallel Execution Test Summary")

        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            print(f"\n  [{status}] {result.name}")
            print(f"         {result.description}")
            print(f"         Verdict: {result.verdict}")
            print(f"         Duration: {result.duration_ms:.0f}ms")
            print(f"         Tool starts: {len(result.tool_start_times)}")

        print()
        print("=" * 70)
        print(f"Total: {len(self.results)} tests, {passed} passed, {failed} failed")

        if failed == 0:
            print("\n[ALL DAG PARALLEL EXECUTION TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")
            print("\nNote: Parallel execution depends on server implementation.")
            print("Check event timestamps and tool execution logs above.")


async def main():
    """Main entry point."""
    tester = DAGParallelTest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
