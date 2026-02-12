# MMU 图片消息 Token 优化方案

> 版本: Draft v0.1
> 状态: RFC
> 日期: 2026-02-11

## 1. 问题描述

当用户上传图片（截图、照片等）时，图片以 base64 编码存入 MMU 的消息流中。当前实现存在三个问题，导致严重的 token 浪费：

### 1.1 图片在上下文中反复发送

```
用户上传截图（第 3 轮）
    ↓
第 3 轮: 发给 LLM ✅ 必要
第 4 轮: 又发给 LLM ⚠️ 浪费
第 5 轮: 又发给 LLM ⚠️ 浪费
  ...
第 20 轮: 还在发 ❌ 严重浪费
```

图片消息一旦进入 `StackFrame.messages`，每轮 `assemble_context()` 都会通过 `Message.to_dict()` 原封不动输出 base64 数据。10 轮对话 = 同一张图发 10 次。

### 1.2 token_estimate() 不计算图片 token

```python
# src/nimbus/core/memory/context.py  Message.token_estimate()
elif isinstance(self.content, list):
    total = 0
    for block in self.content:
        if isinstance(block, dict) and "text" in block:
            total += estimate_text(block["text"])   # ← 只算 text block
    return total                                     # ← image block 返回 0！
```

一张图片实际消耗 **1,000 - 5,000 token**，但 `token_estimate()` 返回 0。后果：

- `assemble_context()` 的 budget 分配失准——以为还有空间，实际已爆
- `should_compact()` 误判——永远觉得不需要压缩
- sliding window 切分位置偏移——图片消息"免费"占位

### 1.3 Compaction 无法处理图片

```python
# src/nimbus/core/compaction.py  DefaultCompactionLLM._format_messages()
content = msg.get("content", "")
# 如果 content 是 list（含图片），Python 会把它转成 "[{'type': 'image', ...}]"
# 摘要 LLM 看到的是一堆乱码，无法理解图片内容
```

即使 compaction 触发，图片消息也无法被有效摘要。

### 1.4 Token 浪费估算

| 场景 | 图片大小 | 单次 token | 20 轮浪费 |
|------|----------|-----------|-----------|
| 用户上传截图 (1080p) | ~200KB | ~1,600 | ~30,400 |
| 用户上传照片 (高清) | ~500KB | ~3,000 | ~57,000 |
| Agent Read 图片文件 | 不定 | ~1,500 | ~28,500 |
| 多张图片（3 张） | — | ~6,000 | ~114,000 |

按 Claude 定价 $3/M input token 计算，20 轮对话中 3 张图片浪费约 **$0.34**。看似不多，但在高频使用场景下累积可观。

## 2. 图片数据流全链路

```
┌──────────┐     ┌───────────┐     ┌─────────┐     ┌──────────────┐     ┌─────┐
│ Web UI   │────▶│ api.py    │────▶│  MMU    │────▶│assemble_     │────▶│ LLM │
│ 用户上传  │     │ L427      │     │         │     │context()     │     │     │
│ 图片      │     │ 构建      │     │ Message │     │ to_dict()    │     │     │
│          │     │ content   │     │ 存入    │     │ 原样输出     │     │     │
│          │     │ _parts    │     │ Stream  │     │ base64       │     │     │
└──────────┘     └───────────┘     └─────────┘     └──────────────┘     └─────┘

content_parts 结构：
[
  {"type": "text", "text": "帮我看看这个截图"},
  {"type": "image", "data": "iVBORw0KGgo...(巨大base64)", "mimeType": "image/png"}
]
```

### 涉及文件

| 文件 | 角色 | 图片相关代码 |
|------|------|------------|
| `src/nimbus/server/api.py` | 入口 | L427: 构建 `content_parts`，image block 含 base64 |
| `src/nimbus/core/memory/context.py` | 存储 | `Message.content` 存 list；`token_estimate()` 不算 image |
| `src/nimbus/core/memory/mmu.py` | 组装 | `assemble_context()` 原样输出；budget 计算失准 |
| `src/nimbus/core/compaction.py` | 压缩 | `_format_messages()` 无法处理 list content |

## 3. 各模型图片 Token 计算方式

| 模型 | 计算方式 | 典型消耗 |
|------|----------|---------|
| **Claude 3.5** | 按像素：`(width × height) / 750` | 1080p ≈ 2,764 token |
| **GPT-4V** | 按 tile：先缩放到 2048，再按 512×512 tile 切分，每 tile 170 token + 固定 85 | 1080p ≈ 765 token (low) / 1,105 token (high) |
| **Gemini Pro** | 按图片个数：固定 258 token/张 | 258 token |

由于模型差异大，`token_estimate()` 应使用一个**保守的中间值**，不需要精确。

## 4. 解决方案

### 4.1 方案 A: 修复 token_estimate()（基础修复）

**目标**：让 budget 计算准确，不影响其他逻辑。

**改动文件**：`src/nimbus/core/memory/context.py`

```python
# Message.token_estimate() 修改
elif isinstance(self.content, list):
    total = 0
    for block in self.content:
        if isinstance(block, dict):
            if "text" in block:
                total += estimate_text(block["text"])
            elif block.get("type") == "image":
                # 保守估计：不同模型差异大，取中间值
                total += 1500
    return total
```

**效果**：budget 计算正确，sliding window 切分准确。但图片仍然每轮发送。

### 4.2 方案 B: 图片降级（核心优化）

**目标**：图片首次出现时保留原始 base64，后续轮次自动替换为文字 placeholder，大幅减少 token。

**策略**：

```
第 3 轮（图片首次出现）:
  content: [
    {"type": "text", "text": "帮我看看这个截图"},
    {"type": "image", "data": "base64...", "mimeType": "image/png"}   ← 原图
  ]

第 4 轮及以后（图片已被 LLM 看过）:
  content: [
    {"type": "text", "text": "帮我看看这个截图"},
    {"type": "text", "text": "[📷 Image: 用户截图 (200KB, image/png) — 已在上文展示]"}  ← placeholder
  ]
```

**改动文件**：`src/nimbus/core/memory/mmu.py`

在 `assemble_context()` 中增加图片降级逻辑：

```python
def assemble_context(self, max_tokens=None, filter_discardable=True):
    # ... 现有逻辑 ...
    
    # 在最终输出前，对消息做图片降级
    messages = self._downgrade_seen_images(messages)
    return messages

def _downgrade_seen_images(self, messages: List[Dict]) -> List[Dict]:
    """
    将已经出现过的图片替换为文字 placeholder。
    只保留最后一次出现的图片原始数据（最相关的那次）。
    """
    seen_images = set()  # 跟踪已见过的图片（用 hash 或 index 标识）
    
    # 第一遍：从后往前扫描，标记每张图片最后一次出现的位置
    last_occurrence = {}  # image_key -> message_index
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    key = self._image_key(block)
                    if key not in last_occurrence:
                        last_occurrence[key] = i
    
    # 第二遍：从前往后，非最后一次出现的图片替换为 placeholder
    result = []
    for i, msg in enumerate(messages):
        content = msg.get("content")
        if isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    key = self._image_key(block)
                    if last_occurrence.get(key) == i:
                        new_content.append(block)  # 保留原图（最后一次出现）
                    else:
                        # 替换为 placeholder
                        mime = block.get("mimeType", "image/unknown")
                        new_content.append({
                            "type": "text",
                            "text": f"[📷 Image ({mime}) — 已在上文展示，此处省略]"
                        })
                else:
                    new_content.append(block)
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)
    
    return result

def _image_key(self, block: dict) -> str:
    """生成图片的唯一标识（用 base64 前 64 字符做指纹，避免全量 hash）"""
    data = block.get("data", "")
    prefix = data[:64] if isinstance(data, str) else ""
    mime = block.get("mimeType", "")
    return f"{mime}:{prefix}"
```

**效果**：同一张图在整个上下文中只发送一次（最后出现的那次），其余位置用约 20 token 的 placeholder 替代。

### 4.3 方案 C: Compaction 图片感知（配套修复）

**目标**：让 compaction 的摘要能正确处理图片消息。

**改动文件**：`src/nimbus/core/compaction.py`

```python
# DefaultCompactionLLM._format_messages() 修改
def _format_messages(self, messages: List[Dict[str, Any]]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        
        # 处理 multimodal content (list of blocks)
        if isinstance(content, list):
            text_parts = []
            image_count = 0
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        image_count += 1
                        mime = block.get("mimeType", "image/unknown")
                        text_parts.append(f"[Attached image: {mime}]")
            content = "\n".join(text_parts)
        
        # ... 后续格式化逻辑不变 ...
```

**效果**：摘要 LLM 能看到 `[Attached image: image/png]` 而非乱码，生成的摘要更准确。

## 5. 推荐实施计划

| Phase | 内容 | 改动量 | 效果 |
|-------|------|--------|------|
| **Phase 1** | 方案 A: 修 `token_estimate()` | ~5 行 | budget 准确 |
| **Phase 2** | 方案 B: 图片降级 `_downgrade_seen_images()` | ~50 行 | 🔥 核心省 token |
| **Phase 3** | 方案 C: compaction 图片感知 | ~15 行 | 摘要质量提升 |

Phase 1 + 2 建议一起做，总改动约 55 行，覆盖三个文件。Phase 3 可独立做。

## 6. 方案 B 详细行为示例

### 场景：用户上传截图后持续对话 10 轮

```
轮次 1: 用户: "你好"
轮次 2: 助手: "你好！"
轮次 3: 用户: "帮我看看这个截图" + [📷 800×600 png, ~150KB]
轮次 4: 助手: "这是一个登录页面..."
轮次 5: 用户: "那个按钮在哪"
轮次 6: 助手: "在右上角..."
  ...
轮次 12: 用户: "还是那个截图的问题..."
```

**当前行为**（轮次 12 发给 LLM 的内容）：
```
messages = [
  {role: system, content: "...pinned..."},
  {role: user, content: [{text: "帮我看看"}, {image: "base64...150KB"}]},  ← 原图
  {role: assistant, content: "这是一个登录页面..."},
  {role: user, content: "那个按钮在哪"},
  ...
  {role: user, content: "还是那个截图的问题..."},
]
```
图片 token：**~1500 × 1 = 1500**（但每轮都发）

**优化后行为**（轮次 12 发给 LLM 的内容）：
```
messages = [
  {role: system, content: "...pinned..."},
  {role: user, content: [
    {text: "帮我看看"},
    {text: "[📷 Image (image/png) — 已在上文展示，此处省略]"}   ← placeholder ~20 token
  ]},
  {role: assistant, content: "这是一个登录页面..."},
  ...
  {role: user, content: "还是那个截图的问题..."},
]
```

**但如果图片在 hot context（最近 15 条消息）内**，保留原图：
```
messages = [
  {role: system, content: "..."},
  ... 历史消息（图片被 placeholder 替代）...
  {role: user, content: [{text: "再看看"}, {image: "base64..."}]},   ← 在 hot context 内，保留原图
  {role: assistant, content: "..."},
]
```

这样 LLM 最近看到的图片保持原样，更早的自动降级。

## 7. 边界情况

| 情况 | 处理方式 |
|------|----------|
| 同一张图被上传两次 | `_image_key` 基于 base64 前缀匹配，视为同一张图，只保留最后一次 |
| 不同图片 | 各自独立跟踪，各自降级 |
| Agent 通过 Read 读取的图片 | 当前 `_read_image()` 返回文字描述而非 base64，不受影响 |
| 图片在 pinned context 中 | pinned context 不做图片降级（理论上不应有图片） |
| compaction 摘要后图片丢失 | 方案 C 确保摘要中保留 `[Attached image]` 标记 |
| 用户引用"之前那张图" | placeholder 提示"已在上文展示"，LLM 仍有文字上下文可推理；如需再看原图，用户可重新上传 |

## 8. 未来可选扩展

| 方向 | 描述 | 优先级 |
|------|------|--------|
| **图片描述缓存** | 首次发送时让 LLM 生成图片描述，缓存到 `Message.meta`，后续用描述替代（比纯 placeholder 更有信息量） | 中 |
| **模型感知 token 估算** | 根据当前 model_id 选择不同的图片 token 估算公式（Claude 按像素、GPT 按 tile） | 低 |
| **图片 storage + 引用** | 图片存到 storage 层，上下文只放引用 ID，需要时通过工具 fetch | 低（过度工程化） |
| **分辨率降级** | 旧图片自动降低分辨率（缩小尺寸）再发送，而非完全替换为文字 | 中 |

---

*本文档为 RFC 状态，欢迎评审。*
