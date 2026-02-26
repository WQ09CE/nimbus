# Model System Cleanup — 模型选择系统整改方案

> **Status**: Draft  
> **Author**: Architect Agent  
> **Date**: 2025-02-26  
> **Scope**: 后端 4 文件 + 前端 3 文件，约 200 行改动

---

## 目录

1. [问题诊断](#1-问题诊断)
2. [设计原则](#2-设计原则)
3. [架构方案](#3-架构方案)
4. [详细改动计划](#4-详细改动计划)
5. [数据流图](#5-数据流图)
6. [实施步骤](#6-实施步骤)
7. [测试计划](#7-测试计划)

---

## 1. 问题诊断

### 1.1 "default" 的三重身份危机

| 位置 | "default" 映射到 | 文件 & 行号 |
|------|-----------------|-------------|
| `manifest.py` `_REGISTRY["default"]` | `anthropic/claude-sonnet-4-6` | `src/nimbus/core/models/manifest.py:120` |
| `config.py` `default_model` | `google/gemini-3-flash-preview` | `src/nimbus/config.py:24` |
| `specialist_tools.py` `_create_profile(model_id="default")` | 透传字符串 "default"，由 manifest 解析 → claude-sonnet-4-6 | `src/nimbus/orchestration/specialist_tools.py:~110` |

**根因**：`manifest.py` 自建了一套 "default" → 特定模型的映射，与 `config.py` 的全局默认模型定义脱节。两个"默认"互不知情。

### 1.2 前端硬编码过时模型

```typescript
// web-ui/src/stores/chat-store.ts:189
llm_config: options?.llm_config || {
  provider: "anthropic",
  model_id: "claude-3-5-sonnet-20241022",  // ← 这个模型已不存在！
},
```

前端 `createNewSession` 不走后端默认值，直接硬编码了一个过时的模型 ID。而后端 `api_vibe.py:~line 170` 有正确逻辑：

```python
model = payload.get("model") or cfg.default_model  # ← 后端会 fallback 到 config
```

但 `api.py` 的 session 创建路径直接透传前端传来的 `llm_config`，没有 fallback 处理。

### 1.3 DispatchCard 不显示 subagent 实际模型

**数据链路断裂**：

```
orchestrator 调用 Explore/Implement tool
  → specialist_tools.py: model_name = kwargs.get("model", "")
    → 如果空串，不在 tool.args 里体现
      → DispatchCard: model = tool.args?.model || ""  → 什么都不显示

但实际上：
  → agentos.spawn() 中确实发出 PROC_SPAWNED 事件
    → data: { model: "...", model_full: "..." }
      → 前端 chat-store.ts SSE handler 没处理这个事件
```

PROC_SPAWNED 事件包含 `model` 和 `model_full` 字段（`agentos.py:~line 604`），但前端从未消费它。

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| **Single Source of Truth** | `config.py` 的 `default_model` 是唯一的默认模型定义 |
| **后端主导** | 前端不硬编码任何模型名，默认值全部从后端 API 获取 |
| **透明性** | "default" 在所有 UI 中显示实际解析后的模型名 |
| **最小改动** | 不重构整个模型系统，只修复混乱点 |
| **向后兼容** | 已存在的 session 不受影响 |

---

## 3. 架构方案

### 3.1 统一默认模型解析链

```
所有入口 → "default" or "" or null
         → registry.normalize("default")
         → 读取 config.py.default_model
         → 返回 "google/gemini-3-flash-preview"（或用户配置的值）
```

**改动核心**：让 `manifest.py` 的 `_REGISTRY["default"]` 动态指向 `config.py` 的 `default_model`，而不是硬编码 `anthropic/claude-sonnet-4-6`。

### 3.2 前端获取默认模型

```
GET /api/v1/config → { default_model: "google/gemini-3-flash-preview", ... }
前端 createNewSession → llm_config: null（让后端决定）
                      → 或读取 /api/v1/config 的 default_model 做显示
```

### 3.3 Subagent 模型信息透传

```
specialist_tools.py → 总是在 tool result 中附带 resolved_model
PROC_SPAWNED 事件 → 前端 SSE handler 消费
DispatchCard → 优先从 PROC_SPAWNED 读取 model，其次从 tool.args.model 读取
```

---

## 4. 详细改动计划

### 4.1 后端改动

#### 4.1.1 `manifest.py` — 动态默认模型映射

**文件**: `src/nimbus/core/models/manifest.py`  
**改动**: 将 `_REGISTRY["default"]` 改为延迟解析

```python
# ─── 当前代码（约 line 120）───
_REGISTRY["default"] = ModelManifest("anthropic/claude-sonnet-4-6", CLAUDE_FEATURES)

# ─── 改为 ───
# 删除这行硬编码。
# 在 get_model_manifest() 函数中处理 "default" 特殊值：

def get_model_manifest(model_id: str) -> ModelManifest:
    """Get manifest for a model, with 'default' resolving to config's default_model."""
    if model_id == "default" or not model_id:
        from nimbus.config import get_config
        model_id = get_config().default_model

    # ... 原有匹配逻辑继续
    if model_id in _REGISTRY:
        return _REGISTRY[model_id]
    # ... fuzzy match 等
```

**影响范围**：所有调用 `get_model_manifest("default")` 的地方都会自动走 config。

#### 4.1.2 `registry.py` — normalize() 处理 "default"

**文件**: `src/nimbus/core/models/registry.py`  
**改动**: `normalize()` 方法识别 "default" 并解析为实际模型

```python
# ─── 在 normalize() 方法开头添加 ───
@staticmethod
def normalize(model_id: str) -> str:
    """Normalize model ID. 'default' resolves to config's default_model."""
    if not model_id or model_id.strip().lower() == "default":
        from nimbus.config import get_config
        model_id = get_config().default_model

    # ... 原有 normalize 逻辑继续
```

**位置**: `registry.py` 的 `ModelRegistry.normalize()` 方法（约 line 80）

#### 4.1.3 `specialist_tools.py` — 透传实际模型名

**文件**: `src/nimbus/orchestration/specialist_tools.py`  
**改动**: 当 `model_name` 为空时，解析并记录实际使用的模型

当前代码流程（约 line 85-130）：
```python
model_name = kwargs.get("model", "")
# ... 如果 model_name 为空，不传给 profile，使用 orchestrator 的 LLM
```

**改为**：
```python
model_name = kwargs.get("model", "")

# 解析实际使用的模型名（用于透传给前端）
resolved_model = model_name
if not resolved_model:
    # 未指定时，subagent 继承 orchestrator 的 LLM
    # 从 self._llm 获取实际模型名（需要 LLM client 暴露 model_id 属性）
    if hasattr(self, '_llm') and hasattr(self._llm, 'model_id'):
        resolved_model = self._llm.model_id
    elif hasattr(self, '_llm') and hasattr(self._llm, 'model'):
        resolved_model = self._llm.model
    else:
        from nimbus.config import get_config
        resolved_model = get_config().default_model
```

然后在返回结果中附带 `resolved_model`：
```python
# 在 _create_profile() 调用时
profile = _create_profile(
    model_id=model_name or "default",  # ← 空串改为 "default"，走统一解析
    role=role,
    ...
)

# 在返回给 orchestrator 的 ToolResult 中，附带 model 信息
# （这样前端可以从 tool_result 里读取）
```

#### 4.1.4 `server/api.py` + `server/models.py` — 暴露 default_model 到 /api/config

**文件**: `src/nimbus/server/api.py` (line 132-139), `src/nimbus/server/models.py` (line ~310)

**改动 1** — `models.py` 的 `ServerConfig` 增加 `default_model` 字段：
```python
class ServerConfig(BaseModel):
    """Server configuration response."""
    default_memory_type: str = "tiered"
    default_planner_type: str = "dag"
    max_concurrent_sessions: int = 10
    mcp_servers: List[str] = Field(default_factory=list)
    default_model: str = ""          # ← 新增
    default_provider: str = ""       # ← 新增
```

**改动 2** — `api.py` 的 `get_config()` 返回实际默认模型：
```python
@router.get("/config", response_model=ServerConfig)
async def get_config():
    """Get server configuration."""
    from nimbus.config import get_config as get_nimbus_config
    cfg = get_nimbus_config()

    # 解析 default_model 为 provider + model_id
    parts = cfg.default_model.split("/", 1)
    provider = parts[0] if len(parts) == 2 else "unknown"
    model_id = parts[1] if len(parts) == 2 else cfg.default_model

    return ServerConfig(
        default_memory_type="tiered",
        default_planner_type="dag",
        max_concurrent_sessions=10,
        mcp_servers=[],
        default_model=cfg.default_model,        # ← "google/gemini-3-flash-preview"
        default_provider=provider,               # ← "google"
    )
```

#### 4.1.5 `session_v2.py` — 创建 session 时 fallback 到 config default

**文件**: `src/nimbus/server/session_v2.py` (line ~210)

当前逻辑 `model_id = model_config.get("model_id", "default")` 已经会用 "default"。但需要确保这个 "default" 被正确解析（由 4.1.1 和 4.1.2 的改动覆盖）。

**额外改动**：当 `model_config` 为空（前端不传 llm_config）时，用 config 的默认值填充返回给前端的 session 数据：

```python
# 在 create_session 返回前，确保 session 的 model_config 包含实际模型信息
if not model_config or not model_config.get("model_id"):
    from nimbus.config import get_config
    cfg = get_config()
    parts = cfg.default_model.split("/", 1)
    model_config = {
        "provider": parts[0] if len(parts) == 2 else "",
        "model_id": parts[1] if len(parts) == 2 else cfg.default_model,
    }
```

---

### 4.2 前端改动

#### 4.2.1 `chat-store.ts` — 移除硬编码默认模型

**文件**: `web-ui/src/stores/chat-store.ts` (line 189)

**当前代码**：
```typescript
llm_config: options?.llm_config || {
  provider: "anthropic",
  model_id: "claude-3-5-sonnet-20241022",
},
```

**改为**：
```typescript
// 不传 llm_config，让后端使用 config.py 的默认值
// 后端会在 session response 中返回实际使用的模型
...(options?.llm_config ? { llm_config: options.llm_config } : {}),
```

即：如果用户没有显式选择模型，前端 **不传** `llm_config`，由后端决定。后端 session response 会包含实际使用的模型信息（由 4.1.5 保证）。

#### 4.2.2 `ModelSelector.tsx` — 显示实际默认模型名

**文件**: `web-ui/src/components/chat/ModelSelector.tsx`

当前行为：`session.llm_config.model_id` 为空时显示 "default" 文本。

**改动**：

1. 在组件挂载时获取后端 `/api/v1/config` 的 `default_model`：

```typescript
const [serverDefault, setServerDefault] = useState<string>("");

useEffect(() => {
    fetch("/api/v1/config")
        .then(r => r.json())
        .then(cfg => setServerDefault(cfg.default_model || ""))
        .catch(() => {});
}, []);
```

2. 显示时，将 "default" 替换为实际模型名：

```typescript
// 当前选中的模型
const currentModelId = optimisticModelId
    || session.llm_config?.model_id
    || "";

// 显示文本：如果是空或 "default"，显示实际默认模型
const displayName = (!currentModelId || currentModelId === "default")
    ? (serverDefault || "default")
    : currentModelId;
```

3. 在模型列表中，"default" 选项显示为 `"Default (google/gemini-3-flash-preview)"` 格式：

```typescript
// 在模型列表渲染中
{models.map(model => (
    <button key={model.id} ...>
        {model.id === "default"
            ? `Default (${serverDefault})`
            : model.display_name || model.id}
    </button>
))}
```

#### 4.2.3 `DispatchCard.tsx` — 显示 subagent 实际模型

**文件**: `web-ui/src/components/chat/tools/DispatchCard.tsx`

**方案**：从 workflow-store 的 WorkflowCall 中获取 PROC_SPAWNED 携带的 model 信息。

**步骤 1** — `chat-store.ts` SSE handler 增加 `proc_spawned` 事件处理：

```typescript
// 在 SSE event handler 的 switch 中添加
case "proc_spawned": {
    const procData = data as Record<string, any>;
    const pid = procData?.pid;
    const model = procData?.model_full || procData?.model || "";
    const parentActionId = procData?.parent_action_id;

    if (parentActionId) {
        // 找到对应的 specialist tool call，附加 model 信息
        const metaIdx = toolCalls.findIndex(tc => tc.id === parentActionId);
        if (metaIdx >= 0) {
            const meta = toolCalls[metaIdx];
            // 将 model 信息注入到 tool call 的 args 中
            if (!meta.arguments.resolved_model) {
                meta.arguments.resolved_model = model;
            }
        }
    }
    break;
}
```

**步骤 2** — `DispatchCard.tsx` 读取 model 信息：

```typescript
// 当前代码
const model = (tool.args?.model as string) || "";

// 改为：优先读 resolved_model（来自 PROC_SPAWNED），其次读 args.model
const model = (tool.args?.resolved_model as string)
    || (tool.args?.model as string)
    || "";
```

**替代方案**（更简单，推荐）：

在后端 `specialist_tools.py` 中，直接在返回的 `ToolResult` 里附带 `resolved_model` 字段。这样前端无需处理额外的 SSE 事件。

```python
# specialist_tools.py - 在构建返回给 orchestrator 的结果时
return ToolResult(
    tool_call_id=call_id,
    output=result_text,
    metadata={
        "resolved_model": resolved_model,
        "role": role,
    }
)
```

然后 DispatchCard 从 `tool.result?.metadata?.resolved_model` 读取。

**注意**: 需要确认当前 ToolResult 是否支持 metadata 字段。查看 `protocol.py` 的 ToolResult 定义。如果不支持，则走 SSE 方案。

---

### 4.3 需要额外检查的关联代码

| 文件 | 关注点 | 优先级 |
|------|--------|--------|
| `src/nimbus/agentos.py:~600` | PROC_SPAWNED 事件的 `model` 字段来源是否正确 | ✅ 已确认正确 |
| `src/nimbus/adapters/llm_factory.py` | `create_llm_client("default")` 是否能正确解析 | 中 |
| `src/nimbus/server/api_vibe.py:~170` | `model = payload.get("model") or cfg.default_model` — 已经正确 | ✅ 无需改动 |
| `web-ui/src/lib/api/sessions.ts` | `SessionCreateRequest` 类型需要允许不传 `llm_config` | 低 |

---

## 5. 数据流图

### 5.1 当前（有问题的）流程

```
┌────────────┐     llm_config: {provider: "anthropic",     ┌─────────────┐
│  Frontend   │ ──  model_id: "claude-3-5-sonnet-20241022"} → │  api.py      │
│ chat-store  │     (硬编码！过时！)                          │ create_sess  │
└────────────┘                                               └──────┬──────┘
                                                                    │
                                                            透传 llm_config
                                                                    ↓
                                                          ┌─────────────────┐
                                                          │ session_v2.py    │
                                                          │ model_id="..."   │
                                                          └─────────────────┘

Specialist 调用时:
┌──────────────────┐   model=""   ┌──────────────────────┐
│ orchestrator LLM  │ ──────────→ │ specialist_tools.py   │
│ (不指定 model)    │             │ model_name = ""       │
└──────────────────┘             │ → 不传给前端           │
                                  └──────────┬───────────┘
                                             │
                                   tool.args.model = ""
                                             ↓
                                  ┌──────────────────────┐
                                  │ DispatchCard.tsx      │
                                  │ model = "" → 不显示   │
                                  └──────────────────────┘
```

### 5.2 整改后的流程

```
┌────────────┐     llm_config: null (不传)       ┌─────────────┐
│  Frontend   │ ──────────────────────────────── → │  api.py      │
│ chat-store  │                                   │ create_sess  │
└────────────┘                                    └──────┬──────┘
                                                         │
                                               fallback to config.default_model
                                                         ↓
                                               ┌─────────────────┐
                                               │ session_v2.py    │
                                               │ "default" →      │
                                               │  config.default  │
                                               │  _model          │
                                               └─────────────────┘
                                                         │
                                               返回 session response:
                                               llm_config: {
                                                 provider: "google",
                                                 model_id: "gemini-3-flash-preview"
                                               }
                                                         ↓
                                               ┌─────────────────┐
                                               │ ModelSelector    │
                                               │ 显示实际模型名    │
                                               └─────────────────┘

Specialist 调用时:
┌──────────────────┐   model=""   ┌──────────────────────────┐
│ orchestrator LLM  │ ──────────→ │ specialist_tools.py       │
│                   │             │ model_name = "" →          │
└──────────────────┘             │  resolved = self._llm.model│
                                  │  或 config.default_model   │
                                  └──────────┬────────────────┘
                                             │
                                   PROC_SPAWNED: {model: "gemini-3-flash-preview"}
                                   + tool.args.resolved_model = "..."
                                             ↓
                                  ┌──────────────────────┐
                                  │ DispatchCard.tsx      │
                                  │ 显示实际模型名 ✓       │
                                  └──────────────────────┘
```

---

## 6. 实施步骤

按依赖顺序分 3 步：

### Phase 1: 后端统一默认值（无前端影响）

| # | 文件 | 改动 | 估计行数 |
|---|------|------|----------|
| 1 | `manifest.py` | 删除 `_REGISTRY["default"]` 硬编码，在 `get_model_manifest()` 中动态解析 "default" → config | ~15 行 |
| 2 | `registry.py` | `normalize()` 方法开头处理 "default" / "" / None → config.default_model | ~8 行 |
| 3 | `server/models.py` | `ServerConfig` 增加 `default_model`, `default_provider` 字段 | ~3 行 |
| 4 | `server/api.py` | `get_config()` 返回实际默认模型 | ~10 行 |
| 5 | `session_v2.py` | create_session 空 model_config 时填充实际模型到 response | ~10 行 |

**验证**: 调用 `GET /api/v1/config` 确认返回 `default_model: "google/gemini-3-flash-preview"`。

### Phase 2: Specialist 模型透传

| # | 文件 | 改动 | 估计行数 |
|---|------|------|----------|
| 6 | `specialist_tools.py` | 空 model_name → 解析为实际模型并写入事件/结果 | ~20 行 |

**验证**: 启动 agent，观察 PROC_SPAWNED 事件是否包含正确的 model 字段。

### Phase 3: 前端适配

| # | 文件 | 改动 | 估计行数 |
|---|------|------|----------|
| 7 | `chat-store.ts` | 移除硬编码 llm_config，不传让后端决定 | ~5 行 |
| 8 | `ModelSelector.tsx` | 从 /api/config 获取 default_model，显示实际名称 | ~25 行 |
| 9 | `DispatchCard.tsx` | 读取 resolved_model 或从 PROC_SPAWNED 获取模型信息 | ~15 行 |
| 10 | `chat-store.ts` (SSE handler) | 处理 proc_spawned 事件，附加 model 信息到 toolCall | ~20 行 |

**验证**: 
- 新建 session → ModelSelector 显示 `gemini-3-flash-preview`
- Dispatch agent → DispatchCard 显示实际模型名

**总估计改动**: ~130 行

---

## 7. 测试计划

### 7.1 单元测试

```python
# test_manifest.py
def test_default_resolves_to_config():
    """'default' 应该解析为 config.py 的 default_model"""
    manifest = get_model_manifest("default")
    cfg = get_config()
    assert manifest.model_id == cfg.default_model

def test_empty_string_resolves_to_config():
    """空串应该解析为 config.py 的 default_model"""
    manifest = get_model_manifest("")
    cfg = get_config()
    assert manifest.model_id == cfg.default_model

# test_registry.py
def test_normalize_default():
    """normalize('default') → config.default_model"""
    result = ModelRegistry.normalize("default")
    cfg = get_config()
    assert result == cfg.default_model

def test_normalize_empty():
    """normalize('') → config.default_model"""
    result = ModelRegistry.normalize("")
    cfg = get_config()
    assert result == cfg.default_model
```

### 7.2 集成测试

| 场景 | 预期行为 |
|------|----------|
| 前端不传 llm_config 创建 session | 后端使用 config.default_model，session response 包含正确模型 |
| 前端传 llm_config={model_id: "claude-sonnet-4-6"} | 使用指定模型，不走 default |
| Specialist 不指定 model 参数 | PROC_SPAWNED 事件包含 orchestrator 的实际模型 |
| `GET /api/v1/config` | 返回 `default_model: "google/gemini-3-flash-preview"` |
| 修改 `~/.nimbus/config.json` 的 default_model | 重启后所有 "default" 解析为新值 |
| 设置 `NIMBUS_MODEL` 环境变量 | 覆盖 config.json 和代码默认值 |

### 7.3 前端 E2E 验证

1. **新建 Session**：打开应用 → 创建 session → ModelSelector 显示 `gemini-3-flash-preview`（而非 "default" 或空）
2. **切换模型**：ModelSelector 选择 claude → 发消息 → 正常工作
3. **Dispatch 模型显示**：发送需要 dispatch 的任务 → DispatchCard 显示 subagent 实际使用的模型名
4. **Default 标签**：如果有 "Default" 选项，应显示为 `Default (gemini-3-flash-preview)`

---

## 附录：关键文件索引

| 文件 | 职责 |
|------|------|
| `src/nimbus/config.py` | 全局配置单例，`default_model` 唯一真相源 |
| `src/nimbus/core/models/manifest.py` | 模型能力清单，feature flags |
| `src/nimbus/core/models/registry.py` | 模型 ID 规范化、provider 映射 |
| `src/nimbus/orchestration/specialist_tools.py` | Specialist 工具，spawn subagent |
| `src/nimbus/agentos.py:~600` | `spawn()` 发出 PROC_SPAWNED 事件 |
| `src/nimbus/server/api.py` | REST API 路由 |
| `src/nimbus/server/api_vibe.py` | Vibe IDE 兼容 API |
| `src/nimbus/server/models.py` | Pydantic 请求/响应模型 |
| `src/nimbus/server/session_v2.py` | Session 管理器 |
| `web-ui/src/stores/chat-store.ts` | 前端状态管理 + SSE 处理 |
| `web-ui/src/components/chat/ModelSelector.tsx` | 模型选择器组件 |
| `web-ui/src/components/chat/tools/DispatchCard.tsx` | Dispatch 任务卡片组件 |
