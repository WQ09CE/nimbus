# ChatInput 文件/图片上传增强方案

> 让用户在聊天时可以粘贴图片、拖拽文件、点击上传，作为消息的一部分发送给 Agent。

## 1. 需求概述

**目标：** 轻量级文件输入能力，不做文件树浏览器，只增强聊天输入框。

**支持的交互方式：**
- `Ctrl/Cmd + V` 粘贴剪贴板图片（截图场景）
- 拖拽文件到输入框区域（Drag & Drop）
- 点击 📎 按钮选择文件

**支持的文件类型：**

| 类别 | 格式 | 处理方式 |
|------|------|----------|
| 图片 | jpg, png, gif, webp | Base64 编码 → vision API |
| 文本 | txt, md, json, csv, yaml, log | 读取文本内容 → 拼入消息 context |
| 文档 | pdf | 前端读取文本（或后端解析）→ 拼入消息 context |

## 2. 现状分析

### 已有的基础设施

**后端（已预留）：**
- `ChatRequest` model 已有 `attachments: List[AttachmentCreate]` 字段
- `AttachmentCreate` 定义了 `type: file/url/text`，带 `path/url/content/name` 字段
- 但 `api.py` 的 chat endpoint 目前只用了 `data.content`，未处理 attachments

**前端（待改造）：**
- `ChatInput.tsx` — 纯文本 textarea，无文件能力
- `ChatRequest` 接口已有 `attachments?: unknown[]`（未使用）
- `chat-store.ts` 的 `sendMessage` 只接收 `string`
- API client 只用 JSON（无 multipart）

### 关键文件清单

| 文件 | 当前状态 | 需要改动 |
|------|----------|----------|
| `web-ui/src/components/chat/ChatInput.tsx` | 纯文本输入 | **主要改造** |
| `web-ui/src/stores/chat-store.ts` | `sendMessage(content: string)` | 扩展签名 |
| `web-ui/src/lib/api/chat.ts` | `ChatRequest` 已有 attachments | 使用 attachments |
| `src/nimbus/server/models.py` | `AttachmentCreate` 已定义 | 可能微调 |
| `src/nimbus/server/api.py` | chat endpoint 未处理 attachments | 需要打通 |

## 3. 前端改动设计

### 3.1 ChatInput 组件改造

**新增 Props：**

```typescript
interface ChatInputProps {
  onSend: (message: string, attachments?: Attachment[]) => void;  // 扩展
  // ... 其他不变
}

interface Attachment {
  id: string;          // 前端生成的唯一 ID
  type: "image" | "text" | "pdf";
  name: string;        // 文件名
  size: number;        // 字节数
  content: string;     // base64 (图片) 或 文本内容
  mimeType: string;    // e.g. "image/png", "text/plain"
  preview?: string;    // 图片缩略图 URL (URL.createObjectURL)
}
```

**新增 UI 元素：**

```
┌─────────────────────────────────────────────────┐
│ [附件预览条 - 仅有附件时显示]                      │
│ ┌──────┐ ┌──────────────┐                        │
│ │ 🖼️   │ │ 📄 report.pdf │                       │
│ │ 截图  │ │   128KB  ✕   │                       │
│ │  ✕   │ └──────────────┘                        │
│ └──────┘                                         │
├─────────────────────────────────────────────────┤
│ 📎 │  Type a message...                    │ ⬆️ │
└─────────────────────────────────────────────────┘
```

- 📎 按钮：输入框左侧，点击弹出文件选择器
- 附件预览条：输入框上方，显示已添加的文件
  - 图片：显示缩略图 + 文件名
  - 文件：显示图标 + 文件名 + 大小
  - 每个附件右上角有 ✕ 删除按钮
- 拖拽覆盖层：拖拽文件到输入区域时显示提示

**新增 Handlers：**

```typescript
// 1. 粘贴图片
const handlePaste = (e: ClipboardEvent) => {
  const items = e.clipboardData?.items;
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      const file = item.getAsFile();
      // 读取为 base64，添加到 attachments
    }
  }
};

// 2. 拖拽文件
const handleDrop = (e: DragEvent) => {
  e.preventDefault();
  const files = e.dataTransfer?.files;
  // 逐个处理文件
};

// 3. 点击选择
const handleFileSelect = (e: ChangeEvent<HTMLInputElement>) => {
  const files = e.target.files;
  // 逐个处理文件
};
```

**文件处理逻辑（共用）：**

```typescript
async function processFile(file: File): Promise<Attachment> {
  if (file.type.startsWith("image/")) {
    // 图片 → base64
    const base64 = await readFileAsBase64(file);
    return { type: "image", content: base64, preview: URL.createObjectURL(file), ... };
  } else if (file.type === "application/pdf") {
    // PDF → 提取文本（可用 pdf.js 或发后端处理）
    // MVP: 先用 base64 传后端处理
    const base64 = await readFileAsBase64(file);
    return { type: "pdf", content: base64, ... };
  } else {
    // 文本文件 → 直接读取内容
    const text = await readFileAsText(file);
    return { type: "text", content: text, ... };
  }
}
```

**大小限制：**
- 单个图片：最大 10MB
- 单个文本文件：最大 5MB
- 单次附件数量：最多 5 个
- 超限时 toast 提示用户

### 3.2 Store 改动

```typescript
// chat-store.ts
interface ChatState {
  // ...
  sendMessage: (content: string, attachments?: Attachment[]) => Promise<void>;
}
```

`sendMessage` 内部变化：
- 构建 `ChatRequest` 时带上 `attachments`
- 用户消息中显示附件（在 UI 中渲染附件预览）

### 3.3 API 层改动

```typescript
// chat.ts - streamChat
export async function* streamChat(
  sessionId: string,
  message: string,
  attachments?: Attachment[],  // 新增
  signal?: AbortSignal
): AsyncGenerator<ChatEvent> {
  const request: ChatRequest = {
    content: message,
    attachments: attachments?.map(a => ({
      type: a.type,
      name: a.name,
      content: a.content,      // base64 or text
      mime_type: a.mimeType,
    })),
  };
  // ...
}
```

### 3.4 用户消息渲染

在 `ChatMessage.tsx` 中，用户消息需要能渲染附件：

```
┌──────────────────────────────────┐
│ 帮我分析一下这张截图的 UI 问题    │
│                                  │
│ ┌────────────────┐               │
│ │  📷 screenshot │               │
│ │  (click to     │               │
│ │   enlarge)     │               │
│ └────────────────┘               │
│                                  │
│ 📄 requirements.txt (2.3KB)      │
└──────────────────────────────────┘
```

## 4. 后端改动设计

### 4.1 Chat Endpoint 处理 Attachments

```python
# api.py - chat()
# 在构造用户消息时，处理 attachments
if data.attachments:
    for att in data.attachments:
        if att.type == "image":
            # 构造 vision message part
            # { "type": "image_url", "image_url": { "url": f"data:{mime};base64,{content}" } }
        elif att.type in ("text", "pdf"):
            # 将文件内容追加到消息 context
            # content += f"\n\n--- {att.name} ---\n{att.content}\n---"
```

### 4.2 LLM 适配

- **图片**：需要确认当前 LLM adapter 是否支持 vision（多模态）message format
- **文本/PDF**：直接拼入 user message content 即可，无需特殊处理

### 4.3 AttachmentCreate 模型微调（可选）

```python
class AttachmentCreate(BaseModel):
    type: str           # image, text, pdf
    name: Optional[str] = None
    content: str        # base64 (image/pdf) or raw text
    mime_type: Optional[str] = None
```

## 5. 数据流总览

```
用户操作                    前端                        后端
────────                  ──────                      ──────
粘贴/拖拽/选择文件
    │
    ▼
processFile()
读取为 base64/text
    │
    ▼
添加到 attachments[]
显示预览条
    │
    ▼
点击发送
    │
    ▼
sendMessage(content, attachments)
    │
    ▼
streamChat() 构造 ChatRequest
    ├─ content: "帮我分析..."
    └─ attachments: [{type: "image", content: "base64...", ...}]
        │
        ▼
    POST /sessions/{id}/chat
        │
        ▼
                                            chat() 处理 attachments
                                                │
                                                ├─ image → vision message part
                                                └─ text/pdf → 追加到 context
                                                │
                                                ▼
                                            stream_chat() → LLM
                                                │
                                                ▼
                                            SSE events → 前端
```

## 6. 实现步骤（建议顺序）

### Step 1: ChatInput UI 增强（纯前端，可立即看到效果）
- 添加 📎 按钮、粘贴/拖拽 handler
- 附件预览条 UI
- Attachment 类型定义
- 文件读取工具函数

### Step 2: 数据流打通（前端 → 后端）
- `sendMessage` 签名扩展
- `streamChat` 带上 attachments
- `ChatMessage` 渲染用户附件

### Step 3: 后端处理 Attachments
- chat endpoint 解析 attachments
- 图片 → vision format
- 文本/PDF → context 拼接
- LLM adapter 确认多模态支持

### Step 4: 细节优化
- 文件大小限制 & 错误提示
- 图片压缩（可选，大图发送前降低分辨率）
- PDF 文本提取优化
- 附件在历史消息中的持久化显示
