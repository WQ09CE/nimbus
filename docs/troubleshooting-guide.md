# Nimbus Troubleshooting Guide
**Version**: 1.0  
**Last Updated**: 2026-01-30

---

## 📋 Overview

This guide provides systematic troubleshooting methods for Nimbus Web UI and backend issues. Based on real-world debugging experience, it emphasizes **layered isolation testing** to quickly identify root causes.

---

## 🎯 Core Principles

### 1. Never Start with End-to-End Testing
**❌ Common Mistake:**
```
Change code → Restart → Test in browser → Fails → Change again → Restart → Still fails → ...
```

**✅ Correct Approach:**
```
1. Test LLM layer (direct API call)
2. Test vCPU layer (Python script)
3. Test AgentOS layer (Python script)
4. Test API layer (curl)
5. Test Web UI (browser)
```

**Key Point**: Identify the failing layer BEFORE making changes.

### 2. Always Isolate the Layer
Don't assume the problem is in the UI just because you see it there. Test each layer independently.

### 3. Log Everything
- Use timestamped log files: `/tmp/nimbus-{timestamp}.log`
- Use DEBUG level during development
- Never overwrite logs - keep them for comparison

### 4. Small Steps, Fast Feedback
- Change ONE thing at a time
- Test immediately after each change
- Git commit after each successful fix

### 5. Automate Repetitive Tasks
Don't manually type the same commands - use scripts.

---

## 🛠️ Debugging Toolkit

### Quick Diagnostic Script
Save as `~/debug-nimbus.sh`:

```bash
#!/bin/bash
# Quick diagnostic for Nimbus

echo "=== Nimbus Quick Diagnostic ==="
echo ""

# 1. Check service status
echo "📊 Service Status:"
lsof -nP -iTCP:3000,4096 -sTCP:LISTEN 2>/dev/null | grep -E "COMMAND|node|python" || echo "  ⚠️  No services listening"
echo ""

# 2. API health check
echo "🏥 API Health:"
HEALTH=$(curl -s http://localhost:4096/api/v1/health 2>/dev/null)
if [ "$HEALTH" ]; then
    echo "  ✅ $HEALTH"
else
    echo "  ❌ API not responding"
fi
echo ""

# 3. Test session creation
echo "🧪 Session Creation Test:"
SID=$(curl -s -X POST http://localhost:4096/api/v1/sessions \
    -H "Content-Type: application/json" \
    -d '{"name":"diagnostic"}' 2>/dev/null | jq -r .id 2>/dev/null)
if [ "$SID" != "null" ] && [ -n "$SID" ]; then
    echo "  ✅ Session created: $SID"
else
    echo "  ❌ Failed to create session"
fi
echo ""

# 4. Test message sending
if [ -n "$SID" ] && [ "$SID" != "null" ]; then
    echo "💬 Message Test:"
    curl -N -s -m 10 -X POST "http://localhost:4096/api/v1/sessions/$SID/chat" \
        -H "Content-Type: application/json" \
        -d '{"content":"say ok"}' 2>&1 | head -20
    echo ""
fi

# 5. Check database
echo "💾 Database Check:"
if [ -f .nimbus/nimbus.db ]; then
    MSG_COUNT=$(sqlite3 .nimbus/nimbus.db "SELECT COUNT(*) FROM messages;" 2>/dev/null)
    echo "  Total messages: $MSG_COUNT"
else
    echo "  ⚠️  Database not found"
fi
echo ""

# 6. Recent errors
echo "⚠️  Recent Errors:"
tail -100 /tmp/nimbus-*.log 2>/dev/null | grep -i "ERROR\|Exception" | tail -5
echo ""

echo "✅ Diagnostic complete"
```

### Automated Restart Script
Save as `~/restart-nimbus.sh`:

```bash
#!/bin/bash
# Restart Nimbus with optional debug mode

set -e

LOG_LEVEL=${1:-info}  # Default to info, pass 'debug' for debug mode

echo "🔄 Restarting Nimbus (log level: $LOG_LEVEL)..."

# Stop all processes
pkill -f "nimbus serve" 2>/dev/null || true
pkill -f "next dev" 2>/dev/null || true
sleep 2

# Start Nimbus server
cd ~/sourcecode/agent/agent-framework/nimbus
LOG_FILE="/tmp/nimbus-$(date +%Y%m%d-%H%M%S).log"
nimbus serve --host 0.0.0.0 --port 4096 --log-level $LOG_LEVEL > "$LOG_FILE" 2>&1 &
NIMBUS_PID=$!

# Start Web UI
cd web-ui
npm run dev > /tmp/webui-$(date +%Y%m%d-%H%M%S).log 2>&1 &
WEBUI_PID=$!

# Wait for startup
sleep 5

# Verify
echo ""
echo "📝 Nimbus log: $LOG_FILE"
echo "   PID: $NIMBUS_PID"
echo ""
curl -s http://localhost:4096/api/v1/health && echo "✅ API OK" || echo "❌ API Failed"
curl -s http://localhost:3000 > /dev/null && echo "✅ Web UI OK" || echo "❌ Web UI Failed"
echo ""
echo "🔗 Web UI: http://localhost:3000"
echo "🔗 API Docs: http://localhost:4096/docs"
```

Usage:
```bash
~/restart-nimbus.sh          # Normal mode (info level)
~/restart-nimbus.sh debug    # Debug mode (verbose logging)
```

### Layer Testing Script
Save as `~/test-nimbus-layers.sh`:

```bash
#!/bin/bash
# Test each layer independently

echo "=== Nimbus Layer Testing ==="
echo ""

# Layer 1: LLM Direct
echo "🧠 Layer 1: LLM API (Gemini)"
GEMINI_KEY=$(jq -r '.providers.gemini.api_key' ~/.nimbus/config.json)
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=$GEMINI_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"contents":[{"parts":[{"text":"say ok"}]}]}' | jq -r '.candidates[0].content.parts[0].text' 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✅ LLM layer OK"
else
    echo "❌ LLM layer FAILED"
fi
echo ""

# Layer 2: Python LLM Client
echo "🐍 Layer 2: Python LLM Client"
python3 << 'EOF'
import asyncio
import sys
sys.path.insert(0, '/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src')
from nimbus.llm import create_llm_client
async def test():
    client = create_llm_client()
    # Add actual test here
    print("✅ Python LLM client OK")
asyncio.run(test())
EOF
echo ""

# Layer 3: AgentOS
echo "🤖 Layer 3: AgentOS"
python3 << 'EOF'
import asyncio
import sys
sys.path.insert(0, '/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src')
from nimbus.v2 import create_agent_os
from nimbus.server.llm_adapter import V1ToV2LLMAdapter
from nimbus.llm import create_llm_client

async def test():
    v1_client = create_llm_client()
    llm = V1ToV2LLMAdapter(v1_client)
    agent = create_agent_os(llm_client=llm, tools={})
    result = await agent.chat("say ok")
    if result.status == "OK":
        print(f"✅ AgentOS OK: {result.output}")
    else:
        print(f"❌ AgentOS FAILED: {result.fault}")
asyncio.run(test())
EOF
echo ""

# Layer 4: API
echo "🌐 Layer 4: API Endpoint"
SID=$(curl -s -X POST http://localhost:4096/api/v1/sessions -d '{}' | jq -r .id)
curl -N -s -m 10 -X POST "http://localhost:4096/api/v1/sessions/$SID/chat" \
    -d '{"content":"say ok"}' | grep -q "event: dag_complete" && echo "✅ API layer OK" || echo "❌ API layer FAILED"
echo ""

echo "✅ Layer testing complete"
```

---

## 🔍 Diagnostic Checklist

When encountering issues, go through this checklist systematically:

### Frontend Issues
- [ ] Open browser DevTools (F12) → Console tab
  - Any JavaScript errors?
  - Any network errors?
- [ ] Check Network tab
  - What's the status code for API calls?
  - Are there CORS errors? (look for `Access-Control-Allow-Origin`)
- [ ] Verify `.env.local` configuration
  - Is `NEXT_PUBLIC_API_URL` correct?
  - Can you access that URL from your location?

### API Layer Issues
- [ ] Can you `curl` the health endpoint?
  ```bash
  curl http://localhost:4096/api/v1/health
  ```
- [ ] Can you create a session?
  ```bash
  curl -X POST http://localhost:4096/api/v1/sessions -d '{}'
  ```
- [ ] Does SSE connection establish?
  ```bash
  curl -N -X POST "http://localhost:4096/api/v1/sessions/$SID/chat" -d '{"content":"hi"}'
  # Look for: event: connected
  ```

### Backend Execution Issues
- [ ] Check logs for ERROR/Exception
  ```bash
  grep -i "error\|exception" /tmp/nimbus-*.log | tail -20
  ```
- [ ] Verify LLM configuration
  ```bash
  cat ~/.nimbus/config.json
  ```
- [ ] Test LLM API directly (skip all Nimbus layers)
  ```bash
  # For Gemini:
  curl "https://generativelanguage.googleapis.com/.../generateContent?key=$KEY" \
    -d '{"contents":[{"parts":[{"text":"hi"}]}]}'
  ```

### Code Changes Not Working
- [ ] Did you reinstall?
  ```bash
  pip install -e .
  ```
- [ ] Did you restart the service?
  ```bash
  pkill -f "nimbus serve" && nimbus serve ...
  ```
- [ ] Is code loading from the right location?
  ```bash
  python3 -c "import nimbus.v2.agentos; import inspect; print(inspect.getfile(nimbus.v2.agentos))"
  ```

---

## 📚 Case Study: Web UI Stuck in "Thinking"

### Problem
- Web UI shows "thinking..." indefinitely
- SSE stream shows `dag_complete` with `ERROR` status
- No assistant response in database

### Investigation Process

#### 1. Layer 5 (Frontend) ✅
```bash
# Browser console: No errors
# Network tab: API calls return 200 OK
# Conclusion: Frontend is fine, problem is backend
```

#### 2. Layer 4 (API) ⚠️
```bash
curl -N -X POST "http://localhost:4096/api/v1/sessions/$SID/chat" -d '{"content":"hi"}'
# Output:
# event: connected ✅
# event: message_start ✅
# event: task_start ✅
# event: dag_complete
# data: {"status": "ERROR"} ❌

# Conclusion: API layer works, but execution fails
```

#### 3. Layer 3 (AgentOS) ❌
```python
# Direct test in Python
from nimbus.v2 import create_agent_os
from nimbus.server.llm_adapter import V1ToV2LLMAdapter
from nimbus.llm import create_llm_client

v1_client = create_llm_client()
llm = V1ToV2LLMAdapter(v1_client)
agent = create_agent_os(llm_client=llm, tools={})
result = await agent.chat("hi")
print(result.status)  # Output: ERROR ❌

# Conclusion: Problem is in AgentOS/vCPU layer
```

#### 4. Layer 2 (LLM Adapter) ❌
Added detailed logging:
```python
# In llm_adapter.py
async def chat(self, messages, tools=None):
    import json
    print(f"DEBUG: Calling complete_with_tools")
    print(f"DEBUG: messages type = {type(messages)}")
    print(f"DEBUG: messages = {json.dumps(messages, indent=2)}")
    
    response = await self._client.complete_with_tools(messages, tools=tools)
    # ERROR: Gemini API returned error about invalid JSON
```

Found the issue by monkey-patching the Gemini client:
```python
# What Gemini API received:
{
  "contents": [
    {
      "parts": [
        {
          "text": [{"role": "user", "content": "hi"}]  # ❌ Array instead of string!
        }
      ]
    }
  ]
}
```

**Root Cause**: Positional vs keyword argument issue:
```python
# ❌ Bug:
response = await self._client.complete_with_tools(messages, tools=tools)
# messages was passed as first positional arg (prompt), not as messages kwarg!

# ✅ Fix:
response = await self._client.complete_with_tools(messages=messages, tools=tools)
```

### Fix Applied
1. Changed `messages` to keyword argument
2. Added tool_calls format conversion
3. Added detailed error logging

### Verification
```bash
# After fix:
curl -N -X POST "http://localhost:4096/api/v1/sessions/$SID/chat" -d '{"content":"hi"}'
# Output:
# event: message
# data: {"content": "Hi there! How can I help you today?"}
# event: dag_complete
# data: {"status": "OK"} ✅
```

---

## 💡 Best Practices

### 1. Git Workflow
```bash
# Before making changes
git add -A && git commit -m "WIP: before fixing X"

# After fixing
git add -A && git commit -m "fix: detailed description"

# If it doesn't work
git reset --hard HEAD^  # Instant rollback
```

### 2. Logging Strategy
```python
# Always log at entry/exit points
logger.info(f"🚀 Starting function_name with {param}")
try:
    result = await do_something()
    logger.info(f"✅ function_name completed: {result.status}")
    return result
except Exception as e:
    logger.error(f"❌ function_name failed: {e}")
    logger.error(f"Traceback:\n{traceback.format_exc()}")
    raise
```

### 3. Testing Pattern
```python
# Always test in isolation first
async def test_isolated():
    # Direct import, no dependencies
    from nimbus.component import Thing
    thing = Thing()
    result = await thing.do_stuff()
    assert result == expected
    print("✅ Isolated test passed")

asyncio.run(test_isolated())
```

### 4. Error Handling
```python
# Never swallow exceptions silently
try:
    result = risky_operation()
except Exception as e:
    # ❌ Bad:
    return ToolResult(status="ERROR")
    
    # ✅ Good:
    logger.error(f"Error in risky_operation: {e}", exc_info=True)
    return ToolResult(
        status="ERROR",
        fault=Fault(message=str(e), context={"traceback": traceback.format_exc()})
    )
```

---

## 🚨 Common Pitfalls

### 1. Assuming Frontend is the Problem
Just because you see the error in the browser doesn't mean it's a frontend bug. Always test the API layer first with `curl`.

### 2. Not Reading Logs
Logs tell you EXACTLY what's happening. If you don't see an error in the logs, add more logging.

### 3. Testing Too Many Layers at Once
Testing the whole system end-to-end makes it hard to identify which layer failed. Test one layer at a time.

### 4. Forgetting to Reinstall After Code Changes
`pip install -e .` only sets up the import path. If you change code, Python may still use the old cached version. Always restart the process.

### 5. Not Using Version Control
Without git, you can't easily rollback a bad change. Commit often, even for WIP changes.

---

## 📖 Further Reading

- [Nimbus Architecture](./architecture.md)
- [API Reference](./api-reference.md)
- [Agent OS Architecture](./agent-os-architecture.md)
- [Getting Started](./getting-started.md)

---

## 🤝 Contributing

Found a bug? Developed a new debugging technique? Please update this guide and submit a PR!

**Last Updated**: 2026-01-30 by Wukong 🐒
