#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Rule Planner Fast Path

This script tests the Nimbus Server's rule-based planning fast path,
which handles common patterns without invoking the LLM.

Test Cases:
1. Greeting rules - "hello", "hi" - direct response, no LLM
2. File read rules - "read pyproject.toml" - direct Read tool invocation
3. List files rules - "list files in src" - direct Bash tool invocation
4. Search rules - "search def create_plan" - direct Bash tool invocation

Key Validation Points:
- Response speed (rule matching should be < 500ms without LLM call)
- Correct results
- SSE events should not have "planning" state (or very brief)

Usage:
    python tests/e2e_rule_planner.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_rule_planner.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Check for httpx
try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# Configuration
SERVER_URL = os.environ.get("NIMBUS_SERVER_URL", "http://127.0.0.1:8080")

# Performance thresholds
RULE_MATCH_THRESHOLD_MS = 500  # Rule matches should complete under 500ms
LLM_THRESHOLD_MS = 2000  # LLM calls typically take > 2000ms


@dataclass
class SSEEvent:
    """Represents a Server-Sent Event."""
    event: str
    data: dict[str, Any]
    raw_data: str = ""
    timestamp: float = 0.0


@dataclass
class TestResult:
    """Represents the result of a test case."""
    name: str
    passed: bool
    message: str
    duration_ms: float
    events: list[SSEEvent] = field(default_factory=list)
    is_fast_path: bool = False  # True if rule matched without LLM
    planning_duration_ms: float = 0.0


class SSEParser:
    """Parser for Server-Sent Events stream."""

    def __init__(self):
        self.current_event: Optional[str] = None
        self.current_data: str = ""
        self.events: list[SSEEvent] = []
        self.start_time: float = 0.0

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

            event = SSEEvent(
                event=self.current_event,
                data=event_data,
                raw_data=self.current_data,
                timestamp=time.time() - self.start_time
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
        self.start_time = time.time()


class RulePlannerE2ETest:
    """E2E test runner for Nimbus Server rule planner fast path."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.session_id: Optional[str] = None
        self.results: list[TestResult] = []

    def print_header(self, text: str):
        """Print a section header."""
        print("\n" + "=" * 60)
        print(text)
        print("=" * 60)

    def print_info(self, text: str):
        """Print info message."""
        print(f"[INFO] {text}")

    def print_ok(self, text: str):
        """Print success message."""
        print(f"[OK] {text}")

    def print_fail(self, text: str):
        """Print failure message."""
        print(f"[FAIL] {text}")

    def print_perf(self, text: str):
        """Print performance message."""
        print(f"[PERF] {text}")

    def print_event(self, event: SSEEvent, max_len: int = 100):
        """Print an SSE event."""
        data_str = json.dumps(event.data)
        if len(data_str) > max_len:
            data_str = data_str[:max_len] + "..."
        print(f"  [{event.timestamp:.3f}s] [SSE:{event.event}] {data_str}")

    async def check_health(self) -> bool:
        """Check if server is healthy."""
        self.print_header("Step 0: Health Check")

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

    async def create_session(self) -> Optional[str]:
        """Create a new session."""
        self.print_header("Step 1: Create Session")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.server_url}/session",
                    json={},
                    timeout=10.0
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    session_id = data.get("id")
                    if session_id:
                        self.session_id = session_id
                        self.print_ok(f"Session created: {session_id}")
                        self.print_info(f"Title: {data.get('title', '(none)')}")
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
        timeout: float = 120.0
    ) -> tuple[list[SSEEvent], str, float]:
        """
        Send a message and collect SSE events.

        Returns:
            Tuple of (events, final_response_text, planning_duration_ms)
        """
        if not self.session_id:
            raise ValueError("No session created")

        url = f"{self.server_url}/session/{self.session_id}/message"

        request_body = {
            "parts": [{"type": "text", "text": message}]
        }

        parser = SSEParser()
        parser.start_time = time.time()
        response_text = ""
        planning_start: Optional[float] = None
        planning_end: Optional[float] = None

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
                        self.print_event(event)

                        # Track planning state timing
                        if event.event == "status":
                            status = event.data.get("status", "")
                            if status == "planning" and planning_start is None:
                                planning_start = time.time()
                            elif status != "planning" and planning_start and planning_end is None:
                                planning_end = time.time()

                        # Collect response text from content.delta events
                        if event.event == "content.delta":
                            text = event.data.get("text", "")
                            response_text += text

        # Calculate planning duration
        planning_duration_ms = 0.0
        if planning_start:
            end_time = planning_end if planning_end else time.time()
            planning_duration_ms = (end_time - planning_start) * 1000

        return parser.events, response_text, planning_duration_ms

    def analyze_fast_path(
        self,
        events: list[SSEEvent],
        duration_ms: float,
        planning_duration_ms: float
    ) -> tuple[bool, str]:
        """
        Analyze if the request used the fast path (rule matching).

        Returns:
            Tuple of (is_fast_path, analysis_message)
        """
        # Check for rule match indicators
        rule_matched = False
        llm_called = False

        for event in events:
            # Check for rule match metadata
            if event.event == "metadata":
                if event.data.get("matched_rule"):
                    rule_matched = True
            # Check for LLM streaming (indicates slow path)
            if event.event == "llm.start" or event.event == "llm.token":
                llm_called = True

        # Heuristic: fast path should be much faster
        is_fast = duration_ms < RULE_MATCH_THRESHOLD_MS

        if rule_matched and not llm_called:
            return True, f"Rule matched, no LLM call, {duration_ms:.0f}ms"
        elif is_fast and not llm_called:
            return True, f"Fast response ({duration_ms:.0f}ms), likely rule match"
        elif llm_called:
            return False, f"LLM was called, {duration_ms:.0f}ms"
        else:
            return is_fast, f"Response in {duration_ms:.0f}ms, planning took {planning_duration_ms:.0f}ms"

    async def run_test_case(
        self,
        name: str,
        message: str,
        success_criteria: Optional[Callable] = None,
        expect_fast_path: bool = True,
        timeout: float = 30.0
    ) -> TestResult:
        """
        Run a single test case.

        Args:
            name: Test case name
            message: Message to send
            success_criteria: Optional function(events, response_text) -> (bool, message)
            expect_fast_path: Whether this test should use the fast path
            timeout: Timeout in seconds
        """
        self.print_header(f"Test: {name}")
        self.print_info(f"Message: {message}")
        self.print_info(f"Expected: {'Fast path (rule match)' if expect_fast_path else 'LLM path'}")
        print()

        start_time = time.time()

        try:
            events, response_text, planning_duration_ms = await self.send_message(
                message, timeout
            )
            duration_ms = (time.time() - start_time) * 1000

            # Analyze if fast path was used
            is_fast_path, fast_path_msg = self.analyze_fast_path(
                events, duration_ms, planning_duration_ms
            )

            # Default success criteria
            if success_criteria:
                passed, msg = success_criteria(events, response_text)
            else:
                done_events = [
                    e for e in events
                    if e.event in ("event.done", "content.done", "done")
                ]
                passed = len(done_events) > 0 or len(response_text) > 0
                msg = f"Received {len(events)} events, response length: {len(response_text)}"

            # Check fast path expectation
            if expect_fast_path and not is_fast_path:
                passed = False
                msg += f" [Expected fast path but got slow path: {fast_path_msg}]"
            elif expect_fast_path and is_fast_path:
                self.print_perf(f"Fast path confirmed: {fast_path_msg}")

            result = TestResult(
                name=name,
                passed=passed,
                message=msg,
                duration_ms=duration_ms,
                events=events,
                is_fast_path=is_fast_path,
                planning_duration_ms=planning_duration_ms
            )

            if passed:
                self.print_ok(f"{msg} ({duration_ms:.0f}ms)")
            else:
                self.print_fail(f"{msg} ({duration_ms:.0f}ms)")

            # Print response excerpt
            if response_text:
                excerpt = response_text[:300]
                if len(response_text) > 300:
                    excerpt += "..."
                print(f"\n[Response]\n{excerpt}")

            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
                events=[]
            )
            self.print_fail(f"Test failed with exception: {e}")
            self.results.append(result)
            return result

    async def run_all_tests(self) -> bool:
        """Run all E2E tests for rule planner fast path."""
        self.print_header("Nimbus E2E Test - Rule Planner Fast Path")
        self.print_info(f"Server: {self.server_url}")
        self.print_info(f"Fast path threshold: < {RULE_MATCH_THRESHOLD_MS}ms")
        print()

        # Step 0: Health check
        if not await self.check_health():
            self.print_fail("Server not available, aborting tests")
            return False

        # Step 1: Create session
        session_id = await self.create_session()
        if not session_id:
            self.print_fail("Cannot create session, aborting tests")
            return False

        # Test cases for rule planner fast path
        test_cases = [
            # =================================================================
            # Greeting Rules - Direct response, no LLM
            # =================================================================
            {
                "name": "Greeting - Chinese",
                "message": "你好",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    "你好" in text or "帮" in text or len(text) > 0,
                    f"Got greeting response: {text[:50] if text else '(empty)'}..."
                )
            },
            {
                "name": "Greeting - English",
                "message": "hello",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    len(text) > 0,
                    f"Got greeting response: {text[:50] if text else '(empty)'}..."
                )
            },
            {
                "name": "Greeting - Hi",
                "message": "hi",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    len(text) > 0,
                    f"Got greeting response: {text[:50] if text else '(empty)'}..."
                )
            },

            # =================================================================
            # File Read Rules - Direct Read tool invocation
            # =================================================================
            {
                "name": "Read File - Chinese",
                "message": "读取 pyproject.toml",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    "nimbus" in text.lower() or "[project]" in text.lower() or len(events) > 2,
                    f"Got file content, found project info: {'nimbus' in text.lower()}"
                )
            },
            {
                "name": "Read File - English",
                "message": "read pyproject.toml",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    "nimbus" in text.lower() or "[project]" in text.lower() or len(events) > 2,
                    f"Got file content, found project info: {'nimbus' in text.lower()}"
                )
            },

            # =================================================================
            # List Files Rules - Direct Bash tool invocation
            # =================================================================
            {
                "name": "List Files - Chinese",
                "message": "列出 src 目录的文件",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    len(text) > 0 or len(events) > 2,
                    f"Got {len(events)} events, response: {len(text)} chars"
                )
            },
            {
                "name": "List Files - English",
                "message": "list files in src",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    len(text) > 0 or len(events) > 2,
                    f"Got {len(events)} events, response: {len(text)} chars"
                )
            },

            # =================================================================
            # Search Rules - Direct Bash tool invocation
            # =================================================================
            {
                "name": "Search Code - Chinese",
                "message": "搜索 def create_plan",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    len(text) > 0 or len(events) > 2,
                    f"Got {len(events)} events, response: {len(text)} chars"
                )
            },
            {
                "name": "Search Code - English",
                "message": "search for def create_plan",
                "expect_fast_path": True,
                "success_criteria": lambda events, text: (
                    len(text) > 0 or len(events) > 2,
                    f"Got {len(events)} events, response: {len(text)} chars"
                )
            },
        ]

        # Run test cases
        for tc in test_cases:
            await self.run_test_case(
                name=tc["name"],
                message=tc["message"],
                success_criteria=tc.get("success_criteria"),
                expect_fast_path=tc.get("expect_fast_path", True),
                timeout=tc.get("timeout", 30.0)
            )
            # Brief pause between tests
            await asyncio.sleep(0.5)

        # Print summary
        self.print_summary()

        # Return overall success
        return all(r.passed for r in self.results)

    def print_summary(self):
        """Print test summary with performance analysis."""
        self.print_header("Test Summary")

        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        total_time_ms = sum(r.duration_ms for r in self.results)

        fast_path_count = sum(1 for r in self.results if r.is_fast_path)
        avg_fast_path_ms = 0.0
        fast_path_times = [r.duration_ms for r in self.results if r.is_fast_path]
        if fast_path_times:
            avg_fast_path_ms = sum(fast_path_times) / len(fast_path_times)

        print("\n[Results]")
        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            fast_indicator = " [FAST]" if result.is_fast_path else ""
            print(f"  [{status}] {result.name} ({result.duration_ms:.0f}ms){fast_indicator}")
            if not result.passed:
                print(f"         {result.message}")

        print("\n[Performance Summary]")
        print(f"  Fast path tests: {fast_path_count}/{len(self.results)}")
        if avg_fast_path_ms > 0:
            print(f"  Average fast path time: {avg_fast_path_ms:.0f}ms")
        print(f"  Total time: {total_time_ms:.0f}ms")

        print("\n[Overall]")
        print(f"  Total: {len(self.results)} tests, {passed} passed, {failed} failed")

        if failed == 0:
            print("\n[ALL TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")


async def main():
    """Main entry point."""
    tester = RulePlannerE2ETest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
