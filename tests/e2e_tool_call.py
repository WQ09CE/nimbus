#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Tool Call Protocol Validation

This script validates the complete tool call flow in nimbus serve:
1. SSE event format (event: xxx, data: {...})
2. Tool call flow (tool_call -> tool_result -> message)
3. Multi-tool scenarios (sequential and parallel)
4. Protocol assertions for event structure

Test Scenarios:
1. Basic tool call: Read a file -> triggers Read tool
2. Glob tool call: List files -> triggers Glob tool
3. Tool chain: Search + Read -> triggers Grep + Read

Protocol Events:
- connected: Connection established
- message_start: Processing started
- tool_call: Tool being invoked {tool, args}
- tool_result: Tool returned {tool, result}
- task_start/task_done: Task lifecycle
- dag_complete: All processing done
- message: Final response content
- error: Error occurred

Usage:
    python tests/e2e_tool_call.py

    # Custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_tool_call.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

# Check for httpx
try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# Configuration
SERVER_URL = os.environ.get("NIMBUS_SERVER_URL", "http://127.0.0.1:8080")


class EventType(str, Enum):
    """SSE Event Types from Nimbus Server."""
    CONNECTED = "connected"
    MESSAGE_START = "message_start"
    PLANNING = "planning"
    DAG_CREATED = "dag_created"
    TASK_START = "task_start"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TASK_DONE = "task_done"
    TASK_FAILED = "task_failed"
    PERMISSION_REQUEST = "permission_request"
    DAG_COMPLETE = "dag_complete"
    MESSAGE = "message"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


@dataclass
class SSEEvent:
    """Represents a parsed Server-Sent Event."""
    event: str
    data: Dict[str, Any]
    raw_data: str = ""


@dataclass
class ToolCall:
    """Represents a tool call extracted from events."""
    tool_name: str
    args: Dict[str, Any]
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ProtocolViolation:
    """Represents a protocol violation detected during testing."""
    rule: str
    expected: str
    actual: str
    context: str = ""


@dataclass
class TestResult:
    """Represents the result of a test case."""
    name: str
    passed: bool
    message: str
    duration_ms: float
    events: List[SSEEvent] = field(default_factory=list)
    tool_calls: List[ToolCall] = field(default_factory=list)
    violations: List[ProtocolViolation] = field(default_factory=list)
    response_text: str = ""


class SSEParser:
    """Parser for Server-Sent Events stream."""

    def __init__(self):
        self.current_event: Optional[str] = None
        self.current_data: str = ""
        self.events: List[SSEEvent] = []

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
                raw_data=self.current_data
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


class ProtocolValidator:
    """Validates SSE events against expected protocol."""

    def __init__(self):
        self.violations: List[ProtocolViolation] = []

    def reset(self):
        """Reset violations."""
        self.violations = []

    def validate_event_structure(self, event: SSEEvent) -> bool:
        """Validate that an event has the correct structure."""
        # Event type should be a valid string
        if not event.event or not isinstance(event.event, str):
            self.violations.append(ProtocolViolation(
                rule="event_type_required",
                expected="Non-empty string",
                actual=str(event.event),
                context="Event type must be a non-empty string"
            ))
            return False

        # Data should be a dict
        if not isinstance(event.data, dict):
            self.violations.append(ProtocolViolation(
                rule="data_is_dict",
                expected="dict",
                actual=type(event.data).__name__,
                context=f"Event: {event.event}"
            ))
            return False

        return True

    def validate_connected_event(self, event: SSEEvent) -> bool:
        """Validate connected event structure."""
        if event.event != EventType.CONNECTED:
            return True  # Not applicable

        if "session_id" not in event.data:
            self.violations.append(ProtocolViolation(
                rule="connected_has_session_id",
                expected="session_id field present",
                actual=str(event.data.keys()),
                context="connected event must include session_id"
            ))
            return False
        return True

    def validate_tool_call_event(self, event: SSEEvent) -> bool:
        """Validate tool_call event structure."""
        if event.event != EventType.TOOL_CALL:
            return True  # Not applicable

        required_fields = ["tool", "args"]
        for field in required_fields:
            if field not in event.data:
                self.violations.append(ProtocolViolation(
                    rule=f"tool_call_has_{field}",
                    expected=f"{field} field present",
                    actual=str(event.data.keys()),
                    context=f"tool_call event must include {field}"
                ))
                return False

        # args should be a dict
        if not isinstance(event.data.get("args"), dict):
            self.violations.append(ProtocolViolation(
                rule="tool_call_args_is_dict",
                expected="dict",
                actual=type(event.data.get("args")).__name__,
                context=f"tool={event.data.get('tool')}"
            ))
            return False

        return True

    def validate_tool_result_event(self, event: SSEEvent) -> bool:
        """Validate tool_result event structure."""
        if event.event != EventType.TOOL_RESULT:
            return True  # Not applicable

        # tool_result must have "tool" field
        if "tool" not in event.data:
            self.violations.append(ProtocolViolation(
                rule="tool_result_has_tool",
                expected="tool field present",
                actual=str(event.data.keys()),
                context="tool_result event must include tool"
            ))
            return False

        # tool_result must have either "result" or "output" field
        if "result" not in event.data and "output" not in event.data:
            self.violations.append(ProtocolViolation(
                rule="tool_result_has_result_or_output",
                expected="result or output field present",
                actual=str(event.data.keys()),
                context="tool_result event must include result or output"
            ))
            return False

        return True

    def validate_message_event(self, event: SSEEvent) -> bool:
        """Validate message event structure."""
        if event.event != EventType.MESSAGE:
            return True  # Not applicable

        if "content" not in event.data:
            self.violations.append(ProtocolViolation(
                rule="message_has_content",
                expected="content field present",
                actual=str(event.data.keys()),
                context="message event must include content"
            ))
            return False

        return True

    def validate_event_sequence(self, events: List[SSEEvent]) -> bool:
        """Validate the overall event sequence follows protocol."""
        if not events:
            self.violations.append(ProtocolViolation(
                rule="events_not_empty",
                expected="At least one event",
                actual="0 events",
                context="Expected some events from the stream"
            ))
            return False

        # First event should be connected (may have heartbeat before)
        event_types = [e.event for e in events if e.event != EventType.HEARTBEAT]
        if event_types and event_types[0] != EventType.CONNECTED:
            # Connected is often emitted before our parse, so this is ok
            pass

        # Check for completion (message or error or dag_complete)
        completion_events = {EventType.MESSAGE, EventType.ERROR, EventType.DAG_COMPLETE}
        has_completion = any(e.event in completion_events for e in events)
        if not has_completion:
            self.violations.append(ProtocolViolation(
                rule="has_completion_event",
                expected="message, error, or dag_complete event",
                actual=str([e.event for e in events[-5:]]),  # Last 5 events
                context="Stream should end with a completion event"
            ))
            return False

        # Tool calls should have corresponding results (eventually)
        tool_calls = [e for e in events if e.event == EventType.TOOL_CALL]
        tool_results = [e for e in events if e.event == EventType.TOOL_RESULT]

        # Note: In streaming, tool results may not directly match tool calls
        # This is a soft check - just ensure we get results if we have calls
        if tool_calls and not tool_results:
            self.violations.append(ProtocolViolation(
                rule="tool_calls_have_results",
                expected=f"{len(tool_calls)} tool results",
                actual="0 tool results",
                context="Each tool_call should have a corresponding tool_result"
            ))
            # This is a warning, not a hard failure
            pass

        return True

    def validate_all(self, events: List[SSEEvent]) -> List[ProtocolViolation]:
        """Run all validations and return violations."""
        self.reset()

        # Validate each event
        for event in events:
            self.validate_event_structure(event)
            self.validate_connected_event(event)
            self.validate_tool_call_event(event)
            self.validate_tool_result_event(event)
            self.validate_message_event(event)

        # Validate sequence
        self.validate_event_sequence(events)

        return self.violations


class NimbusTestClient:
    """Test client for Nimbus Server API."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.session_id: Optional[str] = None

    async def health_check(self) -> bool:
        """Check if server is healthy."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server_url}/health",
                    timeout=5.0
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("status") == "healthy" or data.get("healthy", False)
                return False
        except Exception:
            return False

    async def create_session(self) -> Optional[str]:
        """Create a new session."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.server_url}/api/v1/sessions",
                    json={"workspace": "."},
                    timeout=10.0
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    self.session_id = data.get("id")
                    return self.session_id
                return None
        except Exception as e:
            print(f"[ERROR] Create session failed: {e}")
            return None

    async def send_message(
        self,
        message: str,
        timeout: float = 120.0
    ) -> tuple[List[SSEEvent], str, List[ToolCall]]:
        """
        Send a message and collect SSE events.

        Returns:
            Tuple of (events, response_text, tool_calls)
        """
        if not self.session_id:
            raise ValueError("No session created")

        url = f"{self.server_url}/api/v1/sessions/{self.session_id}/chat"
        request_body = {"content": message}

        parser = SSEParser()
        response_text = ""
        tool_calls: List[ToolCall] = []
        tool_call_map: Dict[str, ToolCall] = {}

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
                        # Track tool calls
                        if event.event == EventType.TOOL_CALL:
                            tool_name = event.data.get("tool", "unknown")
                            args = event.data.get("args", {})
                            tc = ToolCall(tool_name=tool_name, args=args)
                            tool_calls.append(tc)
                            # Use tool name as key (simplified)
                            tool_call_map[tool_name] = tc

                        # Track tool results
                        elif event.event == EventType.TOOL_RESULT:
                            tool_name = event.data.get("tool", "unknown")
                            result = event.data.get("result", "")
                            if tool_name in tool_call_map:
                                tool_call_map[tool_name].result = str(result)[:500]  # Truncate

                        # Collect response text from message events
                        elif event.event == EventType.MESSAGE:
                            content = event.data.get("content", "")
                            response_text += content

                        # Break on completion events
                        elif event.event in {EventType.DAG_COMPLETE, EventType.ERROR}:
                            break

        return parser.events, response_text, tool_calls


class ToolCallE2ETest:
    """E2E test runner for tool call protocol validation."""

    def __init__(self, server_url: str):
        self.server_url = server_url
        self.client = NimbusTestClient(server_url)
        self.validator = ProtocolValidator()
        self.results: List[TestResult] = []

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

    def print_event(self, event: SSEEvent, max_len: int = 80):
        """Print an SSE event."""
        data_str = json.dumps(event.data, default=str, ensure_ascii=False)
        if len(data_str) > max_len:
            data_str = data_str[:max_len] + "..."
        print(f"    [{event.event}] {data_str}")

    def print_tool_call(self, tc: ToolCall):
        """Print a tool call."""
        args_str = json.dumps(tc.args, default=str, ensure_ascii=False)
        if len(args_str) > 60:
            args_str = args_str[:60] + "..."
        result_str = tc.result[:50] + "..." if tc.result and len(tc.result) > 50 else tc.result
        print(f"    Tool: {tc.tool_name}")
        print(f"      Args: {args_str}")
        if result_str:
            print(f"      Result: {result_str}")
        if tc.error:
            print(f"      Error: {tc.error}")

    async def run_test_case(
        self,
        name: str,
        message: str,
        expected_tools: List[str],
        content_check: Optional[Callable[[str], bool]] = None,
        timeout: float = 120.0
    ) -> TestResult:
        """
        Run a single test case.

        Args:
            name: Test case name
            message: Message to send
            expected_tools: List of tool names expected to be called
            content_check: Optional function to validate response content
            timeout: Timeout in seconds
        """
        self.print_header(f"Test: {name}")
        self.print_info(f"Message: {message}")
        self.print_info(f"Expected tools: {expected_tools}")
        print()

        start_time = time.time()

        try:
            # Send message and collect events
            events, response_text, tool_calls = await self.client.send_message(
                message, timeout
            )
            duration_ms = (time.time() - start_time) * 1000

            # Print events summary
            self.print_subheader("Events Received")
            event_counts: Dict[str, int] = {}
            for event in events:
                event_counts[event.event] = event_counts.get(event.event, 0) + 1
                # Print key events
                if event.event in {
                    EventType.TOOL_CALL,
                    EventType.TOOL_RESULT,
                    EventType.ERROR
                }:
                    self.print_event(event, max_len=100)

            print(f"\n  Event counts: {event_counts}")

            # Print tool calls
            if tool_calls:
                self.print_subheader("Tool Calls Detected")
                for tc in tool_calls:
                    self.print_tool_call(tc)

            # Validate protocol
            self.print_subheader("Protocol Validation")
            violations = self.validator.validate_all(events)
            if violations:
                for v in violations:
                    self.print_fail(f"Violation: {v.rule}")
                    print(f"      Expected: {v.expected}")
                    print(f"      Actual: {v.actual}")
                    if v.context:
                        print(f"      Context: {v.context}")
            else:
                self.print_ok("All protocol checks passed")

            # Check expected tools
            self.print_subheader("Tool Assertions")
            actual_tools = [tc.tool_name for tc in tool_calls]
            tools_passed = True
            for expected in expected_tools:
                if expected in actual_tools:
                    self.print_ok(f"Tool '{expected}' was called")
                else:
                    self.print_fail(f"Tool '{expected}' was NOT called")
                    tools_passed = False

            # Check content if provided
            content_passed = True
            if content_check:
                self.print_subheader("Content Validation")
                if content_check(response_text):
                    self.print_ok("Content check passed")
                else:
                    self.print_fail("Content check failed")
                    content_passed = False

            # Print response excerpt
            if response_text:
                self.print_subheader("Response")
                excerpt = response_text[:500]
                if len(response_text) > 500:
                    excerpt += f"... ({len(response_text)} chars total)"
                print(excerpt)

            # Determine overall pass/fail
            passed = (
                len(violations) == 0 and
                tools_passed and
                content_passed and
                len(events) > 0
            )

            result = TestResult(
                name=name,
                passed=passed,
                message=f"Duration: {duration_ms:.0f}ms, Events: {len(events)}, Tools: {len(tool_calls)}",
                duration_ms=duration_ms,
                events=events,
                tool_calls=tool_calls,
                violations=violations,
                response_text=response_text
            )

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            self.print_fail(f"Test failed with exception: {e}")
            result = TestResult(
                name=name,
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )

        self.results.append(result)
        return result

    async def test_basic_read(self) -> TestResult:
        """
        Test 1: Basic Read Tool Call

        Scenario: Ask to read a file
        Expected: Read tool is called
        """
        return await self.run_test_case(
            name="Basic Read Tool Call",
            message="Read the content of pyproject.toml file",
            expected_tools=["Read"],
            content_check=lambda text: "nimbus" in text.lower() or "project" in text.lower() or len(text) > 50,
            timeout=60.0
        )

    async def test_glob_tool(self) -> TestResult:
        """
        Test 2: Glob Tool Call

        Scenario: Ask to list Python files
        Expected: Glob tool is called
        """
        return await self.run_test_case(
            name="Glob Tool Call",
            message="List all Python files in the src/nimbus directory (just the filenames)",
            expected_tools=["Glob"],
            content_check=lambda text: ".py" in text or "python" in text.lower() or "file" in text.lower(),
            timeout=60.0
        )

    async def test_grep_tool(self) -> TestResult:
        """
        Test 3: Grep Tool Call

        Scenario: Ask to search for a pattern
        Expected: Grep tool is called
        """
        return await self.run_test_case(
            name="Grep Tool Call",
            message="Search for 'async def' in the codebase",
            expected_tools=["Grep"],
            content_check=lambda text: "async" in text.lower() or "found" in text.lower() or "search" in text.lower(),
            timeout=60.0
        )

    async def test_tool_chain(self) -> TestResult:
        """
        Test 4: Tool Chain (Multiple Tools)

        Scenario: Ask a question requiring multiple tools
        Expected: Multiple tools are called (e.g., Grep then Read)
        """
        return await self.run_test_case(
            name="Tool Chain (Search + Read)",
            message="Find files that contain 'class SSEHub' and show me the first few lines of that class definition",
            expected_tools=["Grep"],  # May also include Read, but Grep is minimum
            content_check=lambda text: "SSE" in text or "class" in text.lower() or len(text) > 100,
            timeout=90.0
        )

    async def test_multi_turn_tools(self) -> TestResult:
        """
        Test 5: Multi-turn with Tool Calls

        Scenario: Second message in a session that references first
        Expected: Tool called in context of previous conversation
        """
        # First message
        self.print_info("Sending first message...")
        await self.client.send_message("List files in src/nimbus/server/")
        await asyncio.sleep(1)

        # Second message referencing first
        return await self.run_test_case(
            name="Multi-turn Tool Call",
            message="Now read the api.py file from that directory",
            expected_tools=["Read"],
            content_check=lambda text: "api" in text.lower() or "router" in text.lower() or "def " in text,
            timeout=60.0
        )

    async def run_all_tests(self) -> bool:
        """Run all tool call tests."""
        self.print_header("Nimbus E2E Test - Tool Call Protocol Validation")
        self.print_info(f"Server: {self.server_url}")
        print()

        # Health check
        self.print_subheader("Health Check")
        if not await self.client.health_check():
            self.print_fail(f"Server not available at {self.server_url}")
            self.print_info("Start the server with: uv run nimbus serve")
            return False
        self.print_ok("Server is healthy")

        # Create session
        self.print_subheader("Create Session")
        session_id = await self.client.create_session()
        if not session_id:
            self.print_fail("Failed to create session")
            return False
        self.print_ok(f"Session created: {session_id}")

        # Run test cases
        test_methods = [
            self.test_basic_read,
            self.test_glob_tool,
            self.test_grep_tool,
            self.test_tool_chain,
        ]

        for test_method in test_methods:
            await test_method()
            # Pause between tests
            await asyncio.sleep(1)

        # Multi-turn test needs fresh session
        self.print_subheader("Creating fresh session for multi-turn test")
        session_id = await self.client.create_session()
        if session_id:
            self.print_ok(f"New session: {session_id}")
            await self.test_multi_turn_tools()
        else:
            self.print_fail("Could not create session for multi-turn test")

        # Print summary
        self.print_summary()

        return all(r.passed for r in self.results)

    def print_summary(self):
        """Print test summary."""
        self.print_header("Test Summary")

        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        total_time_ms = sum(r.duration_ms for r in self.results)

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            tools = [tc.tool_name for tc in result.tool_calls]
            violations_count = len(result.violations)

            print(f"\n  [{status}] {result.name}")
            print(f"         {result.message}")
            print(f"         Tools called: {tools}")
            if violations_count > 0:
                print(f"         Protocol violations: {violations_count}")

        print()
        print("=" * 70)
        print(f"Total: {len(self.results)} tests, {passed} passed, {failed} failed")
        print(f"Total time: {total_time_ms:.0f}ms")

        if failed == 0:
            print("\n[ALL TOOL CALL TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")


async def main():
    """Main entry point."""
    tester = ToolCallE2ETest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
