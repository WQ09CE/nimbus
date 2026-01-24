#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Readonly Agent Capabilities

This script tests the Nimbus Server's complete message flow for readonly agent
capabilities (code exploration) without requiring a UI.

Test Cases:
1. List Python files in current directory
2. Read pyproject.toml content
3. Search for 'def create_plan' definition in code

API Endpoints:
- GET /health - Health check
- POST /session - Create session
- POST /session/{session_id}/message - Send message (SSE stream)

Usage:
    python tests/e2e_readonly_agent.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_readonly_agent.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Optional


# Check for httpx
try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# Configuration
SERVER_URL = os.environ.get("NIMBUS_SERVER_URL", "http://127.0.0.1:8080")


@dataclass
class SSEEvent:
    """Represents a Server-Sent Event."""
    event: str
    data: dict[str, Any]
    raw_data: str = ""


@dataclass
class TestResult:
    """Represents the result of a test case."""
    name: str
    passed: bool
    message: str
    duration_ms: float
    events: list[SSEEvent]


class SSEParser:
    """Parser for Server-Sent Events stream."""

    def __init__(self):
        self.current_event: Optional[str] = None
        self.current_data: str = ""
        self.events: list[SSEEvent] = []

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


class NimbusE2ETest:
    """E2E test runner for Nimbus Server readonly agent capabilities."""

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

    def print_event(self, event: SSEEvent, max_len: int = 100):
        """Print an SSE event."""
        data_str = json.dumps(event.data)
        if len(data_str) > max_len:
            data_str = data_str[:max_len] + "..."
        print(f"  [SSE:{event.event}] {data_str}")

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
    ) -> tuple[list[SSEEvent], str]:
        """
        Send a message and collect SSE events.

        Returns:
            Tuple of (events, final_response_text)
        """
        if not self.session_id:
            raise ValueError("No session created")

        url = f"{self.server_url}/session/{self.session_id}/message"

        # Try both request formats
        request_body = {
            "parts": [{"type": "text", "text": message}]
        }

        parser = SSEParser()
        response_text = ""

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                url,
                json=request_body,
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(timeout, connect=10.0)
            ) as response:
                if response.status_code != 200:
                    # Try alternative format
                    pass  # For now, continue with current response

                async for line in response.aiter_lines():
                    event = parser.feed_line(line)
                    if event:
                        self.print_event(event)

                        # Collect response text from content.delta events
                        if event.event == "content.delta":
                            text = event.data.get("text", "")
                            response_text += text

        return parser.events, response_text

    async def run_test_case(
        self,
        name: str,
        message: str,
        success_criteria: Optional[callable] = None,
        timeout: float = 120.0
    ) -> TestResult:
        """
        Run a single test case.

        Args:
            name: Test case name
            message: Message to send
            success_criteria: Optional function(events, response_text) -> (bool, message)
            timeout: Timeout in seconds
        """
        self.print_header(f"Test: {name}")
        self.print_info(f"Message: {message}")
        print()

        start_time = time.time()

        try:
            events, response_text = await self.send_message(message, timeout)
            duration_ms = (time.time() - start_time) * 1000

            # Default success criteria: received event.done or content.done
            if success_criteria:
                passed, msg = success_criteria(events, response_text)
            else:
                done_events = [e for e in events if e.event in ("event.done", "content.done", "done")]
                passed = len(done_events) > 0 or len(response_text) > 0
                msg = f"Received {len(events)} events, response length: {len(response_text)}"

            result = TestResult(
                name=name,
                passed=passed,
                message=msg,
                duration_ms=duration_ms,
                events=events
            )

            if passed:
                self.print_ok(f"{msg} ({duration_ms:.0f}ms)")
            else:
                self.print_fail(f"{msg} ({duration_ms:.0f}ms)")

            # Print response excerpt
            if response_text:
                excerpt = response_text[:500]
                if len(response_text) > 500:
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
        """Run all E2E tests."""
        self.print_header("Nimbus E2E Test - Readonly Agent")
        self.print_info(f"Server: {self.server_url}")
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

        # Test cases for readonly agent capabilities
        test_cases = [
            {
                "name": "List Python Files",
                "message": "List the Python files in the current directory",
                "success_criteria": lambda events, text: (
                    len(text) > 0 or len(events) > 2,
                    f"Got {len(events)} events, {len(text)} chars response"
                )
            },
            {
                "name": "Read pyproject.toml",
                "message": "Read the content of pyproject.toml file",
                "success_criteria": lambda events, text: (
                    "nimbus" in text.lower() or len(events) > 2,
                    f"Got {len(events)} events, found project info: {'nimbus' in text.lower()}"
                )
            },
            {
                "name": "Search Code Definition",
                "message": "Search for the definition of 'def create_plan' in the codebase",
                "success_criteria": lambda events, text: (
                    len(text) > 0 or len(events) > 2,
                    f"Got {len(events)} events, {len(text)} chars response"
                )
            },
        ]

        # Run test cases
        for tc in test_cases:
            await self.run_test_case(
                name=tc["name"],
                message=tc["message"],
                success_criteria=tc.get("success_criteria"),
                timeout=tc.get("timeout", 120.0)
            )
            # Brief pause between tests
            await asyncio.sleep(1)

        # Print summary
        self.print_summary()

        # Return overall success
        return all(r.passed for r in self.results)

    def print_summary(self):
        """Print test summary."""
        self.print_header("Test Summary")

        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed
        total_time_ms = sum(r.duration_ms for r in self.results)

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            print(f"  [{status}] {result.name} ({result.duration_ms:.0f}ms)")
            if not result.passed:
                print(f"         {result.message}")

        print()
        print(f"Total: {len(self.results)} tests, {passed} passed, {failed} failed")
        print(f"Total time: {total_time_ms:.0f}ms")

        if failed == 0:
            print("\n[ALL TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")


async def main():
    """Main entry point."""
    tester = NimbusE2ETest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
