# NimFS Phase 0 实现方案

> **文档类型**：实现规范 (Implementation Spec)  
> **状态**：v1.0 Ready for Implementation  
> **关联文档**：`nimfs-design-v2.md`、`nimfs-as-agent-ipc.md`

---

## 1. 文件结构规划

### 新建文件

```
src/nimbus/core/nimfs/
├── __init__.py          # 导出 NimFSManager、核心模型、异常类
├── models.py            # 数据模型：ArtifactManifest、MemoryEntry、枚举
├── project_id.py        # project_id 生成、路径工具函数
├── manager.py           # NimFSManager 核心类（Artifact + Memory API）
└── gc.py                # TTL GC 机制

src/nimbus/tools/
└── nimfs_tools.py       # Agent 可调用的 @tool 工具函数（6个）
```

### 修改文件

```
src/nimbus/tools/__init__.py     ← 注册 6 个 NimFS 工具函数
src/nimbus/core/protocol.py     ← [Phase 1 TODO] ToolResult 扩展 artifact_ref 字段
```

### 实现顺序（依赖关系）

```
models.py          ← 无依赖，最先实现
    ↓
project_id.py      ← 依赖 models（Path 工具）
    ↓
manager.py         ← 依赖 models + project_id
    ↓
gc.py              ← 依赖 manager + models
    ↓
nimfs_tools.py     ← 依赖 manager（工具层包装）
    ↓
tools/__init__.py  ← 注册入口，最后修改
```

---

## 2. 数据模型（models.py）

### 枚举定义

```python
from enum import Enum

class ArtifactTTL(str, Enum):
    TASK      = "task"       # 任务完成后 30 分钟自动 GC
    SESSION   = "session"    # session 结束时清理
    PROJECT   = "project"    # 手动触发或 defrag()
    PERMANENT = "permanent"  # 永不自动 GC（升级为 memory）

class ArtifactStatus(str, Enum):
    PENDING   = "pending"    # 写入中（原子性保障）
    COMMITTED = "committed"  # 已提交，可被读取
    EXPIRED   = "expired"    # 已过期，等待 GC

class MemoryCategory(str, Enum):
    PROFILE      = "profile"      # 智能体自身角色定义
    PREFERENCES  = "preferences"  # 用户偏好、约束条件
    ENTITIES     = "entities"     # 核心对象、组件关联
    EVENTS       = "events"       # 关键状态变更、里程碑
    CASES        = "cases"        # 成功/失败经验案例
    PATTERNS     = "patterns"     # 抽象架构模式、技术规范
```

### ArtifactManifest

```python
@dataclass
class ArtifactManifest:
    artifact_id: str              # 唯一 ID：{task_id}-{uuid[:8]}
    task_id: str                  # 所属任务 ID
    producer: str                 # 生产者 agent role（如 "implement-agent"）
    type: str                     # "code" | "report" | "diff" | "json" | "text"
    filename: str                 # 实际文件名（如 "content.py"）
    size_bytes: int               # 内容大小
    created_at: str               # ISO8601 时间戳
    ttl: ArtifactTTL              # 生命周期级别
    status: ArtifactStatus        # 写入状态
    summary: str                  # 人类可读摘要，< 200 chars
    tags: List[str]               # 标签列表
    supersedes: Optional[str] = None  # 被替代的旧 artifact_id
```

### MemoryEntry

```python
@dataclass
class MemoryEntry:
    memory_id: str                # 唯一 ID：{category}-{uuid[:8]}
    category: MemoryCategory      # 记忆分类
    title: str                    # 标题（用于关键词搜索）
    created_at: str               # ISO8601
    updated_at: str               # ISO8601
    confidence: float             # 置信度 0.0 ~ 1.0
    source: str                   # 来源 agent role
    valid_from: str               # ISO8601，生效时间
    valid_until: Optional[str] = None  # None = 永不过期
    tags: List[str] = field(default_factory=list)
```

### 异常类

```python
class ArtifactExpiredError(Exception):
    """nimfs:// 引用对应的 artifact 已过期或被 GC"""
    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        super().__init__(f"Artifact '{artifact_id}' has expired or been GC'd")

class ArtifactNotFoundError(Exception):
    """nimfs:// 引用不存在"""

class NimFSError(Exception):
    """NimFS 通用异常基类"""
```

---

## 3. project_id.py：路径工具

### 函数定义

```python
from pathlib import Path

def get_project_id(workspace_path: str) -> str:
    """
    将工作区绝对路径转换为项目目录名，与 Claude 保持一致。

    规则：去掉开头的 /，将所有 / 替换为 -

    示例：
        /Users/wangqing/sourcecode/nimbus
        → Users-wangqing-sourcecode-nimbus

        /Users/wangqing/project
        → Users-wangqing-project
    """
    path = Path(workspace_path).resolve()
    return str(path).lstrip("/").replace("/", "-")


def get_nimfs_root() -> Path:
    """
    返回全局 NimFS 根目录：~/.nimbus/fs/
    自动创建目录（包含 global/ 和 projects/ 子目录）
    """
    root = Path.home() / ".nimbus" / "fs"
    root.mkdir(parents=True, exist_ok=True)
    (root / "global").mkdir(exist_ok=True)
    (root / "projects").mkdir(exist_ok=True)
    return root


def get_global_root() -> Path:
    """返回全局 memory 根目录：~/.nimbus/fs/global/"""
    root = get_nimfs_root() / "global"
    for category in ["profile", "preferences"]:
        (root / category).mkdir(exist_ok=True)
    return root


def get_project_root(workspace_path: str) -> Path:
    """
    返回项目级 NimFS 根目录，自动初始化完整目录结构。

    结构：
    ~/.nimbus/fs/projects/{project_id}/
    ├── memory/
    │   ├── profile/
    │   ├── preferences/
    │   ├── entities/
    │   ├── events/
    │   ├── cases/
    │   └── patterns/
    └── artifacts/
        └── index.json
    """
    project_id = get_project_id(workspace_path)
    project_root = get_nimfs_root() / "projects" / project_id

    # 初始化 memory 分区
    memory_root = project_root / "memory"
    for category in ["profile", "preferences", "entities", "events", "cases", "patterns"]:
        (memory_root / category).mkdir(parents=True, exist_ok=True)

    # 初始化 artifacts 分区
    artifacts_root = project_root / "artifacts"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    index_file = artifacts_root / "index.json"
    if not index_file.exists():
        index_file.write_text("[]")

    return project_root
```

---

## 4. NimFSManager 核心类（manager.py）

### 类结构

```python
class NimFSManager:
    """
    NimFS 核心协调器。
    
    职责：
    - 管理 artifacts/（Agent IPC 产物，短生命周期）
    - 管理 memory/（长期记忆，6分类 + L0/L1/L2）
    - 解析和生成 nimfs:// 引用
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.project_id = get_project_id(workspace_path)
        self.project_root = get_project_root(workspace_path)
        self.artifacts_root = self.project_root / "artifacts"
        self.memory_root = self.project_root / "memory"
        self.global_root = get_global_root()
```

### Artifact API

#### write_artifact()

```python
def write_artifact(
    self,
    content: str,
    task_id: str,
    producer: str,
    artifact_type: str = "text",
    ttl: ArtifactTTL = ArtifactTTL.SESSION,
    summary: str = "",
    tags: List[str] = None,
) -> str:
    """
    写入 Agent 产物，返回 nimfs:// 引用。

    实现逻辑：
    1. 生成 artifact_id = f"{task_id}-{uuid4().hex[:8]}"
    2. 建立目录 artifacts/{task_id}/
    3. 先写 manifest.json（status=PENDING）—— 原子性保障起点
    4. 写入 content 文件（content.{ext}，ext 由 type 决定）
    5. 更新 manifest.json（status=COMMITTED）
    6. 更新 artifacts/index.json（追加记录）
    7. 返回 "nimfs://artifact/{artifact_id}"

    文件扩展名映射：
        "code"   → .py（默认，实际由 tags 或内容判断）
        "report" → .md
        "diff"   → .diff
        "json"   → .json
        "text"   → .txt
    """
```

#### read_artifact()

```python
def read_artifact(self, ref: str) -> str:
    """
    根据 nimfs:// 引用读取产物内容。

    实现逻辑：
    1. 解析 ref：支持两种格式
       - "nimfs://artifact/{artifact_id}"
       - 直接传 artifact_id
    2. 从 index.json 查找 task_id（或遍历 artifacts/ 目录）
    3. 读取 manifest.json，检查 status
       - PENDING  → 抛出 NimFSError（写入未完成）
       - EXPIRED  → 抛出 ArtifactExpiredError
       - COMMITTED → 继续
    4. 读取 content 文件并返回
    """
```

#### list_artifacts()

```python
def list_artifacts(self, task_id: str = None) -> List[ArtifactManifest]:
    """
    列出所有 COMMITTED 状态的产物。

    实现逻辑：
    1. 读取 artifacts/index.json
    2. 若 task_id 不为空，过滤匹配项
    3. 过滤掉 status != COMMITTED 的条目
    4. 返回 ArtifactManifest 列表
    """
```

### Memory API

#### write_memory()

```python
def write_memory(
    self,
    category: MemoryCategory,
    title: str,
    content: str,
    summary: str = "",
    confidence: float = 1.0,
    source: str = "agent",
    tags: List[str] = None,
    scope: str = "project",  # "project" | "global"
) -> str:
    """
    写入长期记忆，返回 memory_id。

    目录结构（以 entities 为例）：
    memory/entities/{memory_id}/
    ├── meta.json       ← MemoryEntry 元数据
    ├── l0.abstract     ← L0：纯文本极简摘要（< 100 tokens）
                           Phase 0：直接截取 summary 字段
                           Phase 1：LLM 异步蒸馏
    ├── l1.overview.md  ← L1：结构化 Markdown（背景+结论）
    └── l2.content.md   ← L2：完整原始内容

    实现逻辑：
    1. 生成 memory_id = f"{category.value}-{uuid4().hex[:8]}"
    2. 建立目录 memory/{category}/{memory_id}/
    3. 写入 l2.content.md（完整内容）
    4. 写入 l1.overview.md（标题 + 内容前 500 字）
    5. 写入 l0.abstract（summary 或内容前 100 字）
    6. 写入 meta.json（MemoryEntry）
    7. 返回 memory_id
    """
```

#### read_memory()

```python
def read_memory(self, memory_id: str, layer: int = 1) -> str:
    """
    读取指定层级的记忆内容。

    layer=0 → l0.abstract（最小 Token 成本）
    layer=1 → l1.overview.md（默认，平衡详细度）
    layer=2 → l2.content.md（完整内容，按需加载）
    """
```

#### search_memory()

```python
def search_memory(
    self,
    query: str,
    category: MemoryCategory = None,
    top_k: int = 5,
    min_confidence: float = 0.0,
    scope: str = "project",  # "project" | "global" | "all"
) -> List[MemoryEntry]:
    """
    关键词搜索记忆（Phase 0 基于文件名/标题匹配）。

    实现逻辑：
    1. 确定搜索范围（project / global / all）
    2. 遍历对应 memory/ 目录下所有 meta.json
    3. 若 category 不为空，只搜索该分类
    4. 对 title + tags 做简单字符串包含匹配（不区分大小写）
    5. 过滤 confidence < min_confidence 的条目
    6. 返回前 top_k 个（按 updated_at 降序）

    Phase 1 升级：集成向量相似度搜索
    """
```

#### load_context()

```python
def load_context(self, current_goal: str, max_tokens: int = 2000) -> str:
    """
    为 Nimbus Anchor 组装最优 Context 注入包。

    实现逻辑：
    1. 优先加载 global/profile 和 global/preferences 的 L0 摘要
    2. 对 current_goal 做关键词提取（简单分词）
    3. 搜索 project memory 中相关条目（top 5）
    4. 按 L0 摘要拼接，累计 Token 不超过 max_tokens
    5. 格式化为 Markdown 输出（便于注入 Anchor）

    输出格式：
    ## NimFS Context
    ### Profile
    - {l0 摘要}
    ### Preferences  
    - {l0 摘要}
    ### Relevant Knowledge
    - [{category}] {title}: {l0 摘要}
    """
```

---

## 5. gc.py：TTL GC 机制

```python
class NimFSGC:
    """
    NimFS 垃圾回收器。
    
    策略：
    - TASK 级：task 完成 30 分钟后标记 EXPIRED，下次 gc() 时删除
    - SESSION 级：session 结束时统一清理（通过 session_id 标记）
    - PROJECT 级：仅 defrag() 时处理
    - PERMANENT：永不自动 GC
    """

    def gc_artifacts(
        self,
        workspace_path: str,
        ttl_level: ArtifactTTL = ArtifactTTL.TASK,
        dry_run: bool = False,
    ) -> int:
        """
        清理指定 TTL 级别以下的过期 artifacts。
        
        实现逻辑：
        1. 读取 artifacts/index.json
        2. 过滤 ttl <= ttl_level 且 created_at 超过阈值的条目
        3. 将 manifest.json 的 status 更新为 EXPIRED
        4. 若非 dry_run，删除 content 文件（保留 manifest 作为墓碑）
        5. 更新 index.json
        6. 返回清理数量
        
        TTL 阈值：
        - TASK：created_at + 30 分钟
        - SESSION：session 结束时（通过外部调用触发）
        """

    def defrag(self, workspace_path: str) -> dict:
        """
        碎片整理：
        1. 删除所有 EXPIRED 状态的墓碑条目
        2. 合并 title 相同的 memory 条目（保留 confidence 最高的）
        3. 返回清理统计
        """
```

---

## 6. nimfs_tools.py：Agent 工具层

### 工具列表

| 工具名 | 功能 | 参数 |
|--------|------|------|
| `NimFSWriteArtifact` | 写入 IPC 产物 | content, task_id, summary, ttl, type |
| `NimFSReadArtifact`  | 读取产物引用 | ref |
| `NimFSListArtifacts` | 列出产物清单 | task_id（可选） |
| `NimFSWriteMemory`   | 写入长期记忆 | category, title, content, summary |
| `NimFSSearchMemory`  | 搜索记忆 | query, category（可选） |
| `NimFSLoadContext`   | 加载 Anchor 上下文 | goal |

### 实现模式

```python
from nimbus.tools.base import tool
from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import MemoryCategory, ArtifactTTL, ArtifactExpiredError

def _get_manager(ctx: dict) -> NimFSManager:
    """从工具上下文中获取 workspace_path，构造 NimFSManager"""
    workspace = ctx.get("workspace", str(Path.cwd()))
    return NimFSManager(workspace)


@tool(
    name="NimFSWriteArtifact",
    description=(
        "将大型产物（代码、报告、diff 等）写入 NimFS 共享磁盘，返回 nimfs:// 引用。"
        "用于 Agent 间传递大文件，避免上下文截断。"
    ),
    parameters=[
        ToolParameter("content",  "string", "产物内容（无大小限制）",       required=True),
        ToolParameter("task_id",  "string", "所属任务 ID",                  required=True),
        ToolParameter("summary",  "string", "产物摘要（< 200 字）",         required=True),
        ToolParameter("ttl",      "string", "生命周期: task/session/project/permanent", required=False),
        ToolParameter("type",     "string", "产物类型: code/report/diff/json/text",     required=False),
    ],
)
async def nimfs_write_artifact(content: str, task_id: str, summary: str,
                                ttl: str = "session", type: str = "text", **ctx) -> str:
    manager = _get_manager(ctx)
    ref = manager.write_artifact(
        content=content, task_id=task_id, producer=ctx.get("agent_role", "agent"),
        artifact_type=type, ttl=ArtifactTTL(ttl), summary=summary,
    )
    return f"✅ 产物已写入 NimFS\n引用：{ref}\n摘要：{summary}"


@tool(
    name="NimFSReadArtifact",
    description="通过 nimfs:// 引用读取产物完整内容。",
    parameters=[
        ToolParameter("ref", "string", "nimfs://artifact/{id} 格式的引用", required=True),
    ],
)
async def nimfs_read_artifact(ref: str, **ctx) -> str:
    manager = _get_manager(ctx)
    try:
        return manager.read_artifact(ref)
    except ArtifactExpiredError as e:
        return f"❌ ArtifactExpiredError: {e}"


# ... 其余 4 个工具类似模式
```

### 注册到 tools/__init__.py

```python
# 在现有导入后追加：
from nimbus.tools.nimfs_tools import (
    nimfs_write_artifact,
    nimfs_read_artifact,
    nimfs_list_artifacts,
    nimfs_write_memory,
    nimfs_search_memory,
    nimfs_load_context,
)
```

---

## 7. 对接说明

### 从 **ctx 获取 workspace

AgentOS 在执行工具时通过 `**kwargs` 注入以下环境变量：
- `ctx["workspace"]`：当前工作区绝对路径（优先使用）
- `ctx["agent_role"]`：Agent 角色名（用于 manifest.producer）
- `ctx["session_id"]`：会话 ID（用于 SESSION 级 GC）

若 `workspace` 不存在于 ctx，回退使用 `Path.cwd()`。

### Phase 1 TODO（标注位置）

在 `src/nimbus/core/protocol.py` 的 `ToolResult` dataclass 中：

```python
@dataclass
class ToolResult:
    status: ResultStatus
    output: str
    # ... 现有字段 ...
    
    # TODO(Phase 1 - NimFS IPC): 当 output 超过 8K 时，自动 offload 到 NimFS
    # artifact_ref: Optional[str] = None  # nimfs://artifact/{task_id}
```

在 `src/nimbus/orchestration/context_protocol.py` 的 `GoalDocument.render()` 中：

```python
# TODO(Phase 1 - NimFS IPC): 支持 nimfs:// 引用展开
# for ref in extract_nimfs_refs(self.context):
#     self.context = self.context.replace(ref, nimfs.read_artifact(ref))
```

---

## 8. 测试计划（Phase 0）

### 最小测试用例清单

```python
# tests/test_nimfs.py

# 1. project_id 转换正确性
def test_get_project_id():
    assert get_project_id("/Users/wangqing/sourcecode/nimbus") == \
           "Users-wangqing-sourcecode-nimbus"
    assert get_project_id("/home/user/project") == "home-user-project"

# 2. 目录初始化
def test_project_root_init(tmp_path):
    # get_project_root 应创建完整目录树
    root = get_project_root(str(tmp_path / "workspace"))
    assert (root / "memory" / "entities").exists()
    assert (root / "artifacts" / "index.json").exists()

# 3. Artifact 写读一致性
def test_write_read_artifact(tmp_workspace):
    manager = NimFSManager(tmp_workspace)
    content = "print('hello nimfs')" * 1000  # 大内容
    ref = manager.write_artifact(content, task_id="task-1", producer="test")
    assert ref.startswith("nimfs://artifact/")
    result = manager.read_artifact(ref)
    assert result == content

# 4. 引用解析容错
def test_read_artifact_expired(tmp_workspace):
    manager = NimFSManager(tmp_workspace)
    with pytest.raises(ArtifactExpiredError):
        manager.read_artifact("nimfs://artifact/nonexistent-id")

# 5. Memory 写读分层
def test_write_read_memory(tmp_workspace):
    manager = NimFSManager(tmp_workspace)
    mid = manager.write_memory(
        category=MemoryCategory.ENTITIES,
        title="NimFSManager 类",
        content="完整的类文档..." * 100,
        summary="NimFS 核心协调器，管理 artifacts 和 memory",
    )
    l0 = manager.read_memory(mid, layer=0)
    l1 = manager.read_memory(mid, layer=1)
    l2 = manager.read_memory(mid, layer=2)
    assert len(l0) < len(l1) < len(l2)

# 6. 关键词搜索
def test_search_memory(tmp_workspace):
    manager = NimFSManager(tmp_workspace)
    manager.write_memory(MemoryCategory.PATTERNS, "异步蒸馏模式", "...")
    results = manager.search_memory("蒸馏")
    assert len(results) >= 1
    assert results[0].title == "异步蒸馏模式"

# 7. GC 清理
def test_gc_task_artifacts(tmp_workspace):
    manager = NimFSManager(tmp_workspace)
    gc = NimFSGC()
    ref = manager.write_artifact("data", task_id="old-task", producer="test",
                                  ttl=ArtifactTTL.TASK)
    # 模拟时间过期
    # ... 修改 manifest 的 created_at 为 31 分钟前 ...
    cleaned = gc.gc_artifacts(tmp_workspace, ttl_level=ArtifactTTL.TASK)
    assert cleaned == 1
```

---

## 9. 实现检查清单

实现完成后，逐一验证：

- [ ] `get_project_id()` 与 Claude 路径规则一致
- [ ] `~/.nimbus/fs/` 目录结构正确初始化
- [ ] `write_artifact()` 写入后 manifest.json status=COMMITTED
- [ ] `read_artifact()` 能完整读取大内容（> 16K）
- [ ] `ArtifactExpiredError` 在引用不存在时正确抛出
- [ ] `write_memory()` 生成 l0/l1/l2 三个文件
- [ ] `read_memory(layer=0/1/2)` 各层内容长度递增
- [ ] `search_memory()` 关键词匹配正确
- [ ] `load_context()` 输出不超过 max_tokens
- [ ] 6 个 `@tool` 函数正确注册到 tools/__init__.py
- [ ] `tests/test_nimfs.py` 全部通过

---

*NimFS Implementation Plan v1.0 | 关联：nimfs-design-v2.md、nimfs-as-agent-ipc.md*
