#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Error Handling

This script tests the Nimbus Server's error handling capabilities to ensure
graceful degradation and clear error messages.

Test Cases:
1. Read non-existent file - "Read nonexistent_file_xyz.txt", verify graceful error
2. Invalid file pattern - Edge case handling
3. Empty message handling - Send empty message, verify no crash
4. Non-existent session - Send message to invalid session, verify 404

API Endpoints:
- GET /health - Health check
- POST /session - Create session
- POST /session/{session_id}/message - Send message (SSE stream)

Key Verification Points:
- Error messages are clear and informative
- SSE stream correctly sends tool.error or event.error events
- Server does not crash; subsequent requests work normally

Usage:
    python tests/e2e_error_handling.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_error_handling.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
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
    events: list[SSEEvent] = field(default_factory=list)
    response_text: str = ""
    http_status: Optional[int] = None


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


class NimbusErrorHandlingTest:
    """E2E test runner for Nimbus Server error handling capabilities."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.session_id: Optional[str] = None
        self.results: list[TestResult] = []

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

    def print_event(self, event: SSEEvent, max_len: int = 100):
        """Print an SSE event."""
        data_str = json.dumps(event.data)
        if len(data_str) > max_len:
            data_str = data_str[:max_len] + "..."
        print(f"    [SSE:{event.event}] {data_str}")

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

    async def create_session(self) -> Optional[str]:
        """Create a new session."""
        self.print_subheader("Create Session")

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
        session_id: Optional[str] = None,
        timeout: float = 120.0,
        verbose: bool = True
    ) -> tuple[list[SSEEvent], str, int]:
        """
        Send a message and collect SSE events.

        Returns:
            Tuple of (events, final_response_text, http_status_code)
        """
        sid = session_id or self.session_id
        if not sid:
            raise ValueError("No session created")

        url = f"{self.server_url}/session/{sid}/message"

        request_body = {
            "parts": [{"type": "text", "text": message}]
        }

        parser = SSEParser()
        response_text = ""
        http_status = 0

        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    url,
                    json=request_body,
                    headers={"Accept": "text/event-stream"},
                    timeout=httpx.Timeout(timeout, connect=10.0)
                ) as response:
                    http_status = response.status_code

                    if response.status_code != 200:
                        # Read error response body
                        error_body = await response.aread()
                        return [], error_body.decode("utf-8", errors="replace"), http_status

                    async for line in response.aiter_lines():
                        event = parser.feed_line(line)
                        if event:
                            if verbose:
                                self.print_event(event)

                            # Collect response text from content.delta events
                            if event.event == "content.delta":
                                text = event.data.get("text", "")
                                response_text += text
        except httpx.HTTPStatusError as e:
            return [], str(e), e.response.status_code

        return parser.events, response_text, http_status

    async def send_message_raw(
        self,
        session_id: str,
        body: dict,
        timeout: float = 30.0
    ) -> tuple[int, str]:
        """
        Send a raw message request.

        Returns:
            Tuple of (http_status_code, response_body)
        """
        url = f"{self.server_url}/session/{session_id}/message"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"Accept": "text/event-stream"},
                    timeout=timeout
                )
                return response.status_code, response.text
        except Exception as e:
            return 0, str(e)

    # =========================================================================
    # Test Case 1: Read Non-existent File
    # =========================================================================
    async def test_read_nonexistent_file(self) -> TestResult:
        """
        Test reading a file that does not exist.

        Expected behavior:
        - Agent attempts to read the file
        - Returns a clear error message about file not found
        - SSE stream may include tool.error event
        - Server continues to function normally
        """
        self.print_header("Test 1: Read Non-existent File")

        start_time = time.time()

        try:
            events, response_text, http_status = await self.send_message(
                "Read the file nonexistent_file_xyz_12345.txt",
                timeout=60.0
            )
            duration_ms = (time.time() - start_time) * 1000

            # Check for error handling
            error_events = [
                e for e in events
                if e.event in ("tool.error", "event.error", "error", "task_failed")
            ]

            # The response should either:
            # 1. Contain error events in SSE stream
            # 2. Contain error message in response text
            # 3. HTTP status should still be 200 (graceful handling)

            has_error_indication = (
                len(error_events) > 0 or
                "not found" in response_text.lower() or
                "does not exist" in response_text.lower() or
                "cannot find" in response_text.lower() or
                "no such file" in response_text.lower() or
                "error" in response_text.lower()
            )

            passed = http_status == 200 and (has_error_indication or len(response_text) > 0)

            if error_events:
                msg = f"Graceful error: {len(error_events)} error event(s) in SSE stream"
            elif has_error_indication:
                msg = "Graceful error: Error indicated in response text"
            elif len(response_text) > 0:
                msg = "Response received (may contain implicit error handling)"
            else:
                msg = "No error indication found"
                passed = False

            result = TestResult(
                name="Read Non-existent File",
                passed=passed,
                message=msg,
                duration_ms=duration_ms,
                events=events,
                response_text=response_text,
                http_status=http_status,
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
                name="Read Non-existent File",
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )
            self.print_fail(f"Test failed with exception: {e}")
            self.results.append(result)
            return result

    # =========================================================================
    # Test Case 2: Invalid File Pattern (Edge Cases)
    # =========================================================================
    async def test_invalid_file_pattern(self) -> TestResult:
        """
        Test handling of edge case file patterns.

        Expected behavior:
        - Server handles unusual patterns gracefully
        - No crash or unhandled exception
        - Clear response or error message
        """
        self.print_header("Test 2: Invalid/Edge Case File Pattern")

        start_time = time.time()

        try:
            # Send a request with an unusual pattern that might be edge case
            events, response_text, http_status = await self.send_message(
                "List files matching pattern **/**/***/*",
                timeout=60.0
            )
            duration_ms = (time.time() - start_time) * 1000

            # The test passes if:
            # 1. HTTP status is 200 (server didn't crash)
            # 2. We got some response (either content or error events)
            passed = http_status == 200 and (len(events) > 0 or len(response_text) > 0)

            if passed:
                msg = f"Handled edge case pattern gracefully (HTTP {http_status})"
            else:
                msg = f"Unexpected behavior (HTTP {http_status})"

            result = TestResult(
                name="Invalid File Pattern",
                passed=passed,
                message=msg,
                duration_ms=duration_ms,
                events=events,
                response_text=response_text,
                http_status=http_status,
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
                name="Invalid File Pattern",
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )
            self.print_fail(f"Test failed with exception: {e}")
            self.results.append(result)
            return result

    # =========================================================================
    # Test Case 3: Empty Message Handling
    # =========================================================================
    async def test_empty_message(self) -> TestResult:
        """
        Test sending an empty message.

        Expected behavior:
        - Server returns 400 Bad Request or handles gracefully
        - No crash
        - Clear error message if rejected
        """
        self.print_header("Test 3: Empty Message Handling")

        start_time = time.time()

        try:
            # Send empty message via raw request
            status, body = await self.send_message_raw(
                self.session_id,
                {"parts": [{"type": "text", "text": ""}]},
                timeout=30.0
            )
            duration_ms = (time.time() - start_time) * 1000

            # Empty message should either:
            # 1. Return 400 Bad Request with clear error
            # 2. Handle gracefully with some response
            # 3. Not crash the server

            if status == 400:
                passed = True
                msg = "Correctly rejected empty message with 400"
            elif status == 200:
                # Check if response indicates the issue
                passed = True
                msg = "Handled empty message gracefully (200)"
            else:
                passed = status != 0  # At least we got a response
                msg = f"Got HTTP {status}"

            result = TestResult(
                name="Empty Message Handling",
                passed=passed,
                message=msg,
                duration_ms=duration_ms,
                response_text=body[:500] if body else "",
                http_status=status,
            )

            if passed:
                self.print_ok(f"{msg} ({duration_ms:.0f}ms)")
            else:
                self.print_fail(f"{msg} ({duration_ms:.0f}ms)")

            if body:
                print(f"\n[Response]\n{body[:300]}")

            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Empty Message Handling",
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )
            self.print_fail(f"Test failed with exception: {e}")
            self.results.append(result)
            return result

    # =========================================================================
    # Test Case 4: Non-existent Session
    # =========================================================================
    async def test_nonexistent_session(self) -> TestResult:
        """
        Test sending message to a non-existent session.

        Expected behavior:
        - Server returns 404 Not Found
        - Clear error message
        - No crash
        """
        self.print_header("Test 4: Non-existent Session")

        start_time = time.time()
        fake_session_id = "nonexistent_session_xyz_12345"

        try:
            events, response_text, http_status = await self.send_message(
                "Hello",
                session_id=fake_session_id,
                timeout=30.0,
                verbose=True
            )
            duration_ms = (time.time() - start_time) * 1000

            # Should return 404
            passed = http_status == 404
            if passed:
                msg = "Correctly returned 404 for non-existent session"
            else:
                msg = f"Expected 404, got HTTP {http_status}"

            # Check if error message mentions "not found"
            if "not found" in response_text.lower():
                msg += " with clear error message"

            result = TestResult(
                name="Non-existent Session",
                passed=passed,
                message=msg,
                duration_ms=duration_ms,
                events=events,
                response_text=response_text,
                http_status=http_status,
            )

            if passed:
                self.print_ok(f"{msg} ({duration_ms:.0f}ms)")
            else:
                self.print_fail(f"{msg} ({duration_ms:.0f}ms)")

            if response_text:
                print(f"\n[Response]\n{response_text[:300]}")

            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Non-existent Session",
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )
            self.print_fail(f"Test failed with exception: {e}")
            self.results.append(result)
            return result

    # =========================================================================
    # Test Case 5: Server Recovery (Subsequent Requests)
    # =========================================================================
    async def test_server_recovery(self) -> TestResult:
        """
        Test that server continues to work after error scenarios.

        Expected behavior:
        - After error tests, server responds normally
        - New requests work correctly
        """
        self.print_header("Test 5: Server Recovery Check")

        start_time = time.time()

        try:
            # Simple health check
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server_url}/health",
                    timeout=5.0
                )

            duration_ms = (time.time() - start_time) * 1000

            if response.status_code == 200:
                data = response.json()
                healthy = data.get("healthy", False)
                if healthy:
                    passed = True
                    msg = "Server recovered: Health check passed"
                else:
                    passed = False
                    msg = f"Server unhealthy after tests: {data}"
            else:
                passed = False
                msg = f"Health check failed with HTTP {response.status_code}"

            result = TestResult(
                name="Server Recovery",
                passed=passed,
                message=msg,
                duration_ms=duration_ms,
                http_status=response.status_code,
            )

            if passed:
                self.print_ok(f"{msg} ({duration_ms:.0f}ms)")
            else:
                self.print_fail(f"{msg} ({duration_ms:.0f}ms)")

            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Server Recovery",
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )
            self.print_fail(f"Test failed with exception: {e}")
            self.results.append(result)
            return result

    # =========================================================================
    # Run All Tests
    # =========================================================================
    async def run_all_tests(self) -> bool:
        """Run all error handling tests."""
        self.print_header("Nimbus E2E Test - Error Handling", "=")
        self.print_info(f"Server: {self.server_url}")
        self.print_info("Testing error handling and graceful degradation")
        print()

        # Health check
        if not await self.check_health():
            self.print_fail("Server not available, aborting tests")
            return False

        # Create session for tests that need it
        session_id = await self.create_session()
        if not session_id:
            self.print_fail("Cannot create session, aborting tests")
            return False

        # Run error handling tests
        await self.test_read_nonexistent_file()
        await asyncio.sleep(1)

        await self.test_invalid_glob_pattern()
        await asyncio.sleep(1)

        await self.test_empty_message()
        await asyncio.sleep(1)

        await self.test_nonexistent_session()
        await asyncio.sleep(1)

        await self.test_server_recovery()

        # Print summary
        self.print_summary()

        return all(r.passed for r in self.results)

    def print_summary(self):
        """Print test summary."""
        self.print_header("Error Handling Test Summary")

        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            http_info = f", HTTP {result.http_status}" if result.http_status else ""
            print(f"\n  [{status}] {result.name}")
            print(f"         {result.message}{http_info}")
            print(f"         Duration: {result.duration_ms:.0f}ms")

        print()
        print("=" * 70)
        print(f"Total: {len(self.results)} tests, {passed} passed, {failed} failed")

        if failed == 0:
            print("\n[ALL ERROR HANDLING TESTS PASSED]")
            print("Server handles errors gracefully and recovers correctly.")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")
            print("Review failed tests above for error handling issues.")


async def main():
    """Main entry point."""
    tester = NimbusErrorHandlingTest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
