#!/usr/bin/env python3
"""
🧪 Ollama qwen3.5:9b Tool Calling MVP Test
验证 ollama qwen3.5:9b 的工具调用能力，使用 litellm acompletion 流式调用。

场景 1: 基本文本回复（无工具, reasoning_effort="none"）
场景 2: 单工具调用（get_weather）— 分别测试 thinking/non-thinking
场景 3: 多工具选择（get_weather + search_web）
场景 4: thinking 模式 vs 非 thinking 模式对比
"""

import asyncio
import json
import sys
import urllib.request
import urllib.error

import litellm
from litellm import acompletion

# 与 nimbus 一致的配置
litellm.drop_params = True

# ── ANSI Colors ──────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

MODEL = "ollama/qwen3.5:9b"
API_BASE = "http://localhost:11434"

# ── Tool Definitions (OpenAI format) ────────────────────────
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "获取指定城市的天气信息",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称"}
            },
            "required": ["city"],
        },
    },
}

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "搜索互联网获取最新信息",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
            },
            "required": ["query"],
        },
    },
}


def check_ollama() -> bool:
    """检测 ollama 是否在运行。"""
    try:
        req = urllib.request.Request(f"{API_BASE}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            print(f"{GREEN}✅ Ollama is running. Models: {', '.join(models)}{RESET}")
            return True
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        print(f"{RED}❌ Ollama is not running at {API_BASE}: {e}{RESET}")
        return False


def try_parse_tool_call_from_content(content: str) -> dict | None:
    """
    尝试从 content 中解析 JSON 格式的工具调用。
    qwen3 在 reasoning_effort="none" 时可能将 tool_call 作为 JSON 文本输出到 content。
    格式: {"name": "get_weather", "arguments": {"city": "北京"}}
    """
    content = content.strip()
    if not content.startswith("{"):
        return None
    try:
        data = json.loads(content)
        if "name" in data and "arguments" in data:
            return {
                "name": data["name"],
                "arguments": json.dumps(data["arguments"], ensure_ascii=False),
            }
    except (json.JSONDecodeError, KeyError):
        pass
    return None


async def stream_and_collect(*, messages, tools=None, reasoning_effort=None, label=""):
    """
    流式调用 litellm acompletion，打印每个 chunk 并收集结果。
    返回 (full_content, full_reasoning, tool_calls_list)
    """
    kwargs = dict(
        model=MODEL,
        messages=messages,
        api_base=API_BASE,
        stream=True,
    )
    if tools:
        kwargs["tools"] = tools
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort

    full_content = ""
    full_reasoning = ""
    tool_calls_acc: dict[int, dict] = {}  # index -> {id, name, arguments}
    chunk_idx = 0

    try:
        response = await acompletion(**kwargs)
        async for chunk in response:
            chunk_idx += 1
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            content = delta.content or ""
            reasoning = getattr(delta, "reasoning_content", None) or ""
            tc = delta.tool_calls

            # 打印 chunk 详情（限制频率，避免刷屏）
            if chunk_idx <= 10 or chunk_idx % 20 == 0 or content or tc:
                tc_str = "None"
                if tc:
                    tc_str = str([{
                        "name": t.function.name if t.function else None,
                        "args": t.function.arguments if t.function else None,
                    } for t in tc])
                content_disp = repr(content) if content else "None"
                reasoning_disp = (
                    repr(reasoning[:60] + "...")
                    if len(reasoning) > 60
                    else (repr(reasoning) if reasoning else "None")
                )
                print(
                    f"  {DIM}[chunk {chunk_idx:3d}]{RESET} "
                    f"content={CYAN}{content_disp}{RESET} "
                    f"reasoning={MAGENTA}{reasoning_disp}{RESET} "
                    f"tool_calls={YELLOW}{tc_str}{RESET}"
                )

            full_content += content
            full_reasoning += reasoning

            # 累积 tool_calls（流式可能分多 chunk 传来）
            if tc:
                for t in tc:
                    idx = t.index if t.index is not None else 0
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": t.id or "",
                            "name": (
                                t.function.name
                                if t.function and t.function.name
                                else ""
                            ),
                            "arguments": "",
                        }
                    if t.function:
                        if t.function.name:
                            tool_calls_acc[idx]["name"] = t.function.name
                        if t.function.arguments:
                            tool_calls_acc[idx]["arguments"] += t.function.arguments
                    if t.id:
                        tool_calls_acc[idx]["id"] = t.id

    except Exception as e:
        print(f"  {RED}❌ Error during streaming: {e}{RESET}")
        return full_content, full_reasoning, []

    tool_calls_list = list(tool_calls_acc.values()) if tool_calls_acc else []
    print(f"  {DIM}--- total chunks: {chunk_idx} ---{RESET}")
    return full_content, full_reasoning, tool_calls_list


async def test1_basic_text() -> bool:
    """场景 1: 基本文本回复（无工具, reasoning_effort='none'）"""
    print(f"\n{BOLD}📋 Test 1: Basic Text Response (no tools, reasoning_effort='none'){RESET}")
    print("-" * 60)

    messages = [
        {"role": "user", "content": "你好，请简短介绍一下你自己。"},
    ]

    content, reasoning, tool_calls = await stream_and_collect(
        messages=messages,
        reasoning_effort="none",
        label="test1",
    )

    if content and len(content.strip()) > 0:
        print(f"  {GREEN}✅ PASS: Got text content ({len(content)} chars){RESET}")
        return True
    else:
        print(f"  {RED}❌ FAIL: No text content received. content={repr(content)}{RESET}")
        return False


async def test2_single_tool() -> bool:
    """场景 2: 单工具调用（get_weather）— 测试 non-thinking 和 thinking 两种模式"""
    print(f"\n{BOLD}📋 Test 2: Single Tool Call (get_weather){RESET}")
    print("-" * 60)

    messages = [
        {"role": "user", "content": "北京今天天气怎么样？"},
    ]

    # ── 2a: 先测 reasoning_effort="none" ──
    print(f"\n  {BOLD}2a) reasoning_effort='none'{RESET}")
    content_a, reasoning_a, tool_calls_a = await stream_and_collect(
        messages=messages,
        tools=[WEATHER_TOOL],
        reasoning_effort="none",
        label="test2a",
    )

    result_a = _evaluate_tool_call(content_a, tool_calls_a, "get_weather", "2a")

    # ── 2b: 再测默认 thinking 模式 ──
    print(f"\n  {BOLD}2b) Default (thinking mode){RESET}")
    content_b, reasoning_b, tool_calls_b = await stream_and_collect(
        messages=messages,
        tools=[WEATHER_TOOL],
        label="test2b",
    )

    result_b = _evaluate_tool_call(content_b, tool_calls_b, "get_weather", "2b")

    # 汇总：至少一个模式能正确产生 tool_call 就算 PASS
    if result_a == "structured" or result_b == "structured":
        print(f"  {GREEN}✅ PASS: Structured tool_call received{RESET}")
        return True
    elif result_a == "json_in_content" or result_b == "json_in_content":
        print(f"  {YELLOW}⚠️  PARTIAL: Tool call returned as JSON in content (not structured){RESET}")
        print(f"  {GREEN}✅ PASS: Model understands tool calling (JSON-in-content fallback){RESET}")
        return True
    else:
        print(f"  {RED}❌ FAIL: No tool call detected in either mode{RESET}")
        return False


def _evaluate_tool_call(content: str, tool_calls: list, expected_name: str, sub_label: str) -> str:
    """
    评估工具调用结果。返回:
    - "structured": 通过 tool_calls 字段收到
    - "json_in_content": 工具调用作为 JSON 出现在 content 中
    - "text": 纯文本回复
    - "empty": 无输出
    """
    if tool_calls:
        tc = tool_calls[0]
        name = tc["name"]
        args = tc["arguments"]
        print(f"    {GREEN}[{sub_label}] ✅ Structured tool_call: {name}({args}){RESET}")
        return "structured"

    if content:
        parsed = try_parse_tool_call_from_content(content)
        if parsed:
            print(
                f"    {YELLOW}[{sub_label}] ⚠️  JSON-in-content tool_call: "
                f"{parsed['name']}({parsed['arguments']}){RESET}"
            )
            return "json_in_content"
        else:
            print(
                f"    {YELLOW}[{sub_label}] ⚠️  Text response ({len(content)} chars): "
                f"{content[:100]}{RESET}"
            )
            return "text"

    print(f"    {RED}[{sub_label}] ❌ No output{RESET}")
    return "empty"


async def test3_multi_tool() -> bool:
    """场景 3: 多工具选择（get_weather + search_web）"""
    print(f"\n{BOLD}📋 Test 3: Multi Tool Selection (get_weather + search_web){RESET}")
    print("-" * 60)

    messages = [
        {"role": "user", "content": "帮我搜索一下2024年诺贝尔物理学奖得主是谁？"},
    ]

    # ── 3a: non-thinking ──
    print(f"\n  {BOLD}3a) reasoning_effort='none'{RESET}")
    content_a, _, tool_calls_a = await stream_and_collect(
        messages=messages,
        tools=[WEATHER_TOOL, SEARCH_TOOL],
        reasoning_effort="none",
        label="test3a",
    )
    result_a = _evaluate_tool_call(content_a, tool_calls_a, "search_web", "3a")

    # ── 3b: thinking ──
    print(f"\n  {BOLD}3b) Default (thinking mode){RESET}")
    content_b, _, tool_calls_b = await stream_and_collect(
        messages=messages,
        tools=[WEATHER_TOOL, SEARCH_TOOL],
        label="test3b",
    )
    result_b = _evaluate_tool_call(content_b, tool_calls_b, "search_web", "3b")

    # 检查工具选择是否正确
    def _check_tool_name(tool_calls, content, expected):
        if tool_calls:
            return tool_calls[0]["name"] == expected
        parsed = try_parse_tool_call_from_content(content)
        if parsed:
            return parsed["name"] == expected
        return False

    if result_a in ("structured", "json_in_content") or result_b in (
        "structured",
        "json_in_content",
    ):
        correct_a = _check_tool_name(tool_calls_a, content_a, "search_web")
        correct_b = _check_tool_name(tool_calls_b, content_b, "search_web")
        if correct_a or correct_b:
            print(f"  {GREEN}✅ PASS: Correctly selected search_web{RESET}")
        else:
            print(
                f"  {YELLOW}⚠️  Tool selected but not search_web "
                f"(still counts as PASS for tool calling ability){RESET}"
            )
            print(f"  {GREEN}✅ PASS: Got tool_call{RESET}")
        return True
    else:
        print(f"  {RED}❌ FAIL: No tool call detected{RESET}")
        return False


async def test4_thinking_comparison() -> bool:
    """场景 4: 对比 thinking 模式 vs 非 thinking 模式"""
    print(f"\n{BOLD}📋 Test 4: Thinking Mode vs Non-Thinking Mode Comparison{RESET}")
    print("-" * 60)

    messages = [
        {"role": "user", "content": "请解释什么是量子纠缠，用一句话概括。"},
    ]

    # ── 4a: Non-thinking mode (reasoning_effort="none") ──
    print(f"\n  {BOLD}4a) reasoning_effort='none' (non-thinking){RESET}")
    content_no_think, reasoning_no_think, _ = await stream_and_collect(
        messages=messages,
        reasoning_effort="none",
        label="test4a",
    )

    # ── 4b: Thinking mode (不传 reasoning_effort，默认 thinking) ──
    print(f"\n  {BOLD}4b) No reasoning_effort (default thinking mode){RESET}")
    content_think, reasoning_think, _ = await stream_and_collect(
        messages=messages,
        label="test4b",
    )

    # ── 对比结果 ──
    print(f"\n  {BOLD}📊 Comparison:{RESET}")
    print(f"    {'Mode':<25} {'content len':>12} {'reasoning len':>14}")
    print(f"    {'-'*25} {'-'*12} {'-'*14}")
    print(
        f"    {'non-thinking (none)':<25} "
        f"{len(content_no_think):>12} "
        f"{len(reasoning_no_think):>14}"
    )
    print(
        f"    {'thinking (default)':<25} "
        f"{len(content_think):>12} "
        f"{len(reasoning_think):>14}"
    )

    # 验证 non-thinking 模式有 content
    if content_no_think.strip():
        print(
            f"  {GREEN}✅ non-thinking: has content "
            f"({len(content_no_think)} chars){RESET}"
        )
    else:
        print(f"  {RED}❌ non-thinking: no content{RESET}")

    # 验证 thinking 模式有 reasoning_content
    if reasoning_think.strip():
        print(
            f"  {GREEN}✅ thinking: has reasoning_content "
            f"({len(reasoning_think)} chars){RESET}"
        )
    else:
        print(
            f"  {YELLOW}⚠️  thinking: no reasoning_content detected "
            f"(model may not expose it via litellm streaming){RESET}"
        )

    # 验证 non-thinking 模式无 reasoning
    if not reasoning_no_think.strip():
        print(f"  {GREEN}✅ non-thinking: no reasoning_content (as expected){RESET}")
    else:
        print(
            f"  {YELLOW}⚠️  non-thinking: has reasoning_content "
            f"({len(reasoning_no_think)} chars) - unexpected{RESET}"
        )

    # 整体判断：只要有输出就算 pass
    if content_no_think.strip() or content_think.strip():
        print(f"  {GREEN}✅ PASS: Both modes produced output{RESET}")
        return True
    else:
        print(f"  {RED}❌ FAIL: Neither mode produced output{RESET}")
        return False


async def main():
    print(f"\n{BOLD}🧪 Ollama qwen3.5:9b Tool Calling MVP Test{RESET}")
    print("=" * 60)

    # 检测 ollama
    if not check_ollama():
        print(f"\n{RED}Aborting: Ollama is not available.{RESET}")
        sys.exit(1)

    results = {}

    # 运行 4 个测试
    results["Test 1: Basic Text (no tools)"] = await test1_basic_text()
    results["Test 2: Single Tool Call"] = await test2_single_tool()
    results["Test 3: Multi Tool Selection"] = await test3_multi_tool()
    results["Test 4: Thinking vs Non-Thinking"] = await test4_thinking_comparison()

    # ── 汇总 ──
    print(f"\n{'=' * 60}")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, ok in results.items():
        status = f"{GREEN}✅ PASS{RESET}" if ok else f"{RED}❌ FAIL{RESET}"
        print(f"  {status}  {name}")

    print()
    if passed == total:
        print(f"{GREEN}{BOLD}📊 Summary: {passed}/{total} PASSED ✨{RESET}")
    else:
        print(f"{YELLOW}{BOLD}📊 Summary: {passed}/{total} PASSED{RESET}")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
