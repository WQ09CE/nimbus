#!/usr/bin/env python3
"""
Nimbus Filter Server

只负责追踪消息和判断哪些应该被过滤
不调用 LLM，让 Pi 负责那部分

通过 stdin/stdout JSON 通信
"""

import json
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nimbus.v2.core.memory.mmu import MMU, MMUConfig


def send(data: dict):
    print(json.dumps(data), flush=True)


def log(msg: str):
    print(f"[filter-server] {msg}", file=sys.stderr, flush=True)


def main():
    log("Starting filter server...")
    
    # 初始化 MMU
    mmu = MMU(config=MMUConfig(
        auto_detect_failures=True,
    ))
    
    # 追踪 tool call IDs
    tool_markers: dict[str, str] = {}  # tool_call_id -> "failed" | "ok"
    
    send({"type": "ready"})
    log("Ready")
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        
        try:
            msg = json.loads(line)
            msg_type = msg.get("type")
            
            if msg_type == "track_message":
                # 追踪消息
                role = msg.get("role")
                content = msg.get("content", "")
                tool_call_id = msg.get("toolCallId")
                is_error = msg.get("isError", False)
                
                if role == "tool" and tool_call_id:
                    # 检测失败模式
                    if is_error:
                        tool_markers[tool_call_id] = "failed"
                        log(f"Marked {tool_call_id[:8]} as failed (isError)")
                    elif content.startswith("[Error]"):
                        tool_markers[tool_call_id] = "failed"
                        log(f"Marked {tool_call_id[:8]} as failed (Error prefix)")
                    elif any(p in content.lower() for p in ["not found", "no such file", "permission denied"]):
                        tool_markers[tool_call_id] = "failed"
                        log(f"Marked {tool_call_id[:8]} as failed (pattern)")
                    else:
                        tool_markers[tool_call_id] = "ok"
            
            elif msg_type == "get_discardable":
                # 返回应该被过滤的 tool call IDs
                failed_ids = [k for k, v in tool_markers.items() if v == "failed"]
                send({"type": "discardable_ids", "ids": failed_ids})
                log(f"Discardable: {len(failed_ids)} ids")
            
            elif msg_type == "clear":
                tool_markers.clear()
                send({"type": "done"})
            
            elif msg_type == "shutdown":
                log("Shutting down")
                break
                
        except json.JSONDecodeError:
            log(f"Invalid JSON: {line}")
        except Exception as e:
            log(f"Error: {e}")
    
    log("Server stopped")


if __name__ == "__main__":
    main()
