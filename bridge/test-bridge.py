#!/usr/bin/env python3
"""
测试 Pi Bridge

Usage:
    cd nimbus/bridge
    python test-bridge.py
"""

import subprocess
import json
import sys
import os

def send_request(proc, method, params=None):
    """发送 JSON-RPC 请求"""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    }
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
    
    # 读取响应
    line = proc.stdout.readline()
    return json.loads(line)

def read_notifications(proc, until_stop=True):
    """读取 streaming 通知"""
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        
        data = json.loads(line)
        
        # 如果是响应（有 id），返回
        if "id" in data:
            return data
        
        # 否则是通知
        method = data.get("method")
        params = data.get("params", {})
        
        if method == "ai.streamEvent":
            event_type = params.get("type")
            
            if event_type == "text":
                print(params.get("text", ""), end="", flush=True)
            elif event_type == "thinking":
                print(f"[thinking] {params.get('text', '')}", end="", flush=True)
            elif event_type == "tool_call":
                tc = params.get("toolCall", {})
                print(f"\n[tool_call] {tc.get('name')}({tc.get('arguments')})")
            elif event_type == "usage":
                usage = params.get("usage", {})
                print(f"\n[usage] input: {usage.get('inputTokens')}, output: {usage.get('outputTokens')}")
            elif event_type == "stop":
                print(f"\n[stop] reason: {params.get('reason')}")
                if until_stop:
                    return data
            elif event_type == "error":
                print(f"\n[error] {params.get('error')}")

def main():
    print("=" * 60)
    print("Pi Bridge Test")
    print("=" * 60)
    
    # 启动 bridge
    print("\n1. Starting pi-bridge...")
    proc = subprocess.Popen(
        ["npx", "tsx", "pi-bridge.ts"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    
    # 等待启动消息
    stderr_line = proc.stderr.readline()
    print(f"   {stderr_line.strip()}")
    
    # 测试 ping
    print("\n2. Testing ping...")
    response = send_request(proc, "ping")
    print(f"   Response: {response}")
    
    # 测试 getModels
    print("\n3. Getting models...")
    response = send_request(proc, "ai.getModels", {"provider": "anthropic"})
    models = response.get("result", [])
    print(f"   Found {len(models)} Anthropic models")
    for m in models[:3]:
        print(f"   - {m['id']}")
    
    # 检查 auth 状态
    print("\n4. Checking auth status...")
    response = send_request(proc, "auth.status")
    auth = response.get("result", {})
    print(f"   Auth file: {auth.get('authPath')}")
    print(f"   Exists: {auth.get('exists')}")
    providers = auth.get("providers", [])
    if providers:
        print("   Available credentials:")
        for p in providers:
            status = "✓" if p["valid"] else "✗ (expired)"
            print(f"     - {p['provider']}: {p['type']} {status}")
    else:
        print("   No credentials found")
    
    # 检查是否有可用的 anthropic 凭据
    has_anthropic = any(p["provider"] == "anthropic" and p["valid"] for p in providers)
    if not has_anthropic:
        print("\n5. Skipping stream test (no valid Anthropic credentials)")
        print("   To authenticate, run: pi")
        print("   Then use: /login")
        proc.terminate()
        return
    
    # 测试 stream
    print("\n6. Testing stream...")
    print("-" * 40)
    
    # 设置模型
    response = send_request(proc, "ai.setModel", {
        "provider": "anthropic",
        "modelId": "claude-sonnet-4-20250514"
    })
    print(f"   Model set: {response.get('result', {}).get('success')}")
    
    # 发送 stream 请求
    print("\n   Prompt: 'Say hello in Chinese, just one sentence.'")
    print("   Response: ", end="")
    
    send_request(proc, "ai.stream", {
        "messages": [
            {"role": "user", "content": "Say hello in Chinese, just one sentence."}
        ],
        "options": {"maxTokens": 100}
    })
    
    # 读取 streaming 响应
    read_notifications(proc)
    
    print("-" * 40)
    
    # 关闭
    print("\n7. Shutting down...")
    send_request(proc, "shutdown")
    proc.wait()
    print("   Done!")

if __name__ == "__main__":
    main()
