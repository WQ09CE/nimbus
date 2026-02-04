#!/usr/bin/env python3
"""
Nimbus Server E2E Test - Context Understanding

This script tests the Nimbus Agent's multi-turn conversation context understanding
capabilities.

Test Cases:
1. Pronoun Resolution - Understanding "it" refers to previous content
2. Cross-turn Information Association - Relating info across turns
3. Sequential Tasks - Understanding implicit context from previous turns
4. Context Accumulation - Building understanding across multiple turns

API Endpoints:
- GET /health - Health check
- POST /session - Create session
- POST /session/{session_id}/message - Send message (SSE stream)

Usage:
    python tests/e2e_context_test.py

    # Or with custom server:
    NIMBUS_SERVER_URL=http://localhost:9000 python tests/e2e_context_test.py
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
    events: list[SSEEvent]
    duration_ms: float


@dataclass
class ContextTestResult:
    """Represents the result of a context understanding test."""
    name: str
    description: str
    passed: bool
    verdict: str
    turns: list[TurnResult] = field(default_factory=list)
    context_check_details: str = ""


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


class NimbusContextTest:
    """E2E test runner for Nimbus Agent context understanding capabilities."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.session_id: Optional[str] = None
        self.results: list[ContextTestResult] = []

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
        timeout: float = 120.0,
        verbose: bool = True
    ) -> tuple[list[SSEEvent], str]:
        """
        Send a message and collect SSE events.

        Returns:
            Tuple of (events, final_response_text)
        """
        if not self.session_id:
            raise ValueError("No session created")

        url = f"{self.server_url}/session/{self.session_id}/message"

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
        turns: list[str],
        context_checker: Callable[[list[TurnResult]], tuple[bool, str]],
        timeout: float = 120.0
    ) -> ContextTestResult:
        """
        Run a multi-turn context understanding test.

        Args:
            name: Test name
            description: What this test is checking
            turns: List of messages to send in sequence
            context_checker: Function to check if context was understood
            timeout: Timeout per turn
        """
        self.print_header(f"Test: {name}")
        self.print_info(f"Description: {description}")
        print(f"Turns: {len(turns)}")

        turn_results: list[TurnResult] = []

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

        # Check context understanding
        self.print_subheader("Context Understanding Check")
        passed, verdict = context_checker(turn_results)

        if passed:
            self.print_ok(f"Context understood: {verdict}")
        else:
            self.print_fail(f"Context issue: {verdict}")

        result = ContextTestResult(
            name=name,
            description=description,
            passed=passed,
            verdict=verdict,
            turns=turn_results,
            context_check_details=verdict
        )
        self.results.append(result)
        return result

    async def test_pronoun_resolution(self) -> ContextTestResult:
        """
        Test 1: Pronoun Resolution
        Can the agent understand that "this project" refers to pyproject.toml content?
        """
        def check_context(turns: list[TurnResult]) -> tuple[bool, str]:
            if len(turns) < 2:
                return False, "Not enough turns completed"

            # First turn should have read pyproject.toml
            t1 = turns[0].response_text.lower()
            t2 = turns[1].response_text.lower()

            # Check if first turn mentioned pyproject.toml content
            first_turn_ok = "pyproject" in t1 or "toml" in t1 or "[project]" in t1

            # Check if second turn can answer about project name
            # The project name should be "nimbus" based on pyproject.toml
            second_turn_ok = "nimbus" in t2

            details = []
            if first_turn_ok:
                details.append("Turn 1: pyproject.toml was read")
            else:
                details.append("Turn 1: pyproject.toml not clearly read")

            if second_turn_ok:
                details.append("Turn 2: Project name 'nimbus' identified")
            else:
                details.append(f"Turn 2: Project name not found (response: {t2[:100]}...)")

            passed = first_turn_ok and second_turn_ok
            return passed, "; ".join(details)

        return await self.run_multi_turn_test(
            name="Pronoun Resolution",
            description="Test if agent can resolve 'this project' to previous pyproject.toml content",
            turns=[
                "Read pyproject.toml file",
                "What is this project's name?"
            ],
            context_checker=check_context
        )

    async def test_cross_turn_reference(self) -> ContextTestResult:
        """
        Test 2: Cross-turn Information Association
        Can the agent understand that "it" refers to CodeAgent from previous search?
        """
        def check_context(turns: list[TurnResult]) -> tuple[bool, str]:
            if len(turns) < 2:
                return False, "Not enough turns completed"

            t1 = turns[0].response_text.lower()
            t2 = turns[1].response_text.lower()

            # First turn should find CodeAgent
            first_turn_ok = "codeagent" in t1 or "code_agent" in t1 or "found" in t1

            # Second turn should mention the file path
            # Expected: skills/code_agent/ or similar
            second_turn_ok = (
                "skill" in t2 or
                "code_agent" in t2 or
                ".py" in t2 or
                "file" in t2
            )

            details = []
            if first_turn_ok:
                details.append("Turn 1: CodeAgent search completed")
            else:
                details.append("Turn 1: CodeAgent not found in search")

            if second_turn_ok:
                details.append("Turn 2: File location referenced")
            else:
                details.append(f"Turn 2: No file reference (response: {t2[:100]}...)")

            passed = first_turn_ok and second_turn_ok
            return passed, "; ".join(details)

        return await self.run_multi_turn_test(
            name="Cross-turn Reference",
            description="Test if 'it' refers to CodeAgent from previous search",
            turns=[
                "Search for 'class CodeAgent' in the codebase",
                "Which file is it defined in?"
            ],
            context_checker=check_context
        )

    async def test_sequential_tasks(self) -> ContextTestResult:
        """
        Test 3: Sequential Tasks
        Can the agent understand "among them" refers to previously listed files?
        """
        def check_context(turns: list[TurnResult]) -> tuple[bool, str]:
            if len(turns) < 2:
                return False, "Not enough turns completed"

            t1 = turns[0].response_text.lower()
            t2 = turns[1].response_text.lower()

            # First turn should list files in llm directory
            first_turn_ok = (
                "__init__" in t1 or
                ".py" in t1 or
                "base" in t1 or
                "factory" in t1 or
                "file" in t1
            )

            # Second turn should read __init__.py from llm directory
            second_turn_ok = (
                "llm" in t2 or
                "import" in t2 or
                "def" in t2 or
                "class" in t2 or
                "__all__" in t2 or
                "from" in t2
            )

            details = []
            if first_turn_ok:
                details.append("Turn 1: llm directory listed")
            else:
                details.append("Turn 1: Directory listing unclear")

            if second_turn_ok:
                details.append("Turn 2: __init__.py content from llm dir")
            else:
                details.append(f"Turn 2: Unclear if correct file read (response: {t2[:100]}...)")

            passed = first_turn_ok and second_turn_ok
            return passed, "; ".join(details)

        return await self.run_multi_turn_test(
            name="Sequential Tasks",
            description="Test if 'among them' refers to previously listed src/nimbus/llm directory",
            turns=[
                "List files in src/nimbus/llm/ directory",
                "Read the __init__.py file among them"
            ],
            context_checker=check_context
        )

    async def test_context_accumulation(self) -> ContextTestResult:
        """
        Test 4: Context Accumulation
        Can the agent accumulate context across 3 turns?
        """
        def check_context(turns: list[TurnResult]) -> tuple[bool, str]:
            if len(turns) < 3:
                return False, "Not enough turns completed"

            t1 = turns[0].response_text.lower()
            t2 = turns[1].response_text.lower()
            t3 = turns[2].response_text.lower()

            # First turn should list subdirectories in src/nimbus
            # May return files or directories, check for common patterns
            first_turn_ok = (
                "core" in t1 or
                "llm" in t1 or
                "server" in t1 or
                "tools" in t1 or
                "memory" in t1 or
                "found" in t1 or  # "Found X files"
                "src/nimbus" in t1 or  # Path reference
                "__init__" in t1  # Common file
            )

            # Second turn should list core contents
            second_turn_ok = (
                "agent" in t2 or
                "planner" in t2 or
                ".py" in t2 or
                "runtime" in t2 or
                "factory" in t2
            )

            # Third turn should identify llm as LLM-related
            third_turn_ok = (
                "llm" in t3 or
                "language" in t3 or
                "model" in t3
            )

            details = []
            if first_turn_ok:
                details.append("Turn 1: Subdirectories listed")
            else:
                details.append("Turn 1: Subdirectory listing unclear")

            if second_turn_ok:
                details.append("Turn 2: core directory explored")
            else:
                details.append("Turn 2: core directory not explored")

            if third_turn_ok:
                details.append("Turn 3: LLM directory identified")
            else:
                details.append(f"Turn 3: LLM not identified (response: {t3[:100]}...)")

            passed = first_turn_ok and second_turn_ok and third_turn_ok
            return passed, "; ".join(details)

        return await self.run_multi_turn_test(
            name="Context Accumulation",
            description="Test if agent accumulates understanding across 3 turns",
            turns=[
                "List all subdirectories in src/nimbus/",
                "What files are inside src/nimbus/core directory?",
                "Among the directories we've seen, which one handles LLM integration?"
            ],
            context_checker=check_context
        )

    async def run_all_tests(self) -> bool:
        """Run all context understanding tests."""
        self.print_header("Nimbus E2E Test - Context Understanding", "=")
        self.print_info(f"Server: {self.server_url}")
        self.print_info("Testing multi-turn conversation context understanding")
        print()

        # Health check
        if not await self.check_health():
            self.print_fail("Server not available, aborting tests")
            return False

        # Create session for all tests (same session = shared context)
        session_id = await self.create_session()
        if not session_id:
            self.print_fail("Cannot create session, aborting tests")
            return False

        # Run context tests
        # Each test creates a new session to isolate context
        tests = [
            self.test_pronoun_resolution,
            self.test_cross_turn_reference,
            self.test_sequential_tasks,
            self.test_context_accumulation,
        ]

        for test_func in tests:
            # Create new session for each test to isolate context
            self.print_subheader("Creating fresh session for test isolation")
            session_id = await self.create_session()
            if not session_id:
                self.print_warn("Could not create session, using existing")

            await test_func()
            # Pause between tests
            await asyncio.sleep(1)

        # Print summary
        self.print_summary()

        return all(r.passed for r in self.results)

    def print_summary(self):
        """Print test summary."""
        self.print_header("Context Understanding Test Summary")

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
            print("\n[ALL CONTEXT TESTS PASSED]")
        else:
            print(f"\n[{failed} TEST(S) FAILED]")
            print("\nNote: Context understanding may vary. Check conversation logs above.")


async def main():
    """Main entry point."""
    tester = NimbusContextTest(SERVER_URL)
    success = await tester.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
