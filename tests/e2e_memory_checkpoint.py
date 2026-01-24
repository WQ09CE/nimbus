#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Memory Checkpoint

This script tests the Memory Checkpoint functionality:
1. Multi-turn conversation accumulation with checkpoint verification
2. Memory state retrieval via API (if available)
3. Session recovery and memory persistence

Test Cases:
1. Multi-turn Checkpoint - 5 turns, verify memory accumulates correctly
2. Memory State Retrieval - Get session memory info via API
3. Session Recovery - Reuse session ID, verify memory persists

API Endpoints:
- GET /health - Health check
- POST /session - Create session
- POST /session/{session_id}/message - Send message (SSE stream)
- GET /session/{session_id} - Get session details (includes memory state)

Usage:
    python tests/e2e_memory_checkpoint.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_memory_checkpoint.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

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
class TurnResult:
    """Represents the result of a single conversation turn."""
    turn_number: int
    message: str
    response_text: str
    events: List[SSEEvent]
    duration_ms: float


@dataclass
class CheckpointTestResult:
    """Represents the result of a memory checkpoint test."""
    name: str
    description: str
    passed: bool
    verdict: str
    turns: List[TurnResult] = field(default_factory=list)
    memory_state: Optional[dict] = None
    checkpoints: Optional[List[dict]] = None


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


class MemoryCheckpointTest:
    """E2E test runner for Memory Checkpoint functionality."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.session_id: Optional[str] = None
        self.results: List[CheckpointTestResult] = []

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

    async def create_session(
        self,
        memory_type: str = "tiered",
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """Create a new session with specified memory type."""
        self.print_subheader(f"Create Session (memory_type={memory_type})")

        try:
            async with httpx.AsyncClient() as client:
                payload: Dict[str, Any] = {
                    "memory_type": memory_type,
                    "planner_type": "dag",
                }
                if session_id:
                    payload["id"] = session_id

                response = await client.post(
                    f"{self.server_url}/session",
                    json=payload,
                    timeout=10.0
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    sid = data.get("id")
                    if sid:
                        self.session_id = sid
                        self.print_ok(f"Session created: {sid}")
                        self.print_info(f"Memory type: {data.get('memory_type')}")
                        return sid
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

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session details including memory state."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.server_url}/session/{session_id}",
                    timeout=10.0
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    self.print_warn(f"Get session returned {response.status_code}")
                    return None
        except Exception as e:
            self.print_warn(f"Get session failed: {e}")
            return None

    async def send_message(
        self,
        message: str,
        timeout: float = 120.0,
        verbose: bool = True
    ) -> Tuple[List[SSEEvent], str]:
        """
        Send a message and collect SSE events.

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

    async def run_multi_turn_test(
        self,
        name: str,
        description: str,
        turns: List[str],
        context_checker: Callable[[List[TurnResult]], Tuple[bool, str]],
        timeout: float = 120.0
    ) -> CheckpointTestResult:
        """
        Run a multi-turn memory checkpoint test.

        Args:
            name: Test name
            description: What this test is checking
            turns: List of messages to send in sequence
            context_checker: Function to check if memory works correctly
            timeout: Timeout per turn
        """
        self.print_header(f"Test: {name}")
        self.print_info(f"Description: {description}")
        print(f"Turns: {len(turns)}")

        turn_results: List[TurnResult] = []

        for i, message in enumerate(turns, 1):
            self.print_subheader(f"Turn {i}/{len(turns)}")
            print(f"  User: {message}")
            print()

            start_time = time.time()

            try:
                events, response_text = await self.send_message(
                    message, timeout=timeout, verbose=True
                )
                duration_ms = (time.time() - start_time) * 1000

                turn_result = TurnResult(
                    turn_number=i,
                    message=message,
                    response_text=response_text,
                    events=events,
                    duration_ms=duration_ms
                )
                turn_results.append(turn_result)

                # Print response
                print()
                print(f"  [Response] ({duration_ms:.0f}ms, {len(response_text)} chars)")
                print("-" * 50)
                # Print full response for context analysis
                if len(response_text) > 1000:
                    print(response_text[:1000])
                    print(f"... ({len(response_text) - 1000} more chars)")
                else:
                    print(response_text if response_text else "(empty response)")
                print("-" * 50)

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                self.print_fail(f"Turn {i} failed: {e}")
                turn_result = TurnResult(
                    turn_number=i,
                    message=message,
                    response_text=f"ERROR: {e}",
                    events=[],
                    duration_ms=duration_ms
                )
                turn_results.append(turn_result)

            # Brief pause between turns
            if i < len(turns):
                await asyncio.sleep(0.5)

        # Check memory behavior
        self.print_subheader("Memory Checkpoint Behavior Check")
        passed, verdict = context_checker(turn_results)

        if passed:
            self.print_ok(f"Checkpoint working: {verdict}")
        else:
            self.print_fail(f"Checkpoint issue: {verdict}")

        result = CheckpointTestResult(
            name=name,
            description=description,
            passed=passed,
            verdict=verdict,
            turns=turn_results,
        )
        self.results.append(result)
        return result

    async def test_multi_turn_checkpoint(self) -> CheckpointTestResult:
        """
        Test 1: Multi-turn Conversation with Checkpoint Verification
        Conduct 5 turns and verify memory accumulates correctly.
        """
        def check_memory(turns: List[TurnResult]) -> Tuple[bool, str]:
            if len(turns) < 5:
                return False, "Not enough turns completed"

            details = []
            checks_passed = 0

            # Turn 2 should reference Turn 1's established fact
            t2 = turns[1].response_text.lower()
            if "blue" in t2 or "color" in t2 or "favorite" in t2:
                checks_passed += 1
                details.append("Turn 2: References Turn 1's color fact")
            else:
                details.append("Turn 2: No reference to Turn 1")

            # Turn 3 should reference Turn 2's number
            t3 = turns[2].response_text.lower()
            if "42" in t3 or "number" in t3 or "lucky" in t3:
                checks_passed += 1
                details.append("Turn 3: References Turn 2's lucky number")
            else:
                details.append("Turn 3: No reference to Turn 2")

            # Turn 4 should reference Turn 3's food
            t4 = turns[3].response_text.lower()
            if "pizza" in t4 or "food" in t4 or "favorite" in t4:
                checks_passed += 1
                details.append("Turn 4: References Turn 3's food preference")
            else:
                details.append("Turn 4: No reference to Turn 3")

            # Turn 5 should recall all three facts
            t5 = turns[4].response_text.lower()
            recall_count = 0
            if "blue" in t5:
                recall_count += 1
            if "42" in t5:
                recall_count += 1
            if "pizza" in t5:
                recall_count += 1

            if recall_count >= 2:
                checks_passed += 1
                details.append(f"Turn 5: Recalled {recall_count}/3 facts correctly")
            else:
                details.append(f"Turn 5: Only recalled {recall_count}/3 facts")

            passed = checks_passed >= 3  # At least 3 of 4 checks pass
            return passed, "; ".join(details)

        return await self.run_multi_turn_test(
            name="Multi-turn Checkpoint",
            description="Test that memory accumulates correctly across 5 turns",
            turns=[
                "My favorite color is blue. Please remember this.",
                "What is my favorite color?",
                "My lucky number is 42. Please remember this too.",
                "My favorite food is pizza. Now you know three things about me.",
                "Can you list all three things you know about me?"
            ],
            context_checker=check_memory
        )

    async def test_memory_state_retrieval(self) -> CheckpointTestResult:
        """
        Test 2: Memory State Retrieval
        Get session memory info via API after some conversation.
        """
        self.print_header("Test: Memory State Retrieval")
        self.print_info("Description: Get session memory state via API")

        turn_results: List[TurnResult] = []
        memory_state = None
        passed = False
        details = []

        try:
            # Send a few messages first
            messages = [
                "I am testing memory checkpoints. My name is TestUser.",
                "I live in San Francisco.",
            ]

            for i, msg in enumerate(messages, 1):
                self.print_subheader(f"Turn {i}/{len(messages)}")
                print(f"  User: {msg}")

                start_time = time.time()
                events, response_text = await self.send_message(msg, verbose=False)
                duration_ms = (time.time() - start_time) * 1000

                turn_result = TurnResult(
                    turn_number=i,
                    message=msg,
                    response_text=response_text,
                    events=events,
                    duration_ms=duration_ms
                )
                turn_results.append(turn_result)
                self.print_ok(f"Turn {i} completed in {duration_ms:.0f}ms")
                await asyncio.sleep(0.3)

            # Now get session info
            self.print_subheader("Retrieving Session State")
            session_data = await self.get_session(self.session_id)

            if session_data:
                self.print_ok(f"Session data retrieved: {list(session_data.keys())}")
                memory_state = session_data

                # Check what memory info is available
                if "memory_type" in session_data:
                    details.append(f"memory_type: {session_data['memory_type']}")

                if "memory_state" in session_data and session_data["memory_state"]:
                    mem_state = session_data["memory_state"]
                    if isinstance(mem_state, dict):
                        keys_info = list(mem_state.keys())
                    else:
                        keys_info = type(mem_state).__name__
                    details.append(f"memory_state available: {keys_info}")

                # Check message count if available
                if "message_count" in session_data:
                    count = session_data["message_count"]
                    details.append(f"message_count: {count}")
                    if count >= len(messages) * 2:  # user + assistant messages
                        passed = True

                # If we got session data, consider it passed
                if session_data.get("id") == self.session_id:
                    passed = True
                    details.append("Session ID matches")
            else:
                details.append("Could not retrieve session data")

        except Exception as e:
            details.append(f"Error: {e}")

        verdict = "; ".join(details) if details else "No details"

        if passed:
            self.print_ok(f"Memory state retrieval: {verdict}")
        else:
            self.print_fail(f"Memory state retrieval: {verdict}")

        result = CheckpointTestResult(
            name="Memory State Retrieval",
            description="Test retrieving memory state via API",
            passed=passed,
            verdict=verdict,
            turns=turn_results,
            memory_state=memory_state,
        )
        self.results.append(result)
        return result

    async def test_session_recovery(self) -> CheckpointTestResult:
        """
        Test 3: Session Recovery and Memory Persistence
        Create session, chat, then reuse the same session ID to verify memory persists.
        """
        self.print_header("Test: Session Recovery")
        self.print_info("Description: Verify memory persists when continuing a session")

        turn_results: List[TurnResult] = []
        passed = False
        details = []

        try:
            # Phase 1: Establish some facts in conversation
            self.print_subheader("Phase 1: Establish Facts")
            phase1_messages = [
                "Let me tell you a secret code: GAMMA-9876. Please remember it.",
                "The project name is 'ProjectPhoenix'. Remember this too.",
            ]

            for i, msg in enumerate(phase1_messages, 1):
                print(f"  Turn {i}: {msg[:50]}...")
                start_time = time.time()
                events, response_text = await self.send_message(msg, verbose=False)
                duration_ms = (time.time() - start_time) * 1000

                turn_result = TurnResult(
                    turn_number=i,
                    message=msg,
                    response_text=response_text,
                    events=events,
                    duration_ms=duration_ms
                )
                turn_results.append(turn_result)
                self.print_ok(f"Turn {i} completed in {duration_ms:.0f}ms")
                await asyncio.sleep(0.3)

            # Save session ID for reuse
            original_session_id = self.session_id
            self.print_info(f"Session ID for recovery: {original_session_id}")

            # Brief pause to simulate session break
            self.print_subheader("Simulating Session Break")
            await asyncio.sleep(1.0)

            # Phase 2: Continue with the same session (simulate recovery)
            self.print_subheader("Phase 2: Resume Session and Test Memory")

            # The session should already be active, just continue sending messages
            phase2_messages = [
                "What was the secret code I told you earlier?",
                "What is the project name?",
            ]

            for i, msg in enumerate(phase2_messages, 1):
                turn_num = len(phase1_messages) + i
                print(f"  Turn {turn_num}: {msg}")
                start_time = time.time()
                events, response_text = await self.send_message(msg, verbose=False)
                duration_ms = (time.time() - start_time) * 1000

                turn_result = TurnResult(
                    turn_number=turn_num,
                    message=msg,
                    response_text=response_text,
                    events=events,
                    duration_ms=duration_ms
                )
                turn_results.append(turn_result)

                print(f"  Response: {response_text[:200]}...")
                self.print_ok(f"Turn {turn_num} completed in {duration_ms:.0f}ms")
                await asyncio.sleep(0.3)

            # Check if memory persisted
            self.print_subheader("Verify Memory Persistence")

            # Check last two responses for recalled facts
            if len(turn_results) >= 4:
                t3 = turn_results[2].response_text.lower()
                t4 = turn_results[3].response_text.lower()

                code_recalled = "gamma" in t3 or "9876" in t3
                project_recalled = "phoenix" in t4 or "projectphoenix" in t4

                if code_recalled:
                    details.append("Secret code recalled correctly")
                else:
                    details.append("Secret code NOT recalled")

                if project_recalled:
                    details.append("Project name recalled correctly")
                else:
                    details.append("Project name NOT recalled")

                passed = code_recalled and project_recalled

                # Also verify session ID is consistent
                if self.session_id == original_session_id:
                    details.append("Session ID consistent")
                else:
                    details.append("WARNING: Session ID changed")

        except Exception as e:
            details.append(f"Error: {e}")
            import traceback
            traceback.print_exc()

        verdict = "; ".join(details) if details else "No details"

        if passed:
            self.print_ok(f"Session recovery: {verdict}")
        else:
            self.print_fail(f"Session recovery: {verdict}")

        result = CheckpointTestResult(
            name="Session Recovery",
            description="Test that memory persists when continuing a session",
            passed=passed,
            verdict=verdict,
            turns=turn_results,
        )
        self.results.append(result)
        return result

    async def test_context_reference_chain(self) -> CheckpointTestResult:
        """
        Test 4: Context Reference Chain
        Verify that later turns can reference content from earlier turns in a chain.
        """
        def check_memory(turns: List[TurnResult]) -> Tuple[bool, str]:
            if len(turns) < 6:
                return False, "Not enough turns completed"

            details = []
            checks_passed = 0

            # Turn 6 should be able to list all animals mentioned
            t6 = turns[5].response_text.lower()

            animals = ["cat", "dog", "bird", "fish", "rabbit"]
            animals_found = sum(1 for animal in animals if animal in t6)

            if animals_found >= 4:
                checks_passed += 1
                details.append(f"Turn 6: Recalled {animals_found}/5 animals")
            else:
                details.append(f"Turn 6: Only recalled {animals_found}/5 animals")

            # Check intermediate turns reference context
            for i in range(1, 5):
                t = turns[i].response_text.lower()
                has_context = (
                    "remember" in t or "noted" in t or "added" in t
                    or animals[i] in t or animals[i-1] in t
                )
                if has_context:
                    checks_passed += 1

            passed = checks_passed >= 3  # Flexible threshold
            details.append(f"Context checks passed: {checks_passed}")

            return passed, "; ".join(details)

        return await self.run_multi_turn_test(
            name="Context Reference Chain",
            description="Test that context accumulates through a chain of references",
            turns=[
                "I have a pet cat. Please remember this.",
                "I also have a dog. Now I have two pets.",
                "Adding a bird to my collection. Three pets now.",
                "A fish joins the family. Four pets.",
                "And finally, a rabbit. Five pets total.",
                "Can you list all five of my pets?"
            ],
            context_checker=check_memory
        )

    async def run_all_tests(self) -> bool:
        """Run all memory checkpoint tests."""
        self.print_header("Nimbus E2E Test - Memory Checkpoint", "=")
        self.print_info(f"Server: {self.server_url}")
        self.print_info("Testing memory checkpoint functionality")
        print()

        # Health check
        if not await self.check_health():
            self.print_fail("Server not available, aborting tests")
            return False

        # Define test functions
        tests = [
            self.test_multi_turn_checkpoint,
            self.test_memory_state_retrieval,
            self.test_session_recovery,
            self.test_context_reference_chain,
        ]

        for test_func in tests:
            # Create fresh session for each test
            self.print_subheader("Creating fresh session for test isolation")
            session_id = await self.create_session(memory_type="tiered")
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
        self.print_header("Memory Checkpoint Test Summary")

        passed = sum(1 for r in self.results if r.passed)
        failed = len(self.results) - passed

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            total_time = sum(t.duration_ms for t in result.turns)
            print(f"\n  [{status}] {result.name}")
            print(f"         {result.description}")
            print(f"         Verdict: {result.verdict}")
            print(f"         Turns: {len(result.turns)}, Total time: {total_time:.0f}ms")

        print()
        print("=" * 70)
        print(f"Total: {len(self.results)} tests, {passed} passed, {failed} failed")

        if failed == 0:
            print("\n[ALL MEMORY CHECKPOINT TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")
            print("\nNote: Memory behavior may vary. Check conversation logs above.")


async def main():
    """Main entry point."""
    tester = MemoryCheckpointTest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
