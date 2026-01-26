"""
OpenNotebook Agent Framework - 完整功能测试 (Qwen3:8b)

使用本地 Ollama qwen3:8b 模型测试所有已实现的功能：
1. 基本对话 (Simple Planner)
2. DAG 并行执行 (DAG Planner + AsyncRuntime)
3. AgentFactory 配置加载
4. Tiered Memory 管理
5. Artifact 生成
6. 流式输出
7. Re-planning 机制
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Optional

# Setup path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from nimbus.core import (
    # Agent
    NotebookAgent,
    AgentFactory,
    create_agent,
    # Config
    AgentConfig,
    LLMConfig,
    SkillConfig,
    MemoryConfig,
    RuntimeConfig,
    # Types
    Artifact,
    ArtifactType,
    TaskDAG,
    TaskStatus,
    # Logging
    setup_logging,
    logger,
    get_agent_logger,
    agent_context,
)

# Setup logging (只调用一次)
_logging_initialized = False

def ensure_logging():
    """确保日志只初始化一次"""
    global _logging_initialized
    if not _logging_initialized:
        setup_logging(level="INFO", log_dir="./.logs", json_file=False)
        _logging_initialized = True

# aiohttp for Ollama
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    logger.warning("aiohttp not installed, run: pip install aiohttp")


class QwenOllamaClient:
    """Ollama client for Qwen3:8b with better JSON output."""

    def __init__(
        self,
        model: str = "qwen3:8b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.7,
    ):
        self.model = model
        self.base_url = base_url
        self.temperature = temperature

    async def complete(self, prompt: str) -> str:
        """Call Ollama API for completion."""
        if not HAS_AIOHTTP:
            raise RuntimeError("aiohttp required: pip install aiohttp")

        url = f"{self.base_url}/api/generate"

        # Qwen3 需要 /no_think 来禁用思考模式以获得更好的 JSON 输出
        payload = {
            "model": self.model,
            "prompt": prompt + "\n/no_think",  # 禁用思考模式
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": 1024,
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Ollama error: {resp.status} - {text}")
                data = await resp.json()
                response = data.get("response", "")
                # 清理可能的思考标签
                if "<think>" in response:
                    # 移除 <think>...</think> 部分
                    import re
                    response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)
                return response.strip()


def print_section(title: str):
    """打印分隔线"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


async def test_1_basic_chat():
    """测试 1: 基本对话 (Simple Planner)"""
    print_section("Test 1: 基本对话 (Simple Planner)")

    with agent_context("test-basic", task_id="chat"):
        llm = QwenOllamaClient()
        agent = NotebookAgent(
            llm_client=llm,
            system_prompt="你是一个友好的助手。用简短的中文回复。",
            planner_type="simple",  # 使用简单规划器
        )

        # 测试简单问候
        logger.info("发送: 你好!")
        response = await agent.run("你好!")
        logger.success(f"回复: {response.text}")

        # 测试上下文记忆
        logger.info("发送: 我叫小明")
        response = await agent.run("我叫小明")
        logger.success(f"回复: {response.text}")

        logger.info("发送: 我叫什么名字?")
        response = await agent.run("我叫什么名字?")
        logger.success(f"回复: {response.text}")

        # 显示 Memory 状态
        stats = agent.get_memory_stats()
        logger.info(f"Memory 状态: {stats}")

    print("✅ Test 1 完成")


async def test_2_dag_parallel():
    """测试 2: DAG 并行执行"""
    print_section("Test 2: DAG 并行执行 (DAG Planner)")

    with agent_context("test-dag", task_id="parallel"):
        llm = QwenOllamaClient()
        agent = NotebookAgent(
            llm_client=llm,
            system_prompt="你是一个任务规划助手。",
            planner_type="dag",  # 使用 DAG 规划器
            runtime_config=RuntimeConfig(
                default_timeout=30,
                max_retries=1,
                max_concurrent=5,
            ),
        )

        # 触发多任务场景
        logger.info("发送: 同时搜索 Python 和 Rust 的教程")
        response = await agent.run("同时搜索 Python 和 Rust 的教程")
        logger.success(f"回复: {response.text[:200]}...")

        # 检查是否有 artifacts
        if response.artifacts:
            logger.info(f"生成了 {len(response.artifacts)} 个 Artifacts")

        # 检查建议
        if response.suggestions:
            logger.info(f"建议: {response.suggestions}")

    print("✅ Test 2 完成")


async def test_3_factory_config():
    """测试 3: AgentFactory 配置加载"""
    print_section("Test 3: AgentFactory 配置加载")

    # 创建测试配置
    config_dict = {
        "name": "TestAgent",
        "version": "1.0.0",
        "llm": {
            "model": "qwen3:8b",
            "temperature": 0.5,
        },
        "memory": {
            "type": "tiered",
            "working_memory_budget": 2000,
            "episodic_budget": 4000,
            "pinned_budget": 500,
        },
        "runtime": {
            "default_timeout": 20,
            "max_retries": 1,
        },
        "skills": [
            {"name": "synthesize", "type": "builtin"},
            {"name": "search", "type": "builtin"},
        ],
        "system_prompt": "你是一个测试助手。",
    }

    # 从字典创建配置
    config = AgentConfig.from_dict(config_dict)
    logger.info(f"配置加载成功: {config.name} v{config.version}")
    logger.info(f"  - LLM: {config.llm.model}, temp={config.llm.temperature}")
    logger.info(f"  - Memory: {config.memory.type}, episodic={config.memory.episodic_budget}")
    logger.info(f"  - Skills: {[s.name for s in config.skills]}")

    # 测试配置序列化
    config_dict_out = config.to_dict()
    logger.info(f"配置导出成功 ({len(str(config_dict_out))} chars)")

    # 注册自定义 LLM factory
    AgentFactory.register_llm_factory("qwen3:8b", lambda cfg: QwenOllamaClient(
        model=cfg.model,
        temperature=cfg.temperature,
    ))

    # 从配置创建 Agent
    agent = AgentFactory.create_from_config(config)
    logger.success(f"Agent 创建成功, planner_type={agent._planner_type}")

    # 简单测试
    response = await agent.run("你好，测试一下")
    logger.success(f"回复: {response.text}")

    print("✅ Test 3 完成")


async def test_4_tiered_memory():
    """测试 4: Tiered Memory 管理"""
    print_section("Test 4: Tiered Memory 管理")

    with agent_context("test-memory", task_id="tiered"):
        llm = QwenOllamaClient()
        agent = NotebookAgent(
            llm_client=llm,
            memory_type="tiered",
            memory_config=MemoryConfig(
                working_budget=2000,
                episodic_budget=4000,
                pinned_budget=500,
                compression_threshold=3,  # 较小的阈值以便测试压缩
            ),
        )

        # 测试 Pinned Context
        logger.info("测试 Pinned Context...")
        agent.on_file_upload(
            filename="report.pdf",
            file_type="PDF",
            summary="2024年Q3销售报告，包含区域数据分析",
        )

        # 多轮对话测试
        conversations = [
            "你好",
            "我上传了什么文件?",
            "这个报告是关于什么的?",
            "帮我分析一下这个报告的重点",
        ]

        for msg in conversations:
            logger.info(f"User: {msg}")
            response = await agent.run(msg)
            logger.success(f"Agent: {response.text[:100]}...")

        # 显示 Memory 统计
        stats = agent.get_memory_stats()
        logger.info("Memory 统计:")
        for key, value in stats.items():
            logger.info(f"  - {key}: {value}")

        # 测试 checkpoint
        checkpoint_path = await agent.checkpoint()
        if checkpoint_path:
            logger.success(f"Checkpoint 保存: {checkpoint_path}")

    print("✅ Test 4 完成")


async def test_5_artifacts():
    """测试 5: Artifact 生成"""
    print_section("Test 5: Artifact 生成")

    # 手动创建 Artifact 测试
    artifact = Artifact(
        id="art_001",
        type=ArtifactType.CODE,
        title="示例代码",
        data="print('Hello, World!')",
        mime_type="text/x-python",
        metadata={"language": "python"},
    )

    logger.info(f"创建 Artifact: {artifact.title}")
    logger.info(f"  - ID: {artifact.id}")
    logger.info(f"  - Type: {artifact.type.value}")
    logger.info(f"  - Data: {artifact.data}")

    # 序列化测试
    artifact_dict = artifact.to_dict()
    logger.info(f"序列化: {json.dumps(artifact_dict, ensure_ascii=False)}")

    # 反序列化测试
    artifact2 = Artifact.from_dict(artifact_dict)
    logger.success(f"反序列化成功: {artifact2.title}")

    # 测试不同类型的 Artifact
    artifacts = [
        Artifact(id="art_file", type=ArtifactType.FILE, title="报告.docx", data=b"..."),
        Artifact(id="art_chart", type=ArtifactType.CHART, title="销售图表", data={"type": "bar"}),
        Artifact(id="art_table", type=ArtifactType.TABLE, title="数据表", data=[["A", "B"], [1, 2]]),
        Artifact(id="art_md", type=ArtifactType.MARKDOWN, title="文档", data="# Hello"),
    ]

    for art in artifacts:
        logger.info(f"  - {art.type.value}: {art.title}")

    print("✅ Test 5 完成")


async def test_6_streaming():
    """测试 6: 流式输出"""
    print_section("Test 6: 流式输出")

    with agent_context("test-stream", task_id="streaming"):
        llm = QwenOllamaClient()
        agent = NotebookAgent(
            llm_client=llm,
            planner_type="simple",
        )

        logger.info("发送: 搜索人工智能最新进展")
        print("-" * 40)

        event_counts = {}
        async for event in agent.run_stream("搜索人工智能最新进展"):
            event_type = event.get("type", "unknown")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1

            if event_type == "status":
                print(f"[STATUS] {event['content']}")
            elif event_type == "planning":
                print(f"[PLANNING] {event['content']}")
            elif event_type == "task_start":
                print(f"[START] Task {event.get('task_id')}: {event.get('skill')}")
            elif event_type == "task_done":
                result = str(event.get('result', ''))[:80]
                print(f"[DONE] {result}...")
            elif event_type == "direct":
                print(f"[DIRECT] {event['content'][:100]}...")
            elif event_type == "complete":
                print(f"[COMPLETE] {event['content'][:100]}...")
            elif event_type == "error":
                print(f"[ERROR] {event['content']}")
            elif event_type == "dag_start":
                print(f"[DAG_START] {event.get('goal')[:50]}... ({event.get('total_tasks')} tasks)")
            elif event_type == "dag_complete":
                print(f"[DAG_COMPLETE] completed={event.get('completed')}, failed={event.get('failed')}")

        print("-" * 40)
        logger.info(f"事件统计: {event_counts}")

    print("✅ Test 6 完成")


async def test_7_dag_streaming():
    """测试 7: DAG 模式流式输出"""
    print_section("Test 7: DAG 流式输出")

    with agent_context("test-dag-stream", task_id="dag-streaming"):
        llm = QwenOllamaClient()
        agent = NotebookAgent(
            llm_client=llm,
            planner_type="dag",
            runtime_config=RuntimeConfig(max_concurrent=3),
        )

        logger.info("发送: 搜索 Python 和 JavaScript 教程，然后总结")
        print("-" * 40)

        async for event in agent.run_stream("搜索 Python 和 JavaScript 教程，然后总结"):
            event_type = event.get("type", "unknown")

            if event_type == "dag_start":
                print(f"🚀 DAG 开始: {event.get('total_tasks')} 个任务")
            elif event_type == "task_start":
                print(f"  ▶ 开始: {event.get('skill')} ({event.get('task_id')})")
            elif event_type == "task_done":
                print(f"  ✓ 完成: {event.get('skill')} ({event.get('duration_ms', 0)}ms)")
            elif event_type == "task_failed":
                print(f"  ✗ 失败: {event.get('skill')} - {event.get('error')}")
            elif event_type == "dag_complete":
                print(f"🏁 DAG 完成: {event.get('completed')} 成功, {event.get('failed')} 失败")
            elif event_type == "complete":
                print(f"\n📝 最终回复: {event['content'][:150]}...")

        print("-" * 40)

    print("✅ Test 7 完成")


async def test_8_skills_direct():
    """测试 8: 直接调用 Skills"""
    print_section("Test 8: 直接调用 Skills")

    from nimbus.skills import web_search, summarize_text, extract_keywords

    # 测试搜索
    logger.info("测试 web_search...")
    result = await web_search("Python 异步编程")
    logger.success(f"搜索结果: {result[:150]}...")

    # 测试摘要
    sample_text = """
    Python 是一种高级编程语言，以其简洁和易读性著称。
    它支持多种编程范式，包括面向对象、函数式和过程式编程。
    Python 广泛应用于 Web 开发、数据科学、人工智能和自动化等领域。
    其丰富的标准库和第三方生态系统使其成为最受欢迎的编程语言之一。
    """

    logger.info("测试 extract_keywords...")
    keywords = await extract_keywords(sample_text, top_k=5)
    logger.success(f"关键词: {keywords}")

    logger.info("测试 summarize_text...")
    summary = await summarize_text(sample_text, max_length=50)
    logger.success(f"摘要: {summary}")

    print("✅ Test 8 完成")


async def test_9_error_handling():
    """测试 9: 错误处理"""
    print_section("Test 9: 错误处理")

    with agent_context("test-error", task_id="error-handling"):
        # 测试超时配置
        llm = QwenOllamaClient()
        agent = NotebookAgent(
            llm_client=llm,
            planner_type="dag",
            runtime_config=RuntimeConfig(
                default_timeout=1,  # 非常短的超时
                max_retries=0,
            ),
        )

        logger.info("测试错误恢复...")
        response = await agent.run("你好")

        if response.error:
            logger.warning(f"捕获错误: {response.error}")
        else:
            logger.success(f"正常回复: {response.text[:100]}...")

    print("✅ Test 9 完成")


async def run_all_tests():
    """运行所有测试"""
    # 初始化日志（只执行一次）
    ensure_logging()

    print("\n" + "=" * 60)
    print("  OpenNotebook Agent Framework - 完整功能测试")
    print("  Model: qwen3:8b (Ollama)")
    print("=" * 60)

    tests = [
        ("基本对话", test_1_basic_chat),
        ("DAG 并行执行", test_2_dag_parallel),
        ("AgentFactory 配置", test_3_factory_config),
        ("Tiered Memory", test_4_tiered_memory),
        ("Artifact 生成", test_5_artifacts),
        ("流式输出", test_6_streaming),
        ("DAG 流式输出", test_7_dag_streaming),
        ("Skills 直接调用", test_8_skills_direct),
        ("错误处理", test_9_error_handling),
    ]

    results = []
    for name, test_func in tests:
        try:
            await test_func()
            results.append((name, "✅ PASS"))
        except Exception as e:
            logger.error(f"测试失败: {name} - {e}")
            results.append((name, f"❌ FAIL: {e}"))

    # 打印总结
    print("\n" + "=" * 60)
    print("  测试结果汇总")
    print("=" * 60)
    for name, status in results:
        print(f"  {status} - {name}")

    passed = sum(1 for _, s in results if "PASS" in s)
    print(f"\n  总计: {passed}/{len(tests)} 通过")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
