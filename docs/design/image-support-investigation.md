# Pi-AI Bridge 图片/多模态支持调研报告

> 调研 Nimbus 全链路（web-ui → Nimbus 后端 → pi-ai bridge → pi-ai 包 → LLM Provider）对图片输入的支持情况。

## 结论

**✅ pi-ai 包原生支持图片输入，且全链路数据通道基本已打通，只需在后端 chat API 层做"拼接"即可。**

无需改动 pi-ai bridge 代码，无需改动 pi_ai_http.py，只需在 Nimbus 后端收到 attachments 后正确构造消息格式。

---

## 1. Pi-AI 包层面（✅ 完整支持）

### 1.1 类型定义

`@mariozechner/pi-ai` 的 `types.d.ts` 中定义了完整的图片类型：

```typescript
// 图片内容块
interface ImageContent {
    type: "image";
    data: string;       // base64 编码
    mimeType: string;   // "image/png", "image/jpeg", etc.
}

// 用户消息支持图文混合
interface UserMessage {
    role: "user";
    content: string | (TextContent | ImageContent)[];  // ← 支持图片数组
    timestamp: number;
}

// 工具结果也支持返回图片
interface ToolResultMessage {
    role: "toolResult";
    content: (TextContent | ImageContent)[];  // ← 支持图片
    // ...
}

// Model 定义中标识了是否支持图片输入
interface Model {
    input: ("text" | "image")[];  // ← 检查是否包含 "image"
    // ...
}
```

### 1.2 官方使用示例

```typescript
import { readFileSync } from 'fs';
import { getModel, complete } from '@mariozechner/pi-ai';

const model = getModel('openai', 'gpt-4o-mini');

// 检查模型是否支持图片
if (model.input.includes('image')) {
  console.log('Model supports vision');
}

const imageBuffer = readFileSync('image.png');
const base64Image = imageBuffer.toString('base64');

const response = await complete(model, {
  messages: [{
    role: 'user',
    content: [
      { type: 'text', text: 'What is in this image?' },
      { type: 'image', data: base64Image, mimeType: 'image/png' }
    ]
  }]
});
```

### 1.3 各 Provider 的图片处理

pi-ai 在各 provider 的实现中自动将统一的 `ImageContent` 格式转换为各家 API 要求的格式：

| Provider | 转换逻辑 | 源文件 |
|----------|----------|--------|
| **Anthropic** | `→ { type: "image", source: { type: "base64", media_type, data } }` | `anthropic.js` |
| **OpenAI** | `→ { type: "image_url", image_url: { url: "data:mime;base64,..." } }` | `openai-completions.js` |
| **Google/Gemini** | `→ { inlineData: { mimeType, data } }` | `google-shared.js` |
| **Amazon Bedrock** | `→ { image: createImageBlock(mimeType, data) }` | `amazon-bedrock.js` |

**关键：不支持图片的模型会自动忽略图片内容**，不会报错。pi-ai 通过检查 `model.input.includes("image")` 来决定是否过滤掉图片块。

---

## 2. Pi-AI-Server Bridge 层面（✅ 已支持透传）

### 2.1 HTTP API 接口

`bridge/pi-ai-server.ts` 的 `ChatRequest` 已支持 content 为数组格式：

```typescript
interface ChatRequest {
  messages: Array<{
    role: "system" | "user" | "assistant" | "tool";
    content: string | Array<{ type: string; text?: string; [key: string]: any }>;
    // ↑ 已支持 array 格式，可以传 image block
  }>;
  // ...
}
```

### 2.2 Context 转换

`convertToContext()` 对用户消息的 content 处理：

```typescript
if (msg.role === "user") {
  context.messages.push({
    role: "user",
    content: typeof msg.content === "string" ? msg.content : msg.content,
    //        ↑ string 直接用                   ↑ array 直接透传
    timestamp: Date.now(),
  });
}
```

**结论：只要 HTTP 请求中的 content 包含正确格式的 image block，会被完整透传到 pi-ai 包。无需改动 bridge 代码。**

---

## 3. Nimbus Python 后端（⚠️ 需少量改动）

### 3.1 已有的基础设施

**pi_ai_http.py** — Message 类型已支持 list content：
```python
@dataclass
class Message:
    role: str
    content: str | List[Dict[str, Any]]  # ← 已支持 list 格式
```

**_build_request()** — 直接序列化 content，不区分类型：
```python
msg_list = []
for msg in messages:
    m = {"role": msg.role, "content": msg.content}  # ← list 会被 JSON 序列化为 array
    msg_list.append(m)
```

**pi_adapter.py** — _convert_messages_to_http() 对 list content 直接透传：
```python
if isinstance(content, list):
    result.append(HttpMessage(role=role, content=content))  # ← 直接透传
else:
    result.append(HttpMessage(role=role, content=content or ""))
```

### 3.2 唯一需要改动的地方

**`src/nimbus/server/api.py`** 的 `chat()` endpoint：

当前实现只用了 `data.content`（纯文本），没有处理 `data.attachments`。需要增加逻辑：

```python
# 当前代码（只传文本）
await session_manager.stream_chat(session_id, data.content)

# 需要改为：构造包含图片的 content
if data.attachments:
    content_parts = []
    # 添加文本部分
    if data.content:
        content_parts.append({"type": "text", "text": data.content})
    # 添加附件
    for att in data.attachments:
        if att.type == "image":
            content_parts.append({
                "type": "image",
                "data": att.content,       # base64
                "mimeType": att.mime_type,  # "image/png"
            })
        elif att.type in ("text", "pdf"):
            # 文本文件直接拼入
            content_parts.append({
                "type": "text",
                "text": f"\n--- {att.name} ---\n{att.content}\n---"
            })
    # 传递 content_parts 而不是 data.content
    await session_manager.stream_chat(session_id, content_parts)
else:
    await session_manager.stream_chat(session_id, data.content)
```

还需要检查 `stream_chat()` 是否支持接收 list 类型的 content（可能需要微调签名）。

---

## 4. 全链路数据流

```
用户粘贴图片
    │
    ▼
Web-UI: ChatInput → base64 编码
    │
    ▼
POST /sessions/{id}/chat
{
  content: "这张截图有什么问题？",
  attachments: [{
    type: "image",
    content: "iVBORw0KGgo...",  // base64
    mime_type: "image/png",
    name: "screenshot.png"
  }]
}
    │
    ▼
Nimbus api.py: 构造 content_parts
[
  { "type": "text", "text": "这张截图有什么问题？" },
  { "type": "image", "data": "iVBORw0KGgo...", "mimeType": "image/png" }
]
    │
    ▼
pi_adapter.py → pi_ai_http.py: Message(role="user", content=content_parts)
    │
    ▼
HTTP POST → pi-ai-server:3031
{
  "messages": [{
    "role": "user",
    "content": [
      { "type": "text", "text": "这张截图有什么问题？" },
      { "type": "image", "data": "iVBORw0KGgo...", "mimeType": "image/png" }
    ]
  }]
}
    │
    ▼
pi-ai-server.ts: convertToContext() → 直接透传 content array
    │
    ▼
pi-ai 包: stream(model, context)
    │
    ▼
自动转换为 Provider 格式:
  Anthropic: { type: "image", source: { type: "base64", media_type, data } }
  OpenAI:    { type: "image_url", image_url: { url: "data:...;base64,..." } }
  Google:    { inlineData: { mimeType, data } }
    │
    ▼
LLM Provider API → 返回文本回复
```

## 5. 需要改动的文件清单

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `src/nimbus/server/api.py` | chat endpoint 处理 attachments → 构造 content list | 小 |
| `src/nimbus/server/models.py` | AttachmentCreate 加 `mime_type` 字段（可选） | 极小 |
| `src/nimbus/agentos.py` | `stream_chat()` 签名支持 `content: str \| list` | 小 |
| `bridge/pi-ai-server.ts` | **无需改动** | 0 |
| `src/nimbus/bridge/pi_ai_http.py` | **无需改动** | 0 |
| `src/nimbus/adapters/pi_adapter.py` | **无需改动** | 0 |

## 6. 关于 PDF

PDF 不能直接作为 image 发送给 LLM（除非 Gemini 的原生 PDF 支持）。建议方案：

- **方案 A（简单）**：前端用 JS 库（如 `pdf.js`）提取 PDF 文本，作为 text 类型附件发送
- **方案 B（OCR）**：后端用 Python 库（如 `PyMuPDF` / `pdfplumber`）提取文本+OCR
- **方案 C（截图）**：将 PDF 每页渲染为图片，作为多个 image 附件发送（适合扫描件）

建议 MVP 阶段先用方案 A（前端提取），后续按需添加方案 B。

## 7. 关于模型兼容性

不是所有模型都支持图片。pi-ai 的 `Model.input` 字段标识了支持情况：

```typescript
model.input.includes('image')  // true = 支持 vision
```

支持图片的主流模型：
- `anthropic/claude-sonnet-4-20250514` ✅
- `anthropic/claude-opus-4-5` ✅
- `openai/gpt-4o` ✅
- `openai/gpt-4o-mini` ✅
- `google/gemini-2.5-pro` ✅
- `google/gemini-2.5-flash` ✅

**pi-ai 会自动过滤不支持图片的模型的 image block**，不会报错，只是图片会被忽略。所以前端无需做模型级别的兼容判断。
