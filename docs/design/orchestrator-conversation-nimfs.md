# Orchestrator 对话历史写入 NimFS 设计方案

> 版本：v1.0 | 日期：2025-07 | 状态：待实现

---

## 1. 问题与目标

### 现状

| 层级 | 持久化 | 可回溯 |
|------|--------|--------|
| 子代理 VCPU 每步 | ✅ NimFS artifact (TraceManager) | ✅ |
| Orchestrator 用户↔AI 对话 | ⚠️ 仅 SQLite（结构化存储） | ❌ 无人类可读全链路视图 |

### 目标

每一轮 `用户消息 → AI 回复` 完成后，将对话写入 NimFS artifact：
- **Markdown 格式**：人类可直接阅读，方便调试和审计
- **JSON 格式**：机器可查，便于后续分析
- **TTL = session**：随 session 结束自动清理
- **按 session_id 可检索**：通过 NimFS tags 索引

---

## 2. 方案选择

### 方案对比

| 方案 | Hook 点 | 侵入度 | 优缺点 |
|------|---------|--------|--------|
| **A. 在 `stream_chat` 末尾 hook** | `session_v2.py` `_save_conversation_to_storage` 之后 | 低 | ✅ 复用已有消息提取逻辑，不改 agentos.py |
| B. 在 `AgentOS.chat()` 返回后 hook | `agentos.py` chat() 末尾 | 中 | ❌ AgentOS 不应感知 NimFS session 语义 |
| C. 新建 `ConversationTracer` 类 | 独立模块 | 高 | ✅ 清晰，❌ 过度设计 |

**选择方案 A**：最小侵入，在 `stream_chat` 中已有 `_save_conversation_to_storage` 调用点的正下方添加 NimFS 写入调用。

### Hook 点精确位置

```
session_v2.py stream_chat()
  └─ await agent_os.chat(...)           # 执行完成
  └─ await _save_conversation_to_storage(...)   # 已有：写 SQLite  ← 在这之后
  └─ await _save_turn_to_nimfs(...)     # 新增：写 NimFS artifact  ← 新增
```

同时在 **CancelledError 分支**（用户中断）也写入，保证部分对话不丢失。

---

## 3. 核心数据结构

### 3.1 每轮对话 Turn

```python
@dataclass
class ConversationTurn:
    turn_index: int           # 第几轮（从 1 开始）
    session_id: str
    timestamp: str            # ISO 8601 UTC
    user_message: str         # 用户输入（文本，多模态标注 [image]）
    assistant_reply: str      # AI 最终回复文本
    tool_calls: List[Dict]    # 本轮调用的工具列表 [{name, args_preview, status}]
    duration_ms: int          # 本轮耗时
    status: str               # "OK" | "CANCELLED" | "ERROR"
```

### 3.2 NimFS Artifact 结构

每个 session 维护 **一个** artifact，每轮追加（通过 overwrite 更新）：

```
artifact_id: conv-{session_id}
type: text (Markdown + JSON 分区)
ttl: session
tags: "conversation,session-{session_id},orchestrator"
```

**Artifact 内容格式（Markdown + JSON 双区）**：

```markdown
# Conversation Log: {session_id}
> Created: 2025-07-01T10:00:00Z | Turns: 3

---

## Turn 1 — 2025-07-01T10:00:05Z

**User:** 帮我分析这个文件的性能问题

**Assistant:** 我来分析一下...（完整回复）

**Tools Used:** Read(file.py) → OK, Bash(perf stat) → OK

**Duration:** 4,231ms | **Status:** OK

---

## Turn 2 — 2025-07-01T10:02:11Z
...

---

<!-- JSON_DATA
{
  "session_id": "...",
  "turns": [
    {
      "turn_index": 1,
      "timestamp": "...",
      "user_message": "...",
      "assistant_reply": "...",
      "tool_calls": [...],
      "duration_ms": 4231,
      "status": "OK"
    }
  ]
}
-->
```

> **设计要点**：JSON 嵌在 HTML 注释中，Markdown 渲染时不显示，机器解析时可直接提取。

---

## 4. 关键代码改动点

### 4.1 新增辅助模块：`src/nimbus/server/conv_nimfs.py`（新文件）

职责：封装对话写入 NimFS 的全部逻辑，不污染 session_v2.py。

```python
# conv_nimfs.py 核心接口

async def save_turn_to_nimfs(
    session_id: str,
    turn_index: int,
    user_message: str | list,
    assistant_reply: str,
    tool_calls: list[dict],
    duration_ms: int,
    status: str,
    workspace: Path,
) -> str:
    """
    将一轮对话追加写入 NimFS artifact。
    返回 artifact ref (nimfs://artifact/...)。
    
    策略：
    1. 尝试读取已有 artifact（通过 tag 查找 conv-{session_id}）
    2. 追加新 Turn 到 Markdown 和 JSON 区
    3. 覆盖写入（NimFSManager.write_artifact 支持 overwrite）
    """
    ...

def _extract_tool_calls_from_messages(messages: list) -> list[dict]:
    """从 MMU frame.messages 中提取本轮工具调用摘要。"""
    ...

def _build_markdown(session_id: str, turns: list[dict]) -> str:
    """生成 Markdown + JSON 双区格式内容。"""
    ...
```

### 4.2 修改 `session_v2.py`

#### 改动 1：`stream_chat()` 正常完成分支

**位置**：约第 580 行，`await self._save_conversation_to_storage(...)` 之后

```python
# 现有代码（约 580 行）
await self._save_conversation_to_storage(session_id, agent_os, message, _msg_watermark)

# 新增：写入 NimFS 对话记录
await self._save_turn_to_nimfs(
    session_id=session_id,
    agent_os=agent_os,
    user_message=message,
    result=result,
    turn_start_time=_turn_start_time,   # 需在 chat() 调用前记录
    status="OK",
)
```

#### 改动 2：`stream_chat()` CancelledError 分支

**位置**：约第 545 行，`await self._save_conversation_to_storage(...)` 之后

```python
# 现有代码（约 545 行）
await self._save_conversation_to_storage(session_id, agent_os, message, _msg_watermark)

# 新增
await self._save_turn_to_nimfs(
    session_id=session_id,
    agent_os=agent_os,
    user_message=message,
    result=None,
    turn_start_time=_turn_start_time,
    status="CANCELLED",
)
```

#### 改动 3：`stream_chat()` 顶部，记录轮次开始时间

**位置**：约第 480 行，`result = await agent_os.chat(...)` 之前

```python
import time
_turn_start_time = time.monotonic()   # 新增：记录本轮开始时间
_turn_index = await self._get_next_turn_index(session_id)  # 新增：获取轮次号
result = await agent_os.chat(message, session_id=session_id)
```

#### 改动 4：新增 `_save_turn_to_nimfs()` 方法

在 `SessionManagerV2` 中新增私有方法：

```python
async def _save_turn_to_nimfs(
    self,
    session_id: str,
    agent_os: AgentOS,
    user_message: str | list,
    result: Optional[ToolResult],
    turn_start_time: float,
    status: str,
) -> None:
    """将本轮对话写入 NimFS artifact（fire-and-forget，失败不影响主流程）。"""
    try:
        from .conv_nimfs import save_turn_to_nimfs, _extract_tool_calls_from_messages
        import time

        # 提取 assistant 回复
        assistant_reply = ""
        if result and result.output:
            assistant_reply = result.output

        # 提取工具调用摘要（从 MMU messages 中）
        tool_calls = []
        process = agent_os.get_process(session_id)
        if process and process.mmu and process.mmu._stack:
            tool_calls = _extract_tool_calls_from_messages(
                process.mmu.current_frame.messages
            )

        duration_ms = int((time.monotonic() - turn_start_time) * 1000)

        # 获取 workspace
        workspace = Path(
            getattr(process.mmu, "nimfs_workspace", ".") if process else "."
        )

        await save_turn_to_nimfs(
            session_id=session_id,
            turn_index=self._turn_counters.get(session_id, 1),
            user_message=user_message,
            assistant_reply=assistant_reply,
            tool_calls=tool_calls,
            duration_ms=duration_ms,
            status=status,
            workspace=workspace,
        )
        # 递增轮次计数
        self._turn_counters[session_id] = self._turn_counters.get(session_id, 1) + 1

    except Exception as e:
        logger.warning(f"[NimFS] Failed to save conversation turn: {e}")
        # 静默失败，不影响主流程
```

#### 改动 5：`SessionManagerV2.__init__()` 新增轮次计数器

**位置**：`__init__` 方法内

```python
self._turn_counters: Dict[str, int] = {}  # session_id -> 当前轮次号
```

#### 改动 6：`end_session()` 清理轮次计数器

```python
async def end_session(self, session_id: str):
    # ... 现有逻辑 ...
    self._turn_counters.pop(session_id, None)  # 新增
```

### 4.3 `conv_nimfs.py` 实现要点

```python
# src/nimbus/server/conv_nimfs.py

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import ArtifactTTL

CONV_TAG_PREFIX = "conversation,orchestrator,session-"

async def save_turn_to_nimfs(
    session_id: str,
    turn_index: int,
    user_message,
    assistant_reply: str,
    tool_calls: list,
    duration_ms: int,
    status: str,
    workspace: Path,
) -> Optional[str]:
    nimfs = NimFSManager(workspace_path=workspace)

    # 1. 查找已有 artifact
    existing_turns = []
    existing_ref = None
    tag = f"session-{session_id}"
    try:
        manifests = nimfs.list_artifacts(tags=[tag])  # 按 tag 过滤
        if manifests:
            existing_ref = manifests[0].artifact_id
            content = nimfs.read_artifact(existing_ref)
            existing_turns = _parse_json_from_content(content)
    except Exception:
        pass

    # 2. 构建新 Turn
    user_text = _normalize_message(user_message)
    new_turn = {
        "turn_index": turn_index,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_message": user_text,
        "assistant_reply": assistant_reply,
        "tool_calls": tool_calls,
        "duration_ms": duration_ms,
        "status": status,
    }
    all_turns = existing_turns + [new_turn]

    # 3. 生成 Markdown + JSON 内容
    content = _build_markdown(session_id, all_turns)

    # 4. 写入 NimFS（overwrite）
    artifact_id = f"conv-{session_id}"
    ref = nimfs.write_artifact(
        content=content,
        artifact_id=artifact_id,     # 固定 ID，每次覆盖
        type="text",
        ttl=ArtifactTTL.SESSION,
        tags=f"{CONV_TAG_PREFIX}{session_id}",
        summary=f"Conversation log for session {session_id} ({len(all_turns)} turns)",
    )
    return ref
```

> **注意**：`NimFSManager.write_artifact()` 当前是否支持 `artifact_id` 固定覆盖，需确认接口。
> 若不支持固定 ID 覆盖，则改为：先 `delete_artifact(existing_ref)`，再 `write_artifact()`。

---

## 5. NimFSManager 接口确认

查看 `manager.py` 后需确认以下两点：

| 需求 | 当前接口 | 是否需要改动 |
|------|---------|-------------|
| 按 tag 查找 artifact | `list_artifacts()` 是否支持 tag 过滤？ | 可能需要添加 `tags` 参数 |
| 固定 artifact_id 覆盖写入 | `write_artifact()` 是否接受 `artifact_id` 参数？ | 可能需要添加 overwrite 模式 |

若两个接口都不支持，**备选方案**：

```python
# 用 session 级别的 metadata 文件记录 artifact ref
# 路径：~/.nimbus/fs/projects/{project}/sessions/{session_id}/conv_ref.txt
# 每次写新 artifact，读旧 ref 删除旧 artifact
```

---

## 6. 工具调用摘要提取逻辑

```python
def _extract_tool_calls_from_messages(messages: list) -> list[dict]:
    """
    从 MMU frame.messages 中提取工具调用摘要。
    只提取 role=assistant（含 tool_calls）和 role=tool（结果）的配对。
    """
    tool_summaries = []
    # 遍历 assistant messages 中的 tool_calls
    for msg in messages:
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                name = tc.get("function", {}).get("name", "unknown")
                args_raw = tc.get("function", {}).get("arguments", "{}")
                try:
                    args = json.loads(args_raw)
                    # 只保留前 100 字的参数预览
                    args_preview = {k: str(v)[:100] for k, v in args.items()}
                except Exception:
                    args_preview = {}
                tool_summaries.append({
                    "name": name,
                    "args_preview": args_preview,
                    "status": "OK",  # 默认，后续可从 tool 消息中更新
                })
    return tool_summaries
```

---

## 7. Markdown 格式生成

```python
def _build_markdown(session_id: str, turns: list[dict]) -> str:
    lines = [
        f"# Conversation Log: {session_id}",
        f"> Turns: {len(turns)} | Last updated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for turn in turns:
        lines += [
            "---",
            f"## Turn {turn['turn_index']} — {turn['timestamp']}",
            "",
            f"**User:** {turn['user_message']}",
            "",
            f"**Assistant:** {turn['assistant_reply'] or '_(no output)_'}",
            "",
        ]
        if turn["tool_calls"]:
            tools_str = ", ".join(
                f"{t['name']}({_fmt_args(t['args_preview'])}) → {t['status']}"
                for t in turn["tool_calls"]
            )
            lines.append(f"**Tools Used:** {tools_str}")
            lines.append("")
        lines.append(
            f"**Duration:** {turn['duration_ms']:,}ms | **Status:** {turn['status']}"
        )
        lines.append("")

    # JSON 区（机器可解析）
    lines += [
        "---",
        "",
        "<!-- JSON_DATA",
        json.dumps({"session_id": session_id, "turns": turns}, ensure_ascii=False, indent=2),
        "-->",
    ]
    return "\n".join(lines)
```

---

## 8. 数据流全景

```
用户发消息 POST /sessions/{sid}/chat
    │
    ▼
SessionManagerV2.stream_chat()
    ├─ _turn_start_time = time.monotonic()          # [新增] 记录开始时间
    ├─ agent_os.chat(message, session_id)           # 执行（VCPU 内部 trace 已写 NimFS）
    ├─ _save_conversation_to_storage()              # 现有：写 SQLite
    └─ _save_turn_to_nimfs()                        # [新增] 写 NimFS artifact
            │
            ▼
        conv_nimfs.save_turn_to_nimfs()
            ├─ NimFSManager.list_artifacts(tag=session-{sid})  # 查找已有
            ├─ 追加新 Turn 到 turns[]
            ├─ _build_markdown(session_id, turns)              # 生成内容
            └─ NimFSManager.write_artifact(artifact_id=conv-{sid}, ttl=SESSION)
```

---

## 9. 检索方式

写入后，通过以下方式检索：

```python
# 按 session_id 检索对话记录
nimfs = NimFSManager(workspace_path=workspace)
manifests = nimfs.list_artifacts(tags=[f"session-{session_id}"])
if manifests:
    content = nimfs.read_artifact(manifests[0].artifact_id)
    # content 包含完整 Markdown + JSON 双区
```

或通过 NimFS Memory 搜索（若将 session 摘要写入 memory）：
```python
NimFSSearchMemory(query=session_id, category="events")
```

---

## 10. 实现优先级与 TODO

| 优先级 | 任务 | 文件 | 备注 |
|--------|------|------|------|
| P0 | 确认 `NimFSManager.write_artifact()` 是否支持固定 ID 覆盖 | `manager.py` | 决定备选方案选择 |
| P0 | 确认 `list_artifacts()` 是否支持 tag 过滤 | `manager.py` | 若不支持需添加 |
| P1 | 实现 `conv_nimfs.py` | 新文件 | 核心逻辑 |
| P1 | 修改 `session_v2.py`：添加 `_save_turn_to_nimfs()` + hook 点 | `session_v2.py` | 5 处改动 |
| P2 | 单元测试：mock NimFSManager，验证 artifact 格式 | `tests/server/test_conv_nimfs.py` | |
| P2 | 集成测试：跑完整 chat，验证 NimFS artifact 可读 | `tests/integration/` | |

---

## 11. 设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 每轮写一次还是 session 结束写一次？ | **每轮写**（覆盖更新） | 防止 session 异常退出导致对话丢失 |
| 每轮新建 artifact 还是同一个 artifact 追加？ | **同一个 artifact 覆盖** | 避免 artifact 数量爆炸，检索简单 |
| 失败处理 | **静默失败** | NimFS 写入是可选的观测能力，不应影响主流程 |
| TTL | **SESSION** | 随 session 结束自动 GC，不占用长期存储 |
| 格式 | **Markdown + JSON 双区** | 人类可读 + 机器可解析，一文两用 |
| 工具调用详细度 | **摘要（名称 + args 前 100 字）** | 完整 tool 内容已在 VCPU trace 中，避免重复 |
