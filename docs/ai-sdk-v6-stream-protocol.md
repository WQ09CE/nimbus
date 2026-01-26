# Vercel AI SDK v6 UI Message Stream Protocol

> 参考: https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol

## 概述

AI SDK v6 使用 **UI Message Stream Protocol** 进行前后端通信。这是一个基于 Server-Sent Events (SSE) 的 JSON 流协议。

## 必须的 HTTP Header

```http
Content-Type: text/event-stream
x-vercel-ai-ui-message-stream: v1
```

## SSE 格式

每个事件的格式：
```
data: {JSON对象}\n\n
```

最后以 `[DONE]` 标记结束：
```
data: [DONE]\n\n
```

## 事件类型

### 1. 流控制事件

| 事件类型 | 说明 | 必须 |
|---------|------|-----|
| `start` | 开始新消息 | ✅ |
| `finish` | 消息完成 | ✅ |

**start 事件**
```json
{"type": "start", "messageId": "msg_xxx"}
```

**finish 事件**
```json
{"type": "finish"}
```

### 2. 文本流事件

文本使用 `start → delta → end` 模式，每个文本块有唯一 ID。

| 事件类型 | 说明 |
|---------|------|
| `text-start` | 开始文本块 |
| `text-delta` | 文本增量 |
| `text-end` | 结束文本块 |

**示例**
```json
{"type": "text-start", "id": "text_abc123"}
{"type": "text-delta", "id": "text_abc123", "delta": "Hello"}
{"type": "text-delta", "id": "text_abc123", "delta": " world"}
{"type": "text-end", "id": "text_abc123"}
```

### 3. 工具调用事件

| 事件类型 | 说明 |
|---------|------|
| `tool-input-start` | 开始工具调用 |
| `tool-input-delta` | 工具参数增量 (可选) |
| `tool-input-available` | 工具参数完整可用 |
| `tool-output-available` | 工具执行结果 |

**tool-input-start**
```json
{
  "type": "tool-input-start",
  "toolCallId": "call_xxx",
  "toolName": "getWeather"
}
```

**tool-input-available**
```json
{
  "type": "tool-input-available",
  "toolCallId": "call_xxx",
  "toolName": "getWeather",
  "input": {"city": "San Francisco"}
}
```

**tool-output-available**
```json
{
  "type": "tool-output-available",
  "toolCallId": "call_xxx",
  "output": "72°F, sunny"
}
```

### 4. 推理/思考事件 (可选)

| 事件类型 | 说明 |
|---------|------|
| `reasoning-start` | 开始推理块 |
| `reasoning-delta` | 推理增量 |
| `reasoning-end` | 结束推理块 |

**示例**
```json
{"type": "reasoning-start", "id": "reasoning_xxx"}
{"type": "reasoning-delta", "id": "reasoning_xxx", "delta": "Let me think..."}
{"type": "reasoning-end", "id": "reasoning_xxx"}
```

### 5. 来源引用事件 (可选)

```json
{
  "type": "source",
  "id": "source_xxx",
  "source": {
    "type": "url",
    "url": "https://example.com",
    "title": "Example"
  }
}
```

### 6. 文件/数据事件 (可选)

```json
{
  "type": "file",
  "id": "file_xxx",
  "file": {
    "type": "base64",
    "mimeType": "image/png",
    "data": "..."
  }
}
```

## 完整示例

### 简单文本响应

```
data: {"type":"start","messageId":"msg_001"}

data: {"type":"text-start","id":"text_001"}

data: {"type":"text-delta","id":"text_001","delta":"Hello"}

data: {"type":"text-delta","id":"text_001","delta":" world!"}

data: {"type":"text-end","id":"text_001"}

data: {"type":"finish"}

data: [DONE]

```

### 带工具调用的响应

```
data: {"type":"start","messageId":"msg_002"}

data: {"type":"tool-input-start","toolCallId":"call_001","toolName":"search"}

data: {"type":"tool-input-available","toolCallId":"call_001","toolName":"search","input":{"query":"weather"}}

data: {"type":"tool-output-available","toolCallId":"call_001","output":"Sunny, 72°F"}

data: {"type":"text-start","id":"text_002"}

data: {"type":"text-delta","id":"text_002","delta":"The weather is sunny and 72°F."}

data: {"type":"text-end","id":"text_002"}

data: {"type":"finish"}

data: [DONE]

```

## 前端使用

```typescript
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";

const transport = new DefaultChatTransport({
  api: "http://localhost:8000/api/chat",
});

const { messages, sendMessage, status } = useChat({ transport });
```

## 后端实现 (Python/FastAPI)

```python
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json

def sse_event(data: dict | str) -> str:
    if isinstance(data, str):
        return f"data: {data}\n\n"
    return f"data: {json.dumps(data)}\n\n"

@router.post("/api/chat")
async def chat(request: ChatRequest):
    async def stream():
        message_id = "msg_" + generate_id()
        text_id = "text_" + generate_id()
        
        yield sse_event({"type": "start", "messageId": message_id})
        yield sse_event({"type": "text-start", "id": text_id})
        
        for char in "Hello world!":
            yield sse_event({"type": "text-delta", "id": text_id, "delta": char})
        
        yield sse_event({"type": "text-end", "id": text_id})
        yield sse_event({"type": "finish"})
        yield "data: [DONE]\n\n"
    
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"x-vercel-ai-ui-message-stream": "v1"}
    )
```

## 注意事项

1. **ID 唯一性**: 每个 `messageId`、`text_id`、`toolCallId` 必须唯一
2. **事件顺序**: `start` 必须是第一个事件，`finish` 和 `[DONE]` 必须是最后
3. **text 块配对**: `text-start` 和 `text-end` 必须成对出现
4. **Header 必须**: `x-vercel-ai-ui-message-stream: v1` 是必须的
5. **SSE 格式**: 每行 `data: {...}\n\n`，注意两个换行符

## 与旧版协议对比

| 旧版 (v5) | 新版 (v6) |
|-----------|-----------|
| `0:"text"` | `{"type":"text-delta","delta":"text"}` |
| `9:{toolCall}` | `{"type":"tool-input-available",...}` |
| `e:{finish}` | `{"type":"finish"}` |
| `text/plain` | `text/event-stream` |
| 无 header | `x-vercel-ai-ui-message-stream: v1` |

## 参考链接

- [AI SDK Stream Protocol](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)
- [AI SDK Streaming Data](https://ai-sdk.dev/docs/ai-sdk-ui/streaming-data)
- [AI SDK Migration Guide](https://ai-sdk.dev/docs/migration-guides/migration-guide-6-0)
