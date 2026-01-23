#!/usr/bin/env python3
"""Demo showcasing TieredMemoryManager and Logging/Tracing system."""

import asyncio
import aiohttp


class OllamaClient:
    """Ollama LLM client."""

    def __init__(self, model: str = "gemma3n", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    async def complete(self, prompt: str) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 512},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Ollama error: {resp.status}")
                data = await resp.json()
                return data.get("response", "")


async def demo_tiered_memory():
    """Demo TieredMemoryManager features."""
    from nimbus.core.memory import (
        TieredMemoryManager, MemoryConfig, PinnedItem
    )

    print("=" * 60)
    print("  TieredMemoryManager Demo")
    print("=" * 60)

    # 创建配置 - 低阈值便于演示压缩
    config = MemoryConfig(
        pinned_budget=500,
        working_budget=1000,
        episodic_budget=2000,
        compression_threshold=3,  # 3 轮后压缩
        checkpoint_interval=5,
    )

    llm = OllamaClient(model="gemma3n")
    memory = TieredMemoryManager(config, llm_client=llm)

    # === Part 1: Pinned Layer ===
    print("\n[1] Pinned Layer (永不压缩)")
    print("-" * 40)

    memory.pin(PinnedItem(
        id="file_report",
        type="file_meta",
        content="用户上传了 Q3财务报告.pdf，共 50 页，包含收入、支出、利润分析",
        priority=10
    ))
    memory.pin(PinnedItem(
        id="user_goal",
        type="user_instruction",
        content="用户目标：分析报告中的关键数据并生成摘要",
        priority=8
    ))

    print(f"  Pinned items: {len(memory.get_pinned())}")
    for item in memory.get_pinned():
        print(f"    - [{item.type}] {item.content[:50]}...")

    # === Part 2: Working Layer ===
    print("\n[2] Working Layer (当前任务状态)")
    print("-" * 40)

    memory.set_working("current_phase", "数据提取")
    memory.set_working("progress", "25%")
    memory.set_working("key_finding", "Q3 收入增长 15%")

    print(f"  Working items: {len(memory.working)}")
    for k, v in memory.working.items():
        print(f"    - {k}: {v}")

    # === Part 3: Episodic Layer with Compression ===
    print("\n[3] Episodic Layer (对话历史 + 压缩)")
    print("-" * 40)

    # 添加多轮对话，观察压缩
    conversations = [
        ("user", "请帮我分析这份财务报告"),
        ("assistant", "好的，我来分析这份 Q3 财务报告。让我先看看主要数据..."),
        ("user", "收入情况怎么样？"),
        ("assistant", "根据报告，Q3 收入达到 500 万，同比增长 15%，主要来自产品 A 的销售。"),
        ("user", "利润率呢？"),
        ("assistant", "利润率为 22%，比去年同期提高了 3 个百分点，主要得益于成本控制。"),
        ("user", "有什么风险点吗？"),
        ("assistant", "主要风险包括：1) 供应链成本上涨 2) 市场竞争加剧 3) 汇率波动"),
    ]

    for i, (role, content) in enumerate(conversations):
        print(f"  Adding turn {i+1}: {role}: {content[:30]}...")
        await memory.add_turn(role, content)

        stats = memory.get_stats()
        if stats.compression_count > 0:
            print(f"    → Compression triggered! Count: {stats.compression_count}")

    print(f"\n  Final stats:")
    stats = memory.get_stats()
    print(f"    - Turn count: {stats.turn_count}")
    print(f"    - Compression count: {stats.compression_count}")
    print(f"    - Episodic tokens: {stats.episodic_tokens}")

    # === Part 4: Context Assembly ===
    print("\n[4] Context Assembly (上下文组装)")
    print("-" * 40)

    context = memory.get_context("总结报告要点")
    print("  Context preview (first 500 chars):")
    print("-" * 40)
    for line in context[:500].split("\n"):
        print(f"  {line}")
    print("  ...")

    # === Part 5: Checkpoint ===
    print("\n[5] Checkpoint (持久化)")
    print("-" * 40)

    # 设置 session_id 并保存
    memory.session_id = "demo_session"
    checkpoint_path = await memory.checkpoint()
    print(f"  Saved to: {checkpoint_path}")

    # 验证恢复
    new_memory = TieredMemoryManager(config)
    new_memory.session_id = "demo_session"
    restored = await new_memory.restore()
    print(f"  Restore success: {restored}")
    if restored:
        print(f"  Restored turn count: {new_memory.get_stats().turn_count}")

    return memory


async def demo_tracing():
    """Demo Tracing system."""
    from nimbus.core.tracing import get_tracer, trace

    print("\n" + "=" * 60)
    print("  Tracing System Demo")
    print("=" * 60)

    tracer = get_tracer()
    tracer.clear()

    # === Nested spans ===
    print("\n[1] Nested Spans")
    print("-" * 40)

    with tracer.start_span("agent.run", {"user_input": "分析报告"}) as root:
        root.add_event("input_received")

        with tracer.start_span("memory.get_context") as mem_span:
            await asyncio.sleep(0.01)  # 模拟操作
            mem_span.set_attribute("context_length", 1500)

        with tracer.start_span("planner.create_plan") as plan_span:
            await asyncio.sleep(0.02)
            plan_span.set_attribute("plan_mode", "multi_step")
            plan_span.set_attribute("task_count", 3)

        with tracer.start_span("executor.execute") as exec_span:
            for i in range(3):
                with tracer.start_span(f"task_{i}") as task_span:
                    await asyncio.sleep(0.01)
                    task_span.set_attribute("skill", f"skill_{i}")

        root.add_event("execution_complete")

    # Print trace summary
    print("\n  Trace Summary:")
    summary = tracer.get_trace_summary()
    print(f"    - Total spans: {summary['span_count']}")
    print(f"    - Total duration: {summary['total_duration_ms']}ms")

    print("\n  Span Tree:")
    for span in summary['spans']:
        indent = "  " * (1 if span['parent_id'] else 0)
        status_icon = "✓" if span['status'] == 'ok' else "✗"
        print(f"    {indent}{status_icon} {span['name']} ({span['duration_ms']}ms)")

    # === @trace decorator ===
    print("\n[2] @trace Decorator")
    print("-" * 40)

    @trace("custom_operation")
    async def do_something(x: int) -> int:
        await asyncio.sleep(0.01)
        return x * 2

    result = await do_something(21)
    print(f"  Result: {result}")
    print(f"  New span count: {tracer.get_trace_summary()['span_count']}")


async def demo_integrated_agent():
    """Demo integrated NotebookAgent with TieredMemory and Tracing."""
    from nimbus.core import NotebookAgent
    from nimbus.core.memory import MemoryConfig

    print("\n" + "=" * 60)
    print("  Integrated NotebookAgent Demo")
    print("=" * 60)

    llm = OllamaClient(model="gemma3n")

    # 使用 TieredMemory 创建 Agent
    config = MemoryConfig(compression_threshold=4)
    agent = NotebookAgent(
        llm_client=llm,
        system_prompt="你是一个智能笔记本助手，帮助用户分析文档。",
        memory_type="tiered",
        memory_config=config,
        enable_logging=True,
        session_id="integrated_demo",
    )

    print("\n[1] Agent Configuration")
    print("-" * 40)
    print(f"  Memory type: tiered")
    print(f"  Logging: enabled")
    print(f"  Session ID: {agent.session_id}")

    # 模拟文件上传
    print("\n[2] File Upload")
    print("-" * 40)
    agent.on_file_upload("sales_report.pdf", "PDF", "2024年销售数据，包含各地区业绩")
    print("  File pinned to memory")

    # 运行对话
    print("\n[3] Conversation with Tracing")
    print("-" * 40)

    queries = [
        "你好，我上传的是什么文件？",
        "帮我分析一下报告的要点",
    ]

    for query in queries:
        print(f"\n  User: {query}")
        response = await agent.run(query)
        print(f"  Agent: {response.text[:150]}...")

    # 显示 Memory 统计
    print("\n[4] Memory Stats")
    print("-" * 40)
    stats = agent.get_memory_stats()
    if stats:
        # stats 可能是 dict 或 MemoryStats 对象
        if hasattr(stats, 'turn_count'):
            print(f"  Turn count: {stats.turn_count}")
            print(f"  Pinned tokens: {stats.pinned_tokens}")
            print(f"  Episodic tokens: {stats.episodic_tokens}")
            print(f"  Compression count: {stats.compression_count}")
        else:
            print(f"  Turn count: {stats.get('turn_count', 'N/A')}")
            print(f"  Pinned tokens: {stats.get('pinned_tokens', 'N/A')}")
            print(f"  Episodic tokens: {stats.get('episodic_tokens', 'N/A')}")
            print(f"  Compression count: {stats.get('compression_count', 'N/A')}")

    # 显示 Trace 摘要
    print("\n[5] Trace Summary")
    print("-" * 40)
    trace = agent.get_trace_summary()
    if trace:
        print(f"  Total spans: {trace['span_count']}")
        print(f"  Total duration: {trace['total_duration_ms']}ms")


async def main():
    print("\n" + "=" * 60)
    print("  OpenNotebook Phase 1 Demo")
    print("  TieredMemory + Logging + Tracing")
    print("=" * 60)

    # Demo 1: TieredMemoryManager
    await demo_tiered_memory()

    # Demo 2: Tracing System
    await demo_tracing()

    # Demo 3: Integrated Agent
    await demo_integrated_agent()

    print("\n" + "=" * 60)
    print("  All demos completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
