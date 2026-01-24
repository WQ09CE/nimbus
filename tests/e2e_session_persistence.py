#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Session Persistence

This script tests the Nimbus Server's session persistence capabilities
including session CRUD operations and message history.

Test Cases:
1. Session creation and listing - Create multiple sessions, verify list returns correctly
2. Message persistence - Send messages, get message history, verify content consistency
3. Session deletion - Delete session, verify 404 response

API Endpoints:
- POST /sessions - Create session
- GET /sessions - List sessions
- GET /sessions/{id} - Session details
- DELETE /sessions/{id} - Delete session
- GET /sessions/{id}/messages - Message history

Usage:
    python tests/e2e_session_persistence.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_session_persistence.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field

# Check for httpx
try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# Configuration
SERVER_URL = os.environ.get("NIMBUS_SERVER_URL", "http://127.0.0.1:8080")


@dataclass
class TestResult:
    """Represents the result of a test case."""
    name: str
    passed: bool
    message: str
    duration_ms: float
    details: dict = field(default_factory=dict)


class SessionPersistenceTest:
    """E2E test runner for Session persistence capabilities."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.results: list[TestResult] = []
        self.created_sessions: list[str] = []  # Track for cleanup

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
                    # Support both formats: {"status": "healthy"} and {"healthy": True}
                    is_healthy = (
                        data.get("status") == "healthy" or
                        data.get("healthy", False)
                    )
                    if is_healthy:
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

    async def test_session_creation_and_listing(self) -> TestResult:
        """
        Test Case 1: Session Creation and Listing

        Steps:
        1. Create multiple sessions with different names
        2. List sessions and verify count
        3. Verify session details are correct
        """
        self.print_header("Test 1: Session Creation and Listing")
        start_time = time.time()

        test_sessions = [
            {"name": "test_session_alpha"},
            {"name": "test_session_beta"},
            {"name": "test_session_gamma"},
        ]
        created_ids = []

        try:
            async with httpx.AsyncClient() as client:
                # Step 1: Create multiple sessions
                self.print_info("Creating test sessions...")
                for session_data in test_sessions:
                    response = await client.post(
                        f"{self.server_url}/sessions",
                        json=session_data,
                        timeout=10.0
                    )

                    if response.status_code not in (200, 201):
                        return TestResult(
                            name="Session Creation and Listing",
                            passed=False,
                            message=f"Failed to create session: {response.status_code}",
                            duration_ms=(time.time() - start_time) * 1000,
                            details={"response": response.text[:200]},
                        )

                    data = response.json()
                    session_id = data.get("id")

                    # Validate session ID format (sess_xxxxxxxxxxxx)
                    if not session_id or not session_id.startswith("sess_"):
                        return TestResult(
                            name="Session Creation and Listing",
                            passed=False,
                            message=f"Invalid session ID format: {session_id}",
                            duration_ms=(time.time() - start_time) * 1000,
                        )

                    created_ids.append(session_id)
                    self.created_sessions.append(session_id)
                    self.print_ok(f"Created session: {session_id} (name={session_data['name']})")

                # Step 2: List sessions and verify
                self.print_info("Listing sessions...")
                response = await client.get(
                    f"{self.server_url}/sessions",
                    params={"status": "active", "limit": 100},
                    timeout=10.0
                )

                if response.status_code != 200:
                    return TestResult(
                        name="Session Creation and Listing",
                        passed=False,
                        message=f"Failed to list sessions: {response.status_code}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                list_data = response.json()
                session_list = list_data.get("items", [])
                listed_ids = [s["id"] for s in session_list]

                # Verify all created sessions are in the list
                missing_sessions = [sid for sid in created_ids if sid not in listed_ids]
                if missing_sessions:
                    return TestResult(
                        name="Session Creation and Listing",
                        passed=False,
                        message=f"Missing sessions in list: {missing_sessions}",
                        duration_ms=(time.time() - start_time) * 1000,
                        details={"created": created_ids, "listed": listed_ids},
                    )

                self.print_ok(f"All {len(created_ids)} sessions found in list")

                # Step 3: Verify session details
                self.print_info("Verifying session details...")
                for i, session_id in enumerate(created_ids):
                    response = await client.get(
                        f"{self.server_url}/sessions/{session_id}",
                        timeout=10.0
                    )

                    if response.status_code != 200:
                        return TestResult(
                            name="Session Creation and Listing",
                            passed=False,
                            message=f"Failed to get session details: {session_id}",
                            duration_ms=(time.time() - start_time) * 1000,
                        )

                    detail = response.json()
                    expected_name = test_sessions[i]["name"]
                    actual_name = detail.get("name")

                    if actual_name != expected_name:
                        return TestResult(
                            name="Session Creation and Listing",
                            passed=False,
                            message=f"Name mismatch: expected {expected_name}, got {actual_name}",
                            duration_ms=(time.time() - start_time) * 1000,
                        )

                    self.print_ok(f"Session {session_id} details verified")

            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Session Creation and Listing",
                passed=True,
                message=f"Created and verified {len(created_ids)} sessions",
                duration_ms=duration_ms,
                details={"session_ids": created_ids},
            )
            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Session Creation and Listing",
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )
            self.results.append(result)
            return result

    async def test_message_persistence(self) -> TestResult:
        """
        Test Case 2: Message Persistence

        Steps:
        1. Create a new session
        2. Send messages via chat endpoint
        3. Retrieve message history
        4. Verify message content consistency
        """
        self.print_header("Test 2: Message Persistence")
        start_time = time.time()

        try:
            async with httpx.AsyncClient() as client:
                # Step 1: Create session
                self.print_info("Creating session for message test...")
                response = await client.post(
                    f"{self.server_url}/sessions",
                    json={"name": "message_test_session"},
                    timeout=10.0
                )

                if response.status_code not in (200, 201):
                    return TestResult(
                        name="Message Persistence",
                        passed=False,
                        message=f"Failed to create session: {response.status_code}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                session_id = response.json().get("id")
                self.created_sessions.append(session_id)
                self.print_ok(f"Created session: {session_id}")

                # Step 2: Send a simple message via chat endpoint (SSE stream)
                test_message = "Hello, this is a test message for persistence."
                self.print_info(f"Sending message: {test_message[:50]}...")

                # Use streaming request for chat endpoint
                response_text = ""
                async with client.stream(
                    "POST",
                    f"{self.server_url}/sessions/{session_id}/chat",
                    json={"content": test_message},
                    headers={"Accept": "text/event-stream"},
                    timeout=httpx.Timeout(60.0, connect=10.0)
                ) as response:
                    if response.status_code != 200:
                        return TestResult(
                            name="Message Persistence",
                            passed=False,
                            message=f"Chat request failed: {response.status_code}",
                            duration_ms=(time.time() - start_time) * 1000,
                        )

                    # Consume SSE stream
                    async for line in response.aiter_lines():
                        if line.startswith("event: message"):
                            continue
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[5:].strip())
                                if "content" in data:
                                    response_text = data["content"]
                            except json.JSONDecodeError:
                                pass

                self.print_ok(f"Message sent, response length: {len(response_text)}")

                # Step 3: Wait a bit for persistence
                await asyncio.sleep(0.5)

                # Step 4: Get message history
                self.print_info("Retrieving message history...")
                response = await client.get(
                    f"{self.server_url}/sessions/{session_id}/messages",
                    timeout=10.0
                )

                if response.status_code != 200:
                    return TestResult(
                        name="Message Persistence",
                        passed=False,
                        message=f"Failed to get messages: {response.status_code}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                messages_data = response.json()
                messages = messages_data.get("items", [])

                # Verify message count (should have at least user message)
                if len(messages) < 1:
                    return TestResult(
                        name="Message Persistence",
                        passed=False,
                        message="No messages found, expected at least 1",
                        duration_ms=(time.time() - start_time) * 1000,
                        details={"messages": messages},
                    )

                # Find user message
                user_messages = [m for m in messages if m.get("role") == "user"]
                if not user_messages:
                    return TestResult(
                        name="Message Persistence",
                        passed=False,
                        message="User message not found in history",
                        duration_ms=(time.time() - start_time) * 1000,
                        details={"messages": messages},
                    )

                # Verify content
                user_msg = user_messages[0]
                if user_msg.get("content") != test_message:
                    return TestResult(
                        name="Message Persistence",
                        passed=False,
                        message="Message content mismatch",
                        duration_ms=(time.time() - start_time) * 1000,
                        details={
                            "expected": test_message,
                            "actual": user_msg.get("content"),
                        },
                    )

                self.print_ok(f"Message history verified: {len(messages)} messages found")

            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Message Persistence",
                passed=True,
                message=f"Messages persisted and retrieved correctly ({len(messages)} messages)",
                duration_ms=duration_ms,
                details={
                    "session_id": session_id,
                    "message_count": len(messages),
                },
            )
            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Message Persistence",
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )
            self.results.append(result)
            return result

    async def test_session_deletion(self) -> TestResult:
        """
        Test Case 3: Session Deletion

        Steps:
        1. Create a session
        2. Verify it exists
        3. Delete the session
        4. Verify 404 on subsequent access
        """
        self.print_header("Test 3: Session Deletion")
        start_time = time.time()

        try:
            async with httpx.AsyncClient() as client:
                # Step 1: Create session
                self.print_info("Creating session for deletion test...")
                response = await client.post(
                    f"{self.server_url}/sessions",
                    json={"name": "delete_test_session"},
                    timeout=10.0
                )

                if response.status_code not in (200, 201):
                    return TestResult(
                        name="Session Deletion",
                        passed=False,
                        message=f"Failed to create session: {response.status_code}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                session_id = response.json().get("id")
                self.print_ok(f"Created session: {session_id}")

                # Step 2: Verify session exists
                self.print_info("Verifying session exists...")
                response = await client.get(
                    f"{self.server_url}/sessions/{session_id}",
                    timeout=10.0
                )

                if response.status_code != 200:
                    return TestResult(
                        name="Session Deletion",
                        passed=False,
                        message=f"Session not found after creation: {response.status_code}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                self.print_ok("Session exists")

                # Step 3: Delete session
                self.print_info("Deleting session...")
                response = await client.delete(
                    f"{self.server_url}/sessions/{session_id}",
                    timeout=10.0
                )

                if response.status_code not in (200, 204):
                    return TestResult(
                        name="Session Deletion",
                        passed=False,
                        message=f"Delete request failed: {response.status_code}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                self.print_ok("Session deleted")

                # Step 4: Verify 404 on subsequent access
                self.print_info("Verifying 404 after deletion...")
                response = await client.get(
                    f"{self.server_url}/sessions/{session_id}",
                    timeout=10.0
                )

                if response.status_code != 404:
                    return TestResult(
                        name="Session Deletion",
                        passed=False,
                        message=f"Expected 404, got {response.status_code}",
                        duration_ms=(time.time() - start_time) * 1000,
                    )

                self.print_ok("Correctly returns 404 after deletion")

            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Session Deletion",
                passed=True,
                message="Session deletion and 404 verification successful",
                duration_ms=duration_ms,
                details={"deleted_session_id": session_id},
            )
            self.results.append(result)
            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            result = TestResult(
                name="Session Deletion",
                passed=False,
                message=f"Exception: {e}",
                duration_ms=duration_ms,
            )
            self.results.append(result)
            return result

    async def cleanup(self):
        """Clean up created test sessions."""
        self.print_header("Cleanup")
        self.print_info(f"Cleaning up {len(self.created_sessions)} test sessions...")

        async with httpx.AsyncClient() as client:
            for session_id in self.created_sessions:
                try:
                    await client.delete(
                        f"{self.server_url}/sessions/{session_id}",
                        timeout=5.0
                    )
                    self.print_info(f"Deleted: {session_id}")
                except Exception:
                    pass  # Ignore cleanup errors

        self.print_ok("Cleanup complete")

    async def run_all_tests(self) -> bool:
        """Run all E2E tests."""
        self.print_header("Nimbus E2E Test - Session Persistence")
        self.print_info(f"Server: {self.server_url}")
        print()

        # Health check
        if not await self.check_health():
            self.print_fail("Server not available, aborting tests")
            return False

        # Run test cases
        await self.test_session_creation_and_listing()
        await asyncio.sleep(0.5)

        await self.test_message_persistence()
        await asyncio.sleep(0.5)

        await self.test_session_deletion()

        # Cleanup
        await self.cleanup()

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
    tester = SessionPersistenceTest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
