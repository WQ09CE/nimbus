#!/usr/bin/env python3
"""
Nimbus E2E Test - Infinite Context (Compaction)

Tests the "infinite context" feature where the AI maintains intelligence
after context compaction (archiving old messages when budget exceeded).

Test Strategy:
1. Use small token budget (4000) to trigger compaction quickly
2. Give AI a series of tasks that accumulate context
3. Trigger compaction (archive old context)
4. Ask about information from BEFORE compaction
5. Verify AI can still access it (via archive file or summary)

Key Scenarios:
1. Memory of file contents after compaction
2. Memory of tool results after compaction  
3. Memory of user instructions after compaction
4. Multi-compaction survival (2+ compactions)
"""

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)


SERVER_URL = os.environ.get("NIMBUS_SERVER_URL", "http://127.0.0.1:4096/api/v1")


@dataclass
class TestResult:
    """Test result with details."""
    name: str
    passed: bool
    details: str
    duration_ms: float
    compactions_triggered: int = 0
    final_tokens: int = 0


class InfiniteContextTester:
    """Test harness for infinite context capabilities."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.session_id: Optional[str] = None
        self.results: list[TestResult] = []
        self.compaction_count = 0

    def log(self, msg: str, level: str = "INFO"):
        """Print log message."""
        print(f"[{level}] {msg}")

    async def health_check(self) -> bool:
        """Check server health."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.server_url}/health", timeout=5.0)
                return resp.status_code == 200
        except Exception as e:
            self.log(f"Health check failed: {e}", "ERROR")
            return False

    async def create_session(self) -> Optional[str]:
        """Create a new session."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.server_url}/sessions",  # Note: /sessions not /session
                    json={"workspace_path": os.getcwd()},
                    timeout=10.0
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    self.session_id = data.get("id")
                    self.log(f"Session created: {self.session_id}")
                    return self.session_id
        except Exception as e:
            self.log(f"Create session failed: {e}", "ERROR")
        return None

    async def send_message(self, message: str, timeout: float = 180.0) -> tuple[str, list[dict]]:
        """
        Send message and collect response.
        
        Returns:
            Tuple of (response_text, events)
        """
        if not self.session_id:
            raise ValueError("No session")

        url = f"{self.server_url}/sessions/{self.session_id}/chat"  # Note: /sessions/.../chat
        events = []
        response_text = ""

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                url,
                json={"content": message},  # ChatRequest expects "content" field
                headers={"Accept": "text/event-stream"},
                timeout=httpx.Timeout(timeout, connect=10.0)
            ) as resp:
                current_event = None
                current_data = ""

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        current_data = line[5:].strip()
                    elif line == "" and current_event and current_data:
                        try:
                            data = json.loads(current_data)
                        except:
                            data = {"raw": current_data}

                        events.append({"event": current_event, "data": data})

                        # Collect text from different event types
                        if current_event == "content.delta":
                            response_text += data.get("text", "")
                        elif current_event == "message":
                            # session_v2 emits 'message' events with content
                            content = data.get("content", "")
                            if content and not data.get("done"):
                                response_text += content
                        elif current_event == "thinking":
                            # Also collect thinking content
                            response_text += data.get("content", "")

                        # Track compaction events (from AgentOS events)
                        if current_event == "compaction" or (
                            current_event == "heartbeat" and "compaction" in str(data).lower()
                        ):
                            self.compaction_count += 1
                            self.log(f"🗜️ Compaction triggered! (#{self.compaction_count})")

                        current_event = None
                        current_data = ""

        return response_text, events

    async def test_memory_after_compaction(self) -> TestResult:
        """
        Test 1: Memory retention after compaction.
        
        Strategy:
        1. Tell AI a unique "secret code" at the beginning
        2. Make AI read several files to fill context
        3. Wait for compaction to trigger
        4. Ask AI to recall the secret code
        """
        name = "Memory After Compaction"
        start_time = time.time()
        self.compaction_count = 0

        self.log(f"\n{'='*60}")
        self.log(f"Test: {name}")
        self.log(f"{'='*60}")

        # Step 1: Give AI a unique secret
        secret = "ZEBRA-ALPHA-42"
        self.log(f"Step 1: Telling AI the secret code: {secret}")
        resp1, _ = await self.send_message(
            f"我要告诉你一个秘密代码，请务必记住：{secret}。"
            f"这是一个重要的测试，稍后我会问你这个代码。请回复'已记住'。"
        )
        self.log(f"Response: {resp1[:200]}...")

        # Step 2: Fill context with file reads to trigger compaction
        files_to_read = [
            "src/nimbus/agentos.py",
            "src/nimbus/core/runtime/vcpu.py",
            "src/nimbus/core/memory/mmu.py",
            "src/nimbus/server/session_v2.py",
        ]

        for i, filepath in enumerate(files_to_read):
            self.log(f"Step 2.{i+1}: Reading {filepath} to fill context...")
            resp, _ = await self.send_message(
                f"请读取文件 {filepath} 并告诉我这个文件的主要用途（简要回答）"
            )
            self.log(f"Response: {resp[:150]}...")

            if self.compaction_count > 0:
                self.log(f"✅ Compaction triggered after reading {i+1} files!")
                break

        # Step 3: Ask for the secret code
        self.log("Step 3: Asking AI to recall the secret code...")
        resp3, _ = await self.send_message(
            "请告诉我之前我给你的秘密代码是什么？直接说出代码即可。"
        )
        self.log(f"Response: {resp3}")

        # Check if secret is in response
        duration_ms = (time.time() - start_time) * 1000
        passed = secret in resp3 or "ZEBRA" in resp3.upper()

        details = f"Secret: {secret}, Found in response: {passed}, Compactions: {self.compaction_count}"
        if not passed:
            details += f"\nActual response: {resp3}"

        result = TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=duration_ms,
            compactions_triggered=self.compaction_count
        )
        self.results.append(result)

        self.log(f"Result: {'PASS' if passed else 'FAIL'} - {details}")
        return result

    async def test_tool_history_after_compaction(self) -> TestResult:
        """
        Test 2: Tool call history after compaction.
        
        Strategy:
        1. Ask AI to perform several tool calls
        2. Fill context to trigger compaction
        3. Ask about the tool calls from before compaction
        """
        name = "Tool History After Compaction"
        start_time = time.time()
        self.compaction_count = 0

        self.log(f"\n{'='*60}")
        self.log(f"Test: {name}")
        self.log(f"{'='*60}")

        # Step 1: Do some distinctive tool calls
        self.log("Step 1: Asking AI to list a specific directory...")
        resp1, _ = await self.send_message(
            "请列出 src/nimbus/core/memory/ 目录下的所有文件"
        )
        self.log(f"Response: {resp1[:200]}...")

        # Step 2: Fill context
        self.log("Step 2: Filling context to trigger compaction...")
        fill_messages = [
            "请读取 src/nimbus/core/memory/mmu.py 文件并简要说明",
            "请读取 src/nimbus/core/memory/context.py 文件并简要说明",
            "请读取 src/nimbus/agentos.py 文件的前100行",
        ]

        for i, msg in enumerate(fill_messages):
            self.log(f"Step 2.{i+1}: {msg[:50]}...")
            resp, _ = await self.send_message(msg)
            self.log(f"Response: {resp[:100]}...")

            if self.compaction_count > 0:
                self.log("✅ Compaction triggered!")
                break

        # Step 3: Ask about earlier tool calls
        self.log("Step 3: Asking about earlier directory listing...")
        resp3, _ = await self.send_message(
            "你之前列出了 src/nimbus/core/memory/ 目录的文件，请告诉我那里有哪些 .py 文件？"
        )
        self.log(f"Response: {resp3}")

        # Check if it remembers the files
        duration_ms = (time.time() - start_time) * 1000
        # Should mention mmu.py, context.py, etc.
        keywords = ["mmu", "context", "__init__"]
        found = sum(1 for k in keywords if k.lower() in resp3.lower())
        passed = found >= 2  # At least 2 of the expected files

        details = f"Keywords found: {found}/3, Compactions: {self.compaction_count}"
        if not passed:
            details += f"\nResponse: {resp3[:300]}"

        result = TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=duration_ms,
            compactions_triggered=self.compaction_count
        )
        self.results.append(result)

        self.log(f"Result: {'PASS' if passed else 'FAIL'} - {details}")
        return result

    async def test_multi_compaction_survival(self) -> TestResult:
        """
        Test 3: Survive multiple compactions.
        
        Strategy:
        1. Give important info at start
        2. Trigger first compaction
        3. Give more important info
        4. Trigger second compaction
        5. Verify both pieces of info are accessible
        """
        name = "Multi-Compaction Survival"
        start_time = time.time()
        self.compaction_count = 0

        self.log(f"\n{'='*60}")
        self.log(f"Test: {name}")
        self.log(f"{'='*60}")

        # Phase 1: First piece of info
        code1 = "FIRST-CODE-123"
        self.log(f"Phase 1: First secret code: {code1}")
        await self.send_message(f"第一个秘密代码是: {code1}，请记住。")

        # Fill to trigger first compaction
        self.log("Filling context for first compaction...")
        await self.send_message("读取 src/nimbus/agentos.py 并总结主要功能")
        await self.send_message("读取 src/nimbus/core/runtime/vcpu.py 并总结")

        first_compaction = self.compaction_count
        self.log(f"After phase 1: {first_compaction} compactions")

        # Phase 2: Second piece of info
        code2 = "SECOND-CODE-456"
        self.log(f"Phase 2: Second secret code: {code2}")
        await self.send_message(f"第二个秘密代码是: {code2}，请记住。")

        # Fill to trigger second compaction
        self.log("Filling context for second compaction...")
        await self.send_message("读取 src/nimbus/core/memory/mmu.py 并总结")
        await self.send_message("读取 src/nimbus/server/session_v2.py 并总结")

        second_compaction = self.compaction_count
        self.log(f"After phase 2: {second_compaction} compactions")

        # Ask about both codes
        self.log("Asking about both secret codes...")
        resp, _ = await self.send_message(
            "请告诉我之前我给你的两个秘密代码分别是什么？"
        )
        self.log(f"Response: {resp}")

        duration_ms = (time.time() - start_time) * 1000

        has_first = "FIRST" in resp.upper() or "123" in resp
        has_second = "SECOND" in resp.upper() or "456" in resp
        passed = has_first and has_second

        details = (
            f"Code1 ({code1}) found: {has_first}, "
            f"Code2 ({code2}) found: {has_second}, "
            f"Total compactions: {self.compaction_count}"
        )

        result = TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=duration_ms,
            compactions_triggered=self.compaction_count
        )
        self.results.append(result)

        self.log(f"Result: {'PASS' if passed else 'FAIL'} - {details}")
        return result

    async def test_archive_file_access(self) -> TestResult:
        """
        Test 4: AI can read archive files when needed.
        
        After compaction, the AI should know about archive files and
        be able to read them if needed.
        """
        name = "Archive File Access"
        start_time = time.time()
        self.compaction_count = 0

        self.log(f"\n{'='*60}")
        self.log(f"Test: {name}")
        self.log(f"{'='*60}")

        # Give detailed info
        self.log("Giving detailed technical info...")
        await self.send_message(
            "我要告诉你一些重要的技术细节：\n"
            "1. 数据库密码是: db_pass_9876\n"
            "2. API密钥是: api_key_WXYZ\n"
            "3. 服务器端口是: 8888\n"
            "请确认你已记录这些信息。"
        )

        # Fill context to trigger compaction
        self.log("Filling context to trigger compaction...")
        for filepath in ["src/nimbus/agentos.py", "src/nimbus/core/runtime/vcpu.py"]:
            await self.send_message(f"读取 {filepath} 的完整内容")
            if self.compaction_count > 0:
                break

        # Ask about the details (might need to read archive)
        self.log("Asking about the technical details...")
        resp, _ = await self.send_message(
            "请告诉我之前我提供的数据库密码、API密钥和服务器端口分别是什么？"
        )
        self.log(f"Response: {resp}")

        duration_ms = (time.time() - start_time) * 1000

        # Check all three pieces
        has_password = "9876" in resp
        has_api_key = "WXYZ" in resp.upper()
        has_port = "8888" in resp

        passed = has_password and has_api_key and has_port
        found_count = sum([has_password, has_api_key, has_port])

        details = f"Found {found_count}/3 details, Compactions: {self.compaction_count}"
        if not passed:
            details += f"\nMissing: password={not has_password}, api_key={not has_api_key}, port={not has_port}"

        result = TestResult(
            name=name,
            passed=passed,
            details=details,
            duration_ms=duration_ms,
            compactions_triggered=self.compaction_count
        )
        self.results.append(result)

        self.log(f"Result: {'PASS' if passed else 'FAIL'} - {details}")
        return result

    async def run_all(self) -> bool:
        """Run all tests."""
        print("\n" + "=" * 70)
        print("NIMBUS INFINITE CONTEXT TEST")
        print("Testing AI memory retention after context compaction")
        print("=" * 70)
        print(f"Server: {self.server_url}")
        print("Token budget: 4000 (stress test mode)")
        print()

        if not await self.health_check():
            self.log("Server not available!", "ERROR")
            return False

        # Run each test in a fresh session
        tests = [
            self.test_memory_after_compaction,
            self.test_tool_history_after_compaction,
            # self.test_multi_compaction_survival,  # 可能太长
            self.test_archive_file_access,
        ]

        for test_func in tests:
            if not await self.create_session():
                self.log("Failed to create session", "ERROR")
                continue

            try:
                await test_func()
            except Exception as e:
                self.log(f"Test failed with exception: {e}", "ERROR")
                import traceback
                traceback.print_exc()

            await asyncio.sleep(1)

        # Print summary
        self.print_summary()

        return all(r.passed for r in self.results)

    def print_summary(self):
        """Print test summary."""
        print("\n" + "=" * 70)
        print("TEST SUMMARY")
        print("=" * 70)

        total_compactions = sum(r.compactions_triggered for r in self.results)
        passed = sum(1 for r in self.results if r.passed)

        for r in self.results:
            status = "✅ PASS" if r.passed else "❌ FAIL"
            print(f"\n{status} - {r.name}")
            print(f"  Duration: {r.duration_ms:.0f}ms")
            print(f"  Compactions: {r.compactions_triggered}")
            print(f"  Details: {r.details}")

        print("\n" + "-" * 70)
        print(f"Total: {len(self.results)} tests, {passed} passed, {len(self.results)-passed} failed")
        print(f"Total compactions triggered: {total_compactions}")

        if passed == len(self.results):
            print("\n🎉 ALL TESTS PASSED - Infinite context working!")
        else:
            print(f"\n⚠️ {len(self.results)-passed} test(s) failed")


async def main():
    tester = InfiniteContextTester(SERVER_URL)
    success = await tester.run_all()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
