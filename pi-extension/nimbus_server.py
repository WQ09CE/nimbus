#!/usr/bin/env python3
"""
Nimbus Server for Pi Extension

作为 Pi Extension 的后端运行
- 接收用户消息
- 使用 Nimbus core (MMU/Context Stack) 处理
- 调用 LLM 并返回响应

通过 stdin/stdout JSON 通信
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nimbus.v2.adapters import PiLLMAdapter, PiLLMConfig
from nimbus.v2.core.memory.mmu import MMU, MMUConfig

# 静默模式：不输出日志到 stderr
QUIET = os.environ.get("NIMBUS_QUIET", "").lower() in ("1", "true", "yes")


def send_response(data: dict):
    """发送响应到 stdout"""
    print(json.dumps(data), flush=True)


def log(msg: str):
    """日志输出到 stderr（静默模式下不输出）"""
    if not QUIET:
        print(f"[nimbus-server] {msg}", file=sys.stderr, flush=True)


async def handle_message(llm: PiLLMAdapter, mmu: MMU, msg: dict):
    """处理来自 Pi Extension 的消息"""
    msg_type = msg.get("type")
    
    if msg_type == "user_message":
        content = msg.get("content", "")
        log(f"Received: {content[:50]}...")
        
        # 添加到 MMU
        mmu.add_user_message(content)
        
        # 组装上下文（过滤无价值的消息）
        context = mmu.assemble_context(filter_discardable=True)
        
        # 调用 LLM
        full_response = ""
        try:
            async for event in llm.stream(context):
                if event.type == "text":
                    send_response({"type": "text", "text": event.text})
                    full_response += event.text
                elif event.type == "tool_call":
                    send_response({
                        "type": "tool_call",
                        "toolCall": event.tool_call,
                    })
                elif event.type == "usage":
                    send_response({
                        "type": "usage",
                        "usage": event.usage,
                    })
                elif event.type == "error":
                    send_response({"type": "error", "error": event.error})
                    break
            
            # 添加助手响应到 MMU
            if full_response:
                mmu.add_assistant_message(full_response)
            
            send_response({"type": "done"})
            
        except Exception as e:
            log(f"Error: {e}")
            send_response({"type": "error", "error": str(e)})
    
    elif msg_type == "gc_status":
        total = len(mmu.assemble_context(filter_discardable=False))
        discardable = mmu.get_discardable_count()
        send_response({
            "type": "gc_status",
            "gcStatus": {"total": total, "discardable": discardable},
        })
    
    elif msg_type == "clear":
        mmu.clear()
        send_response({"type": "done"})
    
    elif msg_type == "shutdown":
        log("Shutting down...")
        send_response({"type": "done"})
        sys.exit(0)


async def main():
    log("Starting Nimbus server...")
    
    # 配置
    config = PiLLMConfig(
        provider="anthropic",
        model_id="claude-sonnet-4-20250514",
        max_tokens=8192,
    )
    
    # 初始化 MMU
    mmu = MMU(config=MMUConfig(
        auto_detect_failures=True,
        auto_extract_on_pop=True,
    ))
    
    # 启动 LLM adapter
    async with PiLLMAdapter(config) as llm:
        log("LLM adapter ready")
        
        # 发送就绪信号
        send_response({"type": "ready"})
        
        # 主循环：读取 stdin
        loop = asyncio.get_event_loop()
        
        while True:
            try:
                # 异步读取一行
                line = await loop.run_in_executor(None, sys.stdin.readline)
                
                if not line:
                    log("stdin closed")
                    break
                
                line = line.strip()
                if not line:
                    continue
                
                try:
                    msg = json.loads(line)
                    await handle_message(llm, mmu, msg)
                except json.JSONDecodeError:
                    log(f"Invalid JSON: {line}")
                    
            except Exception as e:
                log(f"Error in main loop: {e}")
                break
    
    log("Server stopped")


if __name__ == "__main__":
    asyncio.run(main())
