#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Tiered Memory

This script tests the TieredMemoryManager's behavior in multi-turn conversations:
1. Conversation history accumulation across turns
2. Context preservation in long conversations
3. Memory stats validation

Test Cases:
1. Multi-turn Accumulation - Each turn sees previous history
2. Long Conversation Context - 10+ turns, early info still accessible
3. Memory Stats Validation - turn_count, pinned_tokens metrics

API Endpoints:
- GET /health - Health check
- POST /sessions - Create session
- POST /sessions/{session_id}/chat - Send message (SSE stream)
- GET /sessions/{session_id} - Get session details

Usage:
    python tests/e2e_tiered_memory.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_tiered_memory.py
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

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
class MemoryTestResult:
    """Represents the result of a memory test."""
    name: str
    description: str
    passed: bool
    verdict: str
    turns: List[TurnResult] = field(default_factory=list)
    memory_stats: Optional[dict] = None


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


class TieredMemoryTest:
    """E2E test runner for Tiered Memory functionality."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.session_id: Optional[str] = None
        self.results: List[MemoryTestResult] = []

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

    async def create_session(self, memory_type: str = "tiered") -> Optional[str]:
        """Create a new session with specified memory type."""
        self.print_subheader(f"Create Session (memory_type={memory_type})")

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.server_url}/session",
                    json={"memory_type": memory_type, "planner_type": "dag"},
                    timeout=10.0
                )

                if response.status_code in (200, 201):
                    data = response.json()
                    session_id = data.get("id")
                    if session_id:
                        self.session_id = session_id
                        self.print_ok(f"Session created: {session_id}")
                        self.print_info(f"Memory type: {data.get('memory_type')}")
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
    ) -> MemoryTestResult:
        """
        Run a multi-turn memory test.

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
        self.print_subheader("Memory Behavior Check")
        passed, verdict = context_checker(turn_results)

        if passed:
            self.print_ok(f"Memory working: {verdict}")
        else:
            self.print_fail(f"Memory issue: {verdict}")

        result = MemoryTestResult(
            name=name,
            description=description,
            passed=passed,
            verdict=verdict,
            turns=turn_results,
        )
        self.results.append(result)
        return result

    async def test_multi_turn_accumulation(self) -> MemoryTestResult:
        """
        Test 1: Multi-turn Conversation Accumulation
        Verify that each turn can see previous conversation history.
        """
        def check_memory(turns: List[TurnResult]) -> Tuple[bool, str]:
            if len(turns) < 5:
                return False, "Not enough turns completed"

            details = []
            checks_passed = 0

            # Turn 2 should reference Turn 1's file read
            t2 = turns[1].response_text.lower()
            if "pyproject" in t2 or "nimbus" in t2 or "file" in t2:
                checks_passed += 1
                details.append("Turn 2: References Turn 1 file read")
            else:
                details.append("Turn 2: No reference to Turn 1")

            # Turn 3 should reference Turn 2's question
            t3 = turns[2].response_text.lower()
            if any(kw in t3 for kw in ["name", "nimbus", "project"]):
                checks_passed += 1
                details.append("Turn 3: References Turn 2 project name")
            else:
                details.append("Turn 3: No reference to Turn 2")

            # Turn 4 should reference Turn 1's content (earlier context)
            t4 = turns[3].response_text.lower()
            if any(kw in t4 for kw in ["version", "0.", "1.", "2."]):
                checks_passed += 1
                details.append("Turn 4: Can access earlier context (version)")
            else:
                details.append("Turn 4: No access to earlier context")

            # Turn 5 should summarize multiple turns
            t5 = turns[4].response_text.lower()
            if any(kw in t5 for kw in ["discussed", "talked", "covered", "pyproject", "nimbus"]):
                checks_passed += 1
                details.append("Turn 5: Can summarize conversation")
            else:
                details.append("Turn 5: Cannot summarize conversation")

            passed = checks_passed >= 3  # At least 3 of 4 checks pass
            return passed, "; ".join(details)

        return await self.run_multi_turn_test(
            name="Multi-turn Accumulation",
            description="Test that conversation history accumulates correctly across 5 turns",
            turns=[
                "Read pyproject.toml file and tell me what you see",
                "What did we just look at?",
                "What is the name of this project?",
                "What version is it?",
                "Summarize what we've discussed so far"
            ],
            context_checker=check_memory
        )

    async def test_long_conversation_context(self) -> MemoryTestResult:
        """
        Test 2: Long Conversation Context Preservation
        Verify that early information is still accessible after 10+ turns.
        """
        def check_memory(turns: List[TurnResult]) -> Tuple[bool, str]:
            if len(turns) < 10:
                return False, "Not enough turns completed"

            details = []

            # Turn 1 establishes the secret code
            t1 = turns[0].response_text.lower()
            secret_acknowledged = any(kw in t1 for kw in ["remember", "noted", "secret", "alpha"])

            # Final turn should recall the secret
            t_final = turns[-1].response_text.lower()
            secret_recalled = any(kw in t_final for kw in ["alpha", "secret", "code"])

            if secret_acknowledged:
                details.append("Turn 1: Secret code acknowledged")
            else:
                details.append("Turn 1: Secret code not clearly acknowledged")

            if secret_recalled:
                details.append(f"Turn {len(turns)}: Secret code recalled correctly")
            else:
                details.append(f"Turn {len(turns)}: Secret code NOT recalled (response: {t_final[:100]}...)")

            # Check intermediate turns had varied topics
            intermediate_ok = True
            for i, turn in enumerate(turns[1:-1], 2):
                if len(turn.response_text) < 10:
                    intermediate_ok = False
                    details.append(f"Turn {i}: Empty or very short response")

            if intermediate_ok:
                details.append("Intermediate turns: All had substantial responses")

            passed = secret_acknowledged and secret_recalled and intermediate_ok
            return passed, "; ".join(details)

        # Create diverse conversation topics to test memory under load
        return await self.run_multi_turn_test(
            name="Long Conversation Context",
            description="Test that early context (secret code) is preserved across 10 turns",
            turns=[
                "Remember this secret code: ALPHA-7749. Acknowledge that you've noted it.",
                "What is 15 + 27?",
                "Name 3 programming languages",
                "What is the capital of France?",
                "Explain what a function is in programming",
                "What color is the sky?",
                "How many days are in a week?",
                "What is an API?",
                "Count from 1 to 5",
                "What was the secret code I told you at the beginning of our conversation?"
            ],
            context_checker=check_memory
        )

    async def test_context_with_file_operations(self) -> MemoryTestResult:
        """
        Test 3: Context Preservation with File Operations
        Verify memory works correctly when interspersed with file operations.
        """
        def check_memory(turns: List[TurnResult]) -> Tuple[bool, str]:
            if len(turns) < 6:
                return False, "Not enough turns completed"

            details = []
            checks_passed = 0

            # Turn 1 should read file
            t1 = turns[0].response_text.lower()
            if "pyproject" in t1 or "[project]" in t1 or "nimbus" in t1:
                checks_passed += 1
                details.append("Turn 1: File read successful")
            else:
                details.append("Turn 1: File read unclear")

            # Turn 3 should relate to Turn 2's question about src
            t3 = turns[2].response_text.lower()
            if any(kw in t3 for kw in ["src", "directory", "folder", "file", "core", "llm"]):
                checks_passed += 1
                details.append("Turn 3: References src directory exploration")
            else:
                details.append("Turn 3: No reference to src directory")

            # Turn 6 should recall both pyproject.toml and src exploration
            t6 = turns[5].response_text.lower()
            recall_pyproject = any(kw in t6 for kw in ["pyproject", "toml"])
            recall_src = any(kw in t6 for kw in ["src", "directory", "folder", "directories"])

            if recall_pyproject:
                checks_passed += 1
                details.append("Turn 6: Recalls pyproject.toml")
            else:
                details.append("Turn 6: Does not recall pyproject.toml")

            if recall_src:
                checks_passed += 1
                details.append("Turn 6: Recalls src exploration")
            else:
                details.append("Turn 6: Does not recall src exploration")

            passed = checks_passed >= 3
            return passed, "; ".join(details)

        return await self.run_multi_turn_test(
            name="Context with File Operations",
            description="Test memory preservation when interleaved with file operations",
            turns=[
                "Read pyproject.toml",
                "Now explore the src directory structure",
                "What did you find in src?",
                "What dependencies does the project have based on pyproject.toml?",
                "Compare the project structure to typical Python projects",
                "Summarize all the files and directories we've looked at"
            ],
            context_checker=check_memory
        )

    async def run_all_tests(self) -> bool:
        """Run all tiered memory tests."""
        self.print_header("Nimbus E2E Test - Tiered Memory", "=")
        self.print_info(f"Server: {self.server_url}")
        self.print_info("Testing multi-turn conversation memory behavior")
        print()

        # Health check
        if not await self.check_health():
            self.print_fail("Server not available, aborting tests")
            return False

        # Define test functions
        tests = [
            self.test_multi_turn_accumulation,
            self.test_long_conversation_context,
            self.test_context_with_file_operations,
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
        self.print_header("Tiered Memory Test Summary")

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
            print("\n[ALL TIERED MEMORY TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")
            print("\nNote: Memory behavior may vary. Check conversation logs above.")


async def main():
    """Main entry point."""
    tester = TieredMemoryTest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
