#!/usr/bin/env python3
"""
MMU 图片 Token 优化压测脚本

用法:
    python scripts/test_image_stress.py [--server URL] [--session SESSION_ID]

测试场景:
    1. 连续发送多张不同图片（测试 budget 控制）
    2. 重复发送同一张图片（测试去重）
    3. 混合文字+图片对话（测试 placeholder 替换）
    4. 检查 context dump 中图片处理情况
"""

import argparse
import base64
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx

DEFAULT_SERVER = "http://localhost:4000"


def generate_fake_image(width: int = 100, height: int = 100, seed: int = 0) -> str:
    """生成一个假的 PNG 图片（随机像素数据）的 base64 编码。"""
    # Minimal valid PNG: 8-byte signature + IHDR + IDAT + IEND
    # For testing we just need unique base64 data, not a real renderable image
    random.seed(seed)
    # Create enough random data to be distinguishable (> 64 bytes to test hash vs prefix)
    data = bytes([random.randint(0, 255) for _ in range(512)])
    # Add PNG magic bytes so it looks like a real PNG
    png_header = b'\x89PNG\r\n\x1a\n'
    fake_png = png_header + data
    return base64.b64encode(fake_png).decode("ascii")


def create_session(client: httpx.Client, server: str) -> str:
    """创建新 session。"""
    resp = client.post(f"{server}/api/sessions", json={"title": "Image Stress Test"})
    resp.raise_for_status()
    data = resp.json()
    session_id = data.get("id") or data.get("session_id")
    print(f"✅ Created session: {session_id}")
    return session_id


def send_chat(client: httpx.Client, server: str, session_id: str, 
              content: str, attachments: list = None, timeout: float = 60) -> str:
    """发送聊天消息并等待完成（非流式，直接读取 SSE 直到结束）。"""
    payload = {"content": content}
    if attachments:
        payload["attachments"] = attachments
    
    print(f"\n📤 Sending: {content[:60]}{'...' if len(content) > 60 else ''}")
    if attachments:
        print(f"   📎 {len(attachments)} attachment(s)")
    
    # Use streaming to read SSE events
    assistant_text = ""
    try:
        with client.stream(
            "POST", 
            f"{server}/api/sessions/{session_id}/chat",
            json=payload,
            timeout=timeout
        ) as resp:
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                        event_type = data.get("type", "")
                        
                        if event_type == "message" and "content" in data:
                            chunk = data["content"]
                            assistant_text += chunk
                        elif event_type == "dag_complete":
                            break
                        elif event_type == "error":
                            print(f"   ❌ Error: {data.get('message', data)}")
                            break
                    except json.JSONDecodeError:
                        pass
                elif line.startswith("event:"):
                    event_name = line[6:].strip()
                    if event_name == "dag_complete":
                        # Next data line will have the payload, but we can break
                        pass
    except httpx.ReadTimeout:
        print(f"   ⏰ Timeout after {timeout}s")
    
    preview = assistant_text[:100].replace('\n', ' ')
    print(f"📥 Response: {preview}{'...' if len(assistant_text) > 100 else ''}")
    return assistant_text


def check_context_dump(latest_n: int = 1):
    """检查最新的 context dump 文件，分析图片处理情况。"""
    log_dir = Path(".logs/context")
    if not log_dir.exists():
        print("⚠️  No context dump directory found")
        return
    
    files = sorted(log_dir.glob("context_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        print("⚠️  No context dump files found")
        return
    
    for f in files[:latest_n]:
        print(f"\n📋 Analyzing: {f.name}")
        with open(f) as fh:
            ctx = json.load(fh)
        
        messages = ctx if isinstance(ctx, list) else ctx.get("messages", ctx)
        
        total_images = 0
        kept_images = 0
        placeholder_images = 0
        total_chars = 0
        
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "image":
                            total_images += 1
                            kept_images += 1
                            data = block.get("data", "")
                            total_chars += len(data)
                        elif block.get("type") == "text":
                            text = block.get("text", "")
                            total_chars += len(text)
                            if "📷" in text and "Omitted" in text:
                                total_images += 1
                                placeholder_images += 1
        
        print(f"   Messages: {len(messages)}")
        print(f"   Total images referenced: {total_images}")
        print(f"   ├─ Kept (base64): {kept_images}")
        print(f"   └─ Placeholders: {placeholder_images}")
        print(f"   Total content size: {total_chars:,} chars")
        
        if total_images > 0:
            saved_pct = (placeholder_images / total_images * 100) if total_images > 0 else 0
            print(f"   💰 Token savings: {placeholder_images}/{total_images} images downgraded ({saved_pct:.0f}%)")


def test_scenario_1_multiple_unique_images(client, server, session_id):
    """场景 1: 连续发送 5 张不同图片（测试 budget 控制）"""
    print("\n" + "=" * 60)
    print("🧪 场景 1: 连续发送 5 张不同图片（测试 budget 控制）")
    print("   max_image_tokens=10000, 每张约 1500 token → 最多保留 ~6 张")
    print("=" * 60)
    
    for i in range(5):
        img_data = generate_fake_image(seed=i * 100 + 42)
        send_chat(client, server, session_id,
                  f"这是第 {i+1} 张图片，请简短描述你看到了什么",
                  attachments=[{
                      "type": "image",
                      "content": img_data,
                      "mime_type": "image/png",
                      "name": f"test_image_{i+1}.png"
                  }])
        time.sleep(1)


def test_scenario_2_duplicate_images(client, server, session_id):
    """场景 2: 重复发送同一张图片 3 次（测试去重）"""
    print("\n" + "=" * 60)
    print("🧪 场景 2: 重复发送同一张图片 3 次（测试去重）")
    print("=" * 60)
    
    same_image = generate_fake_image(seed=999)
    
    for i in range(3):
        send_chat(client, server, session_id,
                  f"再看看这张图（第 {i+1} 次发送同一张）",
                  attachments=[{
                      "type": "image",
                      "content": same_image,
                      "mime_type": "image/png",
                      "name": "same_image.png"
                  }])
        time.sleep(1)


def test_scenario_3_mixed_conversation(client, server, session_id):
    """场景 3: 混合文字+图片对话（测试自然对话中的图片处理）"""
    print("\n" + "=" * 60)
    print("🧪 场景 3: 混合文字+图片多轮对话")
    print("=" * 60)
    
    # 轮 1: 纯文字
    send_chat(client, server, session_id, "你好，我接下来要发一些图片给你看")
    time.sleep(1)
    
    # 轮 2: 图片 + 文字
    img1 = generate_fake_image(seed=200)
    send_chat(client, server, session_id,
              "这是一张截图，帮我看看",
              attachments=[{"type": "image", "content": img1, "mime_type": "image/png", "name": "screenshot.png"}])
    time.sleep(1)
    
    # 轮 3: 纯文字（引用之前的图片）
    send_chat(client, server, session_id, "刚才那张图片里的按钮在哪里？")
    time.sleep(1)
    
    # 轮 4: 另一张图片
    img2 = generate_fake_image(seed=300)
    send_chat(client, server, session_id,
              "再看看这张新的截图",
              attachments=[{"type": "image", "content": img2, "mime_type": "image/jpeg", "name": "new_screenshot.jpg"}])
    time.sleep(1)
    
    # 轮 5: 纯文字总结
    send_chat(client, server, session_id, "总结一下你看到的所有图片内容")


def main():
    parser = argparse.ArgumentParser(description="MMU Image Token Optimization Stress Test")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Nimbus server URL")
    parser.add_argument("--session", default=None, help="Existing session ID (creates new if not provided)")
    parser.add_argument("--scenario", type=int, choices=[1, 2, 3], default=None,
                        help="Run specific scenario (default: all)")
    parser.add_argument("--check-only", action="store_true", help="Only check latest context dump")
    args = parser.parse_args()
    
    if args.check_only:
        check_context_dump(latest_n=3)
        return
    
    client = httpx.Client(timeout=120)
    
    try:
        # Create or reuse session
        session_id = args.session
        if not session_id:
            session_id = create_session(client, args.server)
        
        print(f"\n🎯 Server: {args.server}")
        print(f"🎯 Session: {session_id}")
        
        # Run scenarios
        if args.scenario is None or args.scenario == 1:
            test_scenario_1_multiple_unique_images(client, args.server, session_id)
        
        if args.scenario is None or args.scenario == 2:
            test_scenario_2_duplicate_images(client, args.server, session_id)
        
        if args.scenario is None or args.scenario == 3:
            test_scenario_3_mixed_conversation(client, args.server, session_id)
        
        # Check results
        print("\n" + "=" * 60)
        print("📊 Context Dump 分析")
        print("=" * 60)
        time.sleep(2)  # Wait for last context dump
        check_context_dump(latest_n=3)
        
        print("\n✅ 压测完成！")
        print(f"   可以手动检查 .logs/context/ 下的 JSON 文件查看详细 context")
        
    finally:
        client.close()


if __name__ == "__main__":
    main()
