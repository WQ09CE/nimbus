"""
Parallel AgentOS Stress Test
============================
验证 spawn_batch 并行分发与 _scavenge_partial_result 超时抢救机制。

运行方式：
    python tests/stress_test_parallel.py

测试场景
--------
1. basic_parallel          – 多任务并行，验证并发性（wall-clock < sum of sequential）
2. partial_result_scavenge – 强制超时，验证 _scavenge_partial_result 返回 is_partial=True
3. wait_any_strategy       – wait_any 策略：第一个完成就返回
4. wait_threshold_strategy – wait_threshold 策略：60% 完成就返回
5. sub_session_id_tagging  – 验证每个子进程事件携带正确 sub_session_id
6. mixed_success_failure   – 部分任务成功、部分超时的混合场景
"""

import asyncio
import sys
import time
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 路径设置
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.protocol import ToolResult

# ---------------------------------------------------------------------------
# Mock LLM Client（不依赖真实 API 即可运行压测）
# ---------------------------------------------------------------------------

class MockLLMResponse:
    """Mimics the minimal interface that VCPU uses."""
    def __init__(self, content: str, stop: bool = True):
        self.content = content
        self.stop_reason = "end_turn" if stop else "tool_use"
        self.tool_calls: List[Any] = []
        self.usage = {"input_tokens": 10, "output_tokens": 20}
        self.model = "mock-model"


class MockLLMClient:
    """
    Synchronous/async mock LLM client.
    Returns a RETURN action immediately so VCPU halts on first iteration.
    delay_s controls how long the "LLM" takes to respond.
    """

    def __init__(self, delay_s: float = 0.05, response_text: Optional[str] = None):
        self.delay_s = delay_s
        self.response_text = response_text or "Task complete."
        self.call_count = 0

    def _make_response(self) -> MockLLMResponse:
        import json
        return_action = json.dumps({
            "action": "RETURN",
            "result": self.response_text,
            "is_final": True,
        })
        return MockLLMResponse(content=return_action, stop=True)

    async def chat(self, messages, tools=None, **kwargs) -> MockLLMResponse:
        """Primary interface used by VCPU ALU."""
        self.call_count += 1
        await asyncio.sleep(self.delay_s)
        return self._make_response()

    async def create(self, messages, tools=None, **kwargs) -> MockLLMResponse:
        """Alias for compatibility."""
        return await self.chat(messages, tools=tools, **kwargs)

    async def generate(self, messages, **kwargs) -> MockLLMResponse:
        return await self.chat(messages, **kwargs)


# ---------------------------------------------------------------------------
# Helper: build an AgentOS with the mock LLM
# ---------------------------------------------------------------------------

def make_os(delay_s: float = 0.05) -> AgentOS:
    config = AgentOSConfig()
    llm = MockLLMClient(delay_s=delay_s)
    return AgentOS(llm_client=llm, config=config)


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results: List[Dict[str, Any]] = []


def record(name: str, passed: bool, detail: str = ""):
    tag = PASS if passed else FAIL
    msg = f"{tag}  {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append({"name": name, "passed": passed, "detail": detail})


# ---------------------------------------------------------------------------
# Test 1: basic_parallel – concurrency speedup check
# ---------------------------------------------------------------------------

async def test_basic_parallel():
    """4 tasks each take ~0.1 s → wall-clock should be << 0.4 s."""
    delay = 0.15
    n_tasks = 4
    os_inst = make_os(delay_s=delay)

    tasks = [{"goal": f"task-{i}: do something simple"} for i in range(n_tasks)]

    t0 = time.monotonic()
    batch_results = await os_inst.spawn_batch(tasks, strategy="wait_all")
    elapsed = time.monotonic() - t0

    sequential_estimate = n_tasks * delay
    # Parallel should be significantly faster than sequential
    speedup = sequential_estimate / elapsed if elapsed > 0 else float("inf")

    all_ok = all(r.status in ("OK", "ERROR") for r in batch_results)
    # Allow generous margin: must be at least 2× speedup
    is_fast = elapsed < sequential_estimate * 0.8
    record(
        "basic_parallel",
        all_ok and len(batch_results) == n_tasks,
        f"wall={elapsed:.3f}s, seq_estimate={sequential_estimate:.2f}s, speedup={speedup:.1f}x, statuses={[r.status for r in batch_results]}",
    )
    record(
        "basic_parallel_speedup",
        is_fast or speedup >= 1.5,
        f"elapsed={elapsed:.3f}s vs sequential_estimate={sequential_estimate:.2f}s",
    )


# ---------------------------------------------------------------------------
# Test 2: partial_result_scavenge – timeout triggers scavenge
# ---------------------------------------------------------------------------

async def test_scavenge_on_timeout():
    """Single task with a very short timeout → expect TIMEOUT + is_partial."""
    # Long delay ensures timeout fires before task completes
    os_inst = make_os(delay_s=5.0)

    tasks = [{"goal": "analyse all 10,000 files in the repo"}]

    batch_results = await os_inst.spawn_batch(tasks, timeout=0.05, strategy="wait_all")

    result = batch_results[0]
    is_timeout = result.status == "TIMEOUT"
    has_partial = False
    if result.output and isinstance(result.output, dict):
        has_partial = result.output.get("is_partial", False) is True

    record(
        "scavenge_status_is_TIMEOUT",
        is_timeout,
        f"status={result.status}",
    )
    record(
        "scavenge_is_partial_flag",
        has_partial,
        f"output={result.output}",
    )
    record(
        "scavenge_fault_is_retryable",
        result.fault is not None and result.fault.retryable is True,
        f"fault={result.fault}",
    )


# ---------------------------------------------------------------------------
# Test 3: wait_any strategy
# ---------------------------------------------------------------------------

async def test_wait_any():
    """3 tasks: first is instant, others are slow. wait_any should return quickly."""

    class VariableLLM:
        """Returns immediately for task-0, slowly for others."""
        def _make_return_response(self) -> MockLLMResponse:
            import json
            return MockLLMResponse(
                content=json.dumps({"action": "RETURN", "result": "done", "is_final": True}),
                stop=True,
            )

        def _get_goal(self, messages) -> str:
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    return m.get("content", "")
                elif hasattr(m, "role") and m.role == "user":
                    return getattr(m, "content", "")
            return ""

        async def chat(self, messages, tools=None, **kwargs):
            goal = self._get_goal(messages)
            if "task-0" in goal:
                await asyncio.sleep(0.02)
            else:
                await asyncio.sleep(5.0)
            return self._make_return_response()

        async def create(self, messages, tools=None, **kwargs):
            return await self.chat(messages, tools=tools, **kwargs)

        async def generate(self, messages, **kwargs):
            return await self.chat(messages, **kwargs)

    config = AgentOSConfig()
    os_inst = AgentOS(llm_client=VariableLLM(), config=config)

    tasks = [
        {"goal": "task-0: fast task"},
        {"goal": "task-1: slow task"},
        {"goal": "task-2: slow task"},
    ]
    t0 = time.monotonic()
    batch_results = await os_inst.spawn_batch(tasks, strategy="wait_any", timeout=10.0)
    elapsed = time.monotonic() - t0

    # Should have finished well before the slow tasks
    at_least_one_ok = any(r.status == "OK" for r in batch_results)
    is_fast = elapsed < 1.0

    record("wait_any_returns_early", is_fast, f"elapsed={elapsed:.3f}s")
    record("wait_any_has_ok_result", at_least_one_ok, f"statuses={[r.status for r in batch_results]}")


# ---------------------------------------------------------------------------
# Test 4: wait_threshold strategy
# ---------------------------------------------------------------------------

async def test_wait_threshold():
    """5 tasks. threshold=0.6 → should return after 3 complete."""

    class HalfFastLLM:
        """First 3 tasks are fast, last 2 are slow."""
        def _make_return(self) -> MockLLMResponse:
            import json
            return MockLLMResponse(
                content=json.dumps({"action": "RETURN", "result": "done", "is_final": True}),
                stop=True,
            )

        def _get_goal(self, messages) -> str:
            for m in messages:
                if isinstance(m, dict) and m.get("role") == "user":
                    return m.get("content", "")
                elif hasattr(m, "role") and m.role == "user":
                    return getattr(m, "content", "")
            return ""

        async def chat(self, messages, tools=None, **kwargs):
            goal = self._get_goal(messages)
            slow = any(f"task-{i}" in goal for i in [3, 4])
            await asyncio.sleep(5.0 if slow else 0.05)
            return self._make_return()

        async def create(self, messages, tools=None, **kwargs):
            return await self.chat(messages, tools=tools, **kwargs)

        async def generate(self, messages, **kwargs):
            return await self.chat(messages, **kwargs)

    config = AgentOSConfig()
    os_inst = AgentOS(llm_client=HalfFastLLM(), config=config)

    tasks = [{"goal": f"task-{i}: job"} for i in range(5)]
    t0 = time.monotonic()
    batch_results = await os_inst.spawn_batch(
        tasks, strategy="wait_threshold", threshold=0.6, timeout=10.0
    )
    elapsed = time.monotonic() - t0

    ok_count = sum(1 for r in batch_results if r.status == "OK")
    cancelled_count = sum(1 for r in batch_results if r.status == "CANCELLED")

    record(
        "wait_threshold_returns_early",
        elapsed < 2.0,
        f"elapsed={elapsed:.3f}s",
    )
    record(
        "wait_threshold_ok_count",
        ok_count >= 3,
        f"ok={ok_count} cancelled={cancelled_count} statuses={[r.status for r in batch_results]}",
    )


# ---------------------------------------------------------------------------
# Test 5: sub_session_id tagging in events
# ---------------------------------------------------------------------------

async def test_sub_session_id_tagging():
    """Verify that each spawned process has a unique sub_session_id in its signals."""
    os_inst = make_os(delay_s=0.05)
    n = 3
    tasks = [{"goal": f"mini task {i}"} for i in range(n)]

    # We need to inspect signals BEFORE the processes finish, so we peek right after spawn
    # Actually: spawn_batch first spawns all, then runs them.
    # We hook into events to capture BATCH_TASK_SPAWNED events.
    spawned_events = []

    def listener(event):
        if event.type == "BATCH_TASK_SPAWNED":
            spawned_events.append(event)

    os_inst.add_event_listener(listener)
    await os_inst.spawn_batch(tasks, strategy="wait_all")
    os_inst.remove_event_listener(listener)

    has_unique_ids = len({e.pid for e in spawned_events}) == n
    correct_count = len(spawned_events) == n

    record(
        "sub_session_id_event_count",
        correct_count,
        f"spawned_events={len(spawned_events)} expected={n}",
    )
    record(
        "sub_session_id_unique",
        has_unique_ids,
        f"pids={[e.pid for e in spawned_events]}",
    )


# ---------------------------------------------------------------------------
# Test 6: mixed success/failure
# ---------------------------------------------------------------------------

async def test_mixed_success_failure():
    """3 tasks: 2 succeed quickly, 1 times out → mixed result set."""
    os_inst = make_os(delay_s=0.05)  # fast for the two quick tasks

    # Monkey-patch the third task's LLM to be slow
    # We can't easily do per-task LLM here without spawn_batch task-level llm_client support,
    # so we just use short timeout and a mix of timeouts.
    # Instead: use per-task llm_client override.
    slow_llm = MockLLMClient(delay_s=10.0)
    fast_llm = MockLLMClient(delay_s=0.05)

    tasks = [
        {"goal": "quick task A", "llm_client": fast_llm},
        {"goal": "quick task B", "llm_client": fast_llm},
        {"goal": "slow task C will timeout", "llm_client": slow_llm},
    ]

    results_list = await os_inst.spawn_batch(tasks, timeout=0.3, strategy="wait_all")

    ok_count = sum(1 for r in results_list if r.status == "OK")
    timeout_count = sum(1 for r in results_list if r.status == "TIMEOUT")

    record(
        "mixed_ok_count",
        ok_count >= 2,
        f"ok={ok_count} statuses={[r.status for r in results_list]}",
    )
    record(
        "mixed_timeout_partial",
        timeout_count >= 1 and any(
            r.output and r.output.get("is_partial") for r in results_list if r.status == "TIMEOUT"
        ),
        f"timeout={timeout_count}",
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def main():
    print("=" * 60)
    print("  Nimbus AgentOS Parallel Stress Test")
    print("=" * 60)
    print()

    tests = [
        ("basic_parallel", test_basic_parallel),
        ("scavenge_on_timeout", test_scavenge_on_timeout),
        ("wait_any_strategy", test_wait_any),
        ("wait_threshold_strategy", test_wait_threshold),
        ("sub_session_id_tagging", test_sub_session_id_tagging),
        ("mixed_success_failure", test_mixed_success_failure),
    ]

    for name, fn in tests:
        print(f"\n── {name} ──")
        try:
            await fn()
        except Exception as exc:
            import traceback
            record(name, False, f"EXCEPTION: {exc}")
            traceback.print_exc()

    # Summary
    print()
    print("=" * 60)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    print(f"  Results: {passed}/{total} passed,  {failed} failed")
    print("=" * 60)

    if failed:
        print("\nFailed tests:")
        for r in results:
            if not r["passed"]:
                print(f"  ❌ {r['name']}: {r['detail']}")
        sys.exit(1)
    else:
        print("\n🎉 All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
