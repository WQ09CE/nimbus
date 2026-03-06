# Tool Failover Design (工具执行层智能容错)

> Status: Proposed
> Author: @意 (Architect)
> Date: 2025-01-25

## Summary

设计 Nimbus 工具执行层的智能容错机制，通过分层架构实现：
1. **路径解析层 (SmartPathResolver)** - 模糊匹配、候选推荐
2. **执行中间件层 (ToolRetryMiddleware)** - 工具级重试、错误增强
3. **上下文预注入层** - 文件树注入、LLM 辅助修复

## Design

### 架构概述

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           User Request                                   │
│                     "帮我看下 utils 文件"                                │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 1: Context Injection (上下文预注入)                               │
│  ┌──────────────────┐  ┌──────────────────┐                             │
│  │  FileTreeCache   │  │  Memory.pin()    │                             │
│  │  (目录结构缓存)   │  │  (注入 pinned)   │                             │
│  └──────────────────┘  └──────────────────┘                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 2: Planner/LLM (规划层)                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  LLM sees file tree in context → generates correct path          │   │
│  │  Read(file_path="src/utils.py")                                  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 3: Tool Middleware (工具中间件)                                   │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  ToolRetryMiddleware                                             │   │
│  │  - Pre-execution: SmartPathResolver.resolve()                    │   │
│  │  - Post-failure: Retry with candidates / Ask user                │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 4: Tool Execution (工具执行)                                      │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  ToolRegistry.execute("Read", params)                            │   │
│  │  → read_file(file_path, workspace)                               │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 5: DAG Retry (DAG 级重试) - 已存在                                │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  on_failure → fix_task → retry_target                            │   │
│  │  (用于复杂的 fix-and-retry 循环)                                  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 核心组件

#### 1. SmartPathResolver (智能路径解析器)

**职责**: 将模糊路径解析为候选路径列表

**位置**: `src/nimbus/tools/resolver.py`

```python
@dataclass
class PathCandidate:
    """路径候选项"""
    path: Path           # 解析后的绝对路径
    score: float         # 匹配置信度 (0.0-1.0)
    reason: str          # 匹配原因
    original: str        # 原始输入

class SmartPathResolver:
    """智能路径解析器

    策略优先级:
    1. 精确匹配 (exact) - 路径存在
    2. 后缀补全 (suffix) - 补 .py/.ts 等
    3. 模糊搜索 (fuzzy) - glob + 编辑距离
    4. 最近文件 (recent) - 最近修改的同名文件
    """

    def __init__(
        self,
        workspace: Path,
        file_tree_cache: Optional["FileTreeCache"] = None,
        suffix_priority: List[str] = [".py", ".ts", ".js", ".go", ".rs"],
        fuzzy_threshold: float = 0.6,
    ):
        self.workspace = workspace
        self.cache = file_tree_cache
        self.suffix_priority = suffix_priority
        self.fuzzy_threshold = fuzzy_threshold

    def resolve(self, path: str) -> List[PathCandidate]:
        """解析路径，返回候选列表"""
        candidates = []

        # Strategy 1: Exact match
        exact = self._try_exact(path)
        if exact:
            candidates.append(exact)

        # Strategy 2: Suffix completion
        suffix_matches = self._try_suffix_completion(path)
        candidates.extend(suffix_matches)

        # Strategy 3: Fuzzy search
        if not candidates:
            fuzzy_matches = self._try_fuzzy_search(path)
            candidates.extend(fuzzy_matches)

        # Sort by score descending
        candidates.sort(key=lambda c: c.score, reverse=True)

        return candidates[:5]  # Top 5 candidates

    def resolve_single(self, path: str) -> Optional[Path]:
        """解析为单一路径（高置信度时自动选择）"""
        candidates = self.resolve(path)
        if candidates and candidates[0].score >= 0.9:
            return candidates[0].path
        return None
```

**匹配策略详解**:

| 策略 | 触发条件 | 置信度 | 示例 |
|------|----------|--------|------|
| exact | `Path.exists()` | 1.0 | `utils.py` -> `utils.py` |
| suffix | basename 无后缀 | 0.95 | `utils` -> `utils.py` |
| fuzzy | 编辑距离 < 3 | 0.6-0.9 | `utlis` -> `utils.py` |
| recent | 同名最近修改 | 0.7 | `main` -> `src/main.py` |

#### 2. ToolRetryMiddleware (工具重试中间件)

**职责**: 拦截工具执行，实现自动重试和错误增强

**位置**: `src/nimbus/tools/middleware.py`

```python
@dataclass
class ToolRetryConfig:
    """重试配置"""
    max_retries: int = 2              # 最大重试次数
    auto_resolve: bool = True         # 是否自动解析路径
    auto_resolve_threshold: float = 0.9  # 自动选择阈值
    ask_on_ambiguous: bool = True     # 歧义时询问用户
    inject_context_on_fail: bool = True  # 失败时注入上下文

class ToolRetryMiddleware:
    """工具重试中间件

    拦截点:
    1. Pre-execution: 路径预解析
    2. On-failure: 智能重试 / 询问用户
    3. Post-execution: 结果增强
    """

    def __init__(
        self,
        resolver: SmartPathResolver,
        config: ToolRetryConfig = ToolRetryConfig(),
        clarification_callback: Optional[Callable] = None,
    ):
        self.resolver = resolver
        self.config = config
        self.clarify = clarification_callback

    async def wrap_execute(
        self,
        registry: ToolRegistry,
        name: str,
        params: Dict[str, Any],
        **context: Any,
    ) -> Any:
        """包装工具执行"""

        # Phase 1: Pre-execution path resolution
        resolved_params = await self._pre_resolve(name, params)

        # Phase 2: Execute with retry
        last_error: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return await registry.execute(name, resolved_params, **context)
            except FileNotFoundError as e:
                last_error = e
                # Try to recover
                resolved_params = await self._on_file_not_found(
                    name, resolved_params, e, attempt
                )
                if resolved_params is None:
                    break
            except ToolExecutionError as e:
                last_error = e
                if not self._is_retryable(e):
                    break

        # Phase 3: Enhance error with suggestions
        raise self._enhance_error(last_error, name, params)

    async def _pre_resolve(self, tool_name: str, params: Dict) -> Dict:
        """预解析路径参数"""
        if tool_name not in ("Read", "Glob", "Grep", "Write", "Edit"):
            return params

        path_param = self._get_path_param(tool_name)
        if path_param not in params:
            return params

        original_path = params[path_param]
        resolved = self.resolver.resolve_single(original_path)

        if resolved:
            params = params.copy()
            params[path_param] = str(resolved)
            params["_original_path"] = original_path  # 保留原始路径

        return params

    async def _on_file_not_found(
        self,
        tool_name: str,
        params: Dict,
        error: FileNotFoundError,
        attempt: int,
    ) -> Optional[Dict]:
        """文件未找到时的恢复策略"""
        path_param = self._get_path_param(tool_name)
        original_path = params.get("_original_path", params.get(path_param))

        # Get candidates
        candidates = self.resolver.resolve(original_path)

        if not candidates:
            return None  # No recovery possible

        # Strategy: Auto-select if high confidence
        if candidates[0].score >= self.config.auto_resolve_threshold:
            params = params.copy()
            params[path_param] = str(candidates[0].path)
            return params

        # Strategy: Ask user for clarification
        if self.config.ask_on_ambiguous and self.clarify:
            selected = await self.clarify(
                message=f"Did you mean one of these?\n" +
                        "\n".join(f"  {i+1}. {c.path} ({c.reason})"
                                  for i, c in enumerate(candidates)),
                options=[str(c.path) for c in candidates],
            )
            if selected:
                params = params.copy()
                params[path_param] = selected
                return params

        return None  # Let error propagate

    def _enhance_error(
        self,
        error: Exception,
        tool_name: str,
        params: Dict,
    ) -> ToolExecutionError:
        """增强错误信息"""
        path_param = self._get_path_param(tool_name)
        original_path = params.get(path_param, "unknown")

        # Get suggestions
        candidates = self.resolver.resolve(original_path)
        suggestions = [f"  - {c.path}" for c in candidates[:3]]

        enhanced_msg = (
            f"File not found: {original_path}\n"
            f"Did you mean:\n" + "\n".join(suggestions) if suggestions else ""
        )

        return ToolExecutionError(
            tool_name=tool_name,
            message=enhanced_msg,
            original_error=error,
        )
```

**中间件集成方式**:

```python
# 在 ToolRegistry 中添加中间件支持
class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, tuple[ToolDefinition, Callable]] = {}
        self._middleware: List[ToolMiddleware] = []

    def use(self, middleware: ToolMiddleware) -> None:
        """添加中间件"""
        self._middleware.append(middleware)

    async def execute(self, name: str, params: Dict, **context) -> Any:
        """执行工具（经过中间件）"""
        # Build middleware chain
        async def core_execute(name: str, params: Dict, **ctx) -> Any:
            # ... existing execute logic

        execute_fn = core_execute
        for mw in reversed(self._middleware):
            execute_fn = mw.wrap(execute_fn)

        return await execute_fn(name, params, **context)
```

#### 3. FileTreeCache (文件树缓存)

**职责**: 缓存工作区文件树，支持快速查找和模糊匹配

**位置**: `src/nimbus/tools/filetree.py`

```python
@dataclass
class FileTreeEntry:
    """文件树条目"""
    path: Path
    name: str           # 文件名
    stem: str           # 不带后缀的文件名
    suffix: str         # 后缀
    mtime: float        # 修改时间
    is_dir: bool

class FileTreeCache:
    """文件树缓存

    功能:
    1. 增量更新 (inotify/watchdog)
    2. 快速查找 (前缀树 / 倒排索引)
    3. 模糊匹配 (编辑距离)
    """

    def __init__(
        self,
        workspace: Path,
        exclude_patterns: List[str] = ["node_modules", ".git", "__pycache__", ".venv"],
        max_depth: int = 10,
        max_files: int = 50000,
    ):
        self.workspace = workspace
        self.exclude = exclude_patterns
        self.max_depth = max_depth
        self.max_files = max_files

        # Index structures
        self._entries: Dict[Path, FileTreeEntry] = {}
        self._by_name: Dict[str, List[Path]] = {}   # filename -> [paths]
        self._by_stem: Dict[str, List[Path]] = {}   # stem -> [paths]
        self._recent: List[Path] = []               # recently modified

        self._initialized = False

    async def initialize(self) -> None:
        """初始化文件树缓存"""
        await self._scan_directory(self.workspace, depth=0)
        self._recent = sorted(
            self._entries.keys(),
            key=lambda p: self._entries[p].mtime,
            reverse=True,
        )[:100]
        self._initialized = True

    def find_by_name(self, name: str) -> List[FileTreeEntry]:
        """精确名称查找"""
        paths = self._by_name.get(name, [])
        return [self._entries[p] for p in paths]

    def find_by_stem(self, stem: str) -> List[FileTreeEntry]:
        """按 stem 查找（支持后缀补全）"""
        paths = self._by_stem.get(stem, [])
        return [self._entries[p] for p in paths]

    def find_fuzzy(self, query: str, threshold: float = 0.6) -> List[Tuple[FileTreeEntry, float]]:
        """模糊查找（编辑距离）"""
        results = []
        query_lower = query.lower()

        for entry in self._entries.values():
            # Check name similarity
            score = self._similarity(query_lower, entry.name.lower())
            if score >= threshold:
                results.append((entry, score))

            # Check stem similarity
            stem_score = self._similarity(query_lower, entry.stem.lower())
            if stem_score >= threshold and stem_score > score:
                results.append((entry, stem_score))

        return sorted(results, key=lambda x: x[1], reverse=True)

    def _similarity(self, s1: str, s2: str) -> float:
        """计算相似度 (1 - normalized_levenshtein)"""
        if s1 == s2:
            return 1.0
        max_len = max(len(s1), len(s2))
        if max_len == 0:
            return 1.0
        distance = self._levenshtein(s1, s2)
        return 1.0 - (distance / max_len)

    def get_tree_summary(self, max_depth: int = 2, max_entries: int = 50) -> str:
        """生成文件树摘要（用于 Context Injection）"""
        # ... implementation
```

#### 4. ContextInjector (上下文注入器)

**职责**: 在 LLM 请求前注入文件树信息

**位置**: `src/nimbus/core/context_injector.py`

```python
class ContextInjector:
    """上下文注入器

    注入策略:
    1. Session 开始时注入完整文件树摘要
    2. 每次请求时注入相关文件
    3. 工具失败后注入修正建议
    """

    def __init__(
        self,
        file_tree: FileTreeCache,
        memory: TieredMemoryManager,
    ):
        self.file_tree = file_tree
        self.memory = memory

    async def inject_session_context(self) -> None:
        """Session 开始时注入文件树"""
        tree_summary = self.file_tree.get_tree_summary()

        self.memory.pin(PinnedItem(
            id="file_tree",
            type="file_meta",
            content=f"## Project Structure\n```\n{tree_summary}\n```",
            priority=10,
            description="Current project file tree (auto-updated)",
            read_only=True,
        ))

    async def inject_on_failure(
        self,
        tool_name: str,
        error: ToolExecutionError,
        candidates: List[PathCandidate],
    ) -> str:
        """工具失败后注入修正建议到 LLM Context"""
        suggestion = (
            f"Tool {tool_name} failed: {error.message}\n"
            f"Suggestions:\n" +
            "\n".join(f"- {c.path} ({c.reason})" for c in candidates)
        )

        # 注入到 working memory
        self.memory.set_working("last_tool_error", suggestion)

        return suggestion
```

### 数据流

```
┌───────────────────────────────────────────────────────────────────────────┐
│ User: "帮我看下 utils 文件"                                               │
└───────────────────────────────────────────────────────────────────────────┘
        │
        │ 1. Session Start
        ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ ContextInjector.inject_session_context()                                  │
│ → Memory.pin("file_tree", "src/\n  utils.py\n  main.py\n...")            │
└───────────────────────────────────────────────────────────────────────────┘
        │
        │ 2. LLM Planning
        ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ LLM sees file tree → generates: Read(file_path="src/utils.py")           │
│ (or "utils" if LLM hallucinates)                                         │
└───────────────────────────────────────────────────────────────────────────┘
        │
        │ 3. Tool Execution
        ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ ToolRetryMiddleware.wrap_execute("Read", {"file_path": "utils"})         │
│                                                                           │
│   ┌─────────────────────────────────────────────────────────────────┐    │
│   │ Phase 1: Pre-resolve                                             │    │
│   │ SmartPathResolver.resolve("utils")                               │    │
│   │ → [PathCandidate(path="src/utils.py", score=0.95, reason="suffix")]  │
│   │ → Auto-select (score >= 0.9)                                     │    │
│   └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│   ┌─────────────────────────────────────────────────────────────────┐    │
│   │ Phase 2: Execute                                                 │    │
│   │ ToolRegistry.execute("Read", {"file_path": "src/utils.py"})     │    │
│   │ → Success!                                                       │    │
│   └─────────────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────────────┘
```

**失败场景流程**:

```
┌───────────────────────────────────────────────────────────────────────────┐
│ LLM generates: Read(file_path="utility.py")  ← 幻觉路径                   │
└───────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ ToolRetryMiddleware                                                       │
│                                                                           │
│ Pre-resolve: SmartPathResolver.resolve("utility.py")                     │
│ → [] (no exact match)                                                     │
│                                                                           │
│ Execute: Read("utility.py")                                              │
│ → FileNotFoundError                                                       │
│                                                                           │
│ On-failure (attempt 1):                                                   │
│   SmartPathResolver.resolve("utility.py")                                │
│   → [PathCandidate(path="src/utils.py", score=0.75, reason="fuzzy")]    │
│   → score < 0.9, ask_on_ambiguous=True                                   │
│                                                                           │
│   ┌────────────────────────────────────────────────────────────────┐     │
│   │ 交互式澄清                                                      │     │
│   │ "Did you mean one of these?"                                   │     │
│   │   1. src/utils.py (fuzzy match)                                │     │
│   │   2. src/util/helper.py (fuzzy match)                          │     │
│   │ User selects: 1                                                │     │
│   └────────────────────────────────────────────────────────────────┘     │
│                                                                           │
│ Retry: Read("src/utils.py")                                              │
│ → Success!                                                                │
└───────────────────────────────────────────────────────────────────────────┘
```

## Decisions

### Decision 1: 分层架构而非工具内置

- **决策**: 采用独立的 SmartPathResolver + ToolRetryMiddleware，而非在每个工具内部实现
- **理由**:
  1. 职责分离，便于测试和维护
  2. 可复用性高，Read/Glob/Grep/Write/Edit 共享
  3. 可配置性强，不同场景可调整策略
- **备选方案**: 在 read_file() 内部调用 resolve()
- **风险**: 增加调用链复杂度，可能影响性能

### Decision 2: 自动解析阈值 0.9

- **决策**: 置信度 >= 0.9 时自动选择候选路径，否则询问用户
- **理由**:
  1. 0.9 阈值平衡了用户体验和准确性
  2. 后缀补全 (utils -> utils.py) 通常 0.95
  3. 模糊匹配 (utlis -> utils.py) 通常 0.7-0.85
- **备选方案**: 始终询问用户 / 始终自动选择
- **风险**: 误选可能导致读取错误文件

### Decision 3: 工具级重试与 DAG 级重试正交

- **决策**: ToolRetryMiddleware 负责单个工具的即时重试，DAG on_failure 负责复杂的 fix-and-retry 循环
- **理由**:
  1. 工具级重试处理简单的路径错误（秒级）
  2. DAG 级重试处理需要 LLM 介入的复杂修复（分钟级）
  3. 两者职责清晰，不会冲突
- **备选方案**: 统一在 DAG 级处理
- **风险**: 简单错误也触发 DAG 重试，浪费资源

### Decision 4: 文件树在 Session 级别缓存

- **决策**: FileTreeCache 在 Session 开始时初始化，增量更新
- **理由**:
  1. 避免每次工具调用都扫描文件系统
  2. 支持快速模糊查找
  3. 可使用 watchdog 监听变更
- **备选方案**: 每次调用时实时扫描
- **风险**: 缓存可能与文件系统不同步

## Tradeoffs

1. **自动化 vs 准确性**: 选择 0.9 阈值自动解析，牺牲部分准确性换取流畅体验
2. **性能 vs 功能**: FileTreeCache 增加内存占用，换取快速查找
3. **简单性 vs 灵活性**: 中间件架构增加复杂度，换取可插拔性

## Constraints

- **技术约束**:
  - 必须与现有 Sandbox 安全机制兼容
  - 必须与 DAG 重试机制 (on_failure, retry_target) 正交
  - 文件树缓存不能超过 10MB 内存

- **业务约束**:
  - 自动解析不能导致安全问题（不能绕过 Sandbox）
  - 交互式澄清需要 UI 支持回调

## Risks

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 自动选择错误文件 | 中 | 中 | 保守阈值 0.9 + 日志记录 |
| FileTreeCache 内存溢出 | 低 | 高 | max_files 限制 + LRU 淘汰 |
| 中间件链过长影响性能 | 低 | 低 | 性能监控 + 可选禁用 |
| 缓存与文件系统不同步 | 中 | 低 | watchdog 监听 + 定时刷新 |

## Evidence

- Sources:
  - `src/nimbus/tools/base.py:482-533`: ToolRegistry.execute 现有实现
  - `src/nimbus/tools/read.py:127-247`: read_file 工具实现
  - `src/nimbus/tools/sandbox.py:76-138`: Sandbox.validate 安全验证
  - `src/nimbus/core/runtime/executor.py:562-632`: DAG 级 on_failure 重试
  - `src/nimbus/core/memory.py:143-164`: TieredMemoryManager.pin 方法

- Assumptions:
  - UI 层支持交互式澄清回调 (clarification_callback)
  - watchdog 库可用于文件监听
  - 工作区文件数通常 < 50000

## Relationship with Existing DAG Retry

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Nimbus Retry Architecture                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ Level 1: Tool-level Retry (ToolRetryMiddleware)                    │ │
│  │ - Scope: Single tool execution                                     │ │
│  │ - Trigger: FileNotFoundError, simple path errors                   │ │
│  │ - Recovery: SmartPathResolver + auto-select / ask user             │ │
│  │ - Latency: ~10ms (no LLM call)                                     │ │
│  │ - Max retries: 2                                                   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              │ If unrecoverable                          │
│                              ▼                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ Level 2: DAG Task Retry (RuntimeConfig.max_retries)                │ │
│  │ - Scope: TaskNode in DAG                                           │ │
│  │ - Trigger: TimeoutError, ConnectionError                           │ │
│  │ - Recovery: Simple retry with delay                                │ │
│  │ - Latency: ~1s (includes retry_delay)                              │ │
│  │ - Max retries: 2 (RuntimeConfig)                                   │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                              │                                           │
│                              │ If still fails                            │
│                              ▼                                           │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │ Level 3: DAG Fix-Retry Loop (on_failure + retry_target)            │ │
│  │ - Scope: Task graph with fix task                                  │ │
│  │ - Trigger: Task marked FAILED with on_failure set                  │ │
│  │ - Recovery: Execute fix_task → retry retry_target                  │ │
│  │ - Latency: ~10s-60s (LLM generates fix)                            │ │
│  │ - Max retries: TaskNode.max_retries (default 0)                    │ │
│  │ - Example: test_fails → generate_fix → re-test                     │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**适用场景**:

| 错误类型 | 处理层级 | 示例 |
|----------|----------|------|
| 路径拼写错误 | Level 1 | `utlis.py` -> `utils.py` |
| 路径缺后缀 | Level 1 | `utils` -> `utils.py` |
| 路径不存在 | Level 1 | 询问用户选择候选 |
| 网络超时 | Level 2 | 自动重试 2 次 |
| LLM 幻觉路径 | Level 1+3 | L1 失败后 L3 让 LLM 修正 |
| 测试失败 | Level 3 | fix_task 生成修复代码 |

## Next Steps

1. **实现 SmartPathResolver** (`src/nimbus/tools/resolver.py`)
   - 精确匹配、后缀补全、模糊搜索
   - 单元测试覆盖各种边界情况

2. **实现 FileTreeCache** (`src/nimbus/tools/filetree.py`)
   - 异步初始化、增量更新
   - 内存限制、LRU 淘汰

3. **实现 ToolRetryMiddleware** (`src/nimbus/tools/middleware.py`)
   - 集成到 ToolRegistry
   - 支持 clarification_callback

4. **实现 ContextInjector** (`src/nimbus/core/context_injector.py`)
   - Session 级文件树注入
   - 失败后建议注入

5. **集成测试**
   - E2E 测试各种失败场景
   - 性能测试（大型项目）

## Appendix: Configuration Example

```yaml
# nimbus.yaml
tool_failover:
  enabled: true

  path_resolver:
    suffix_priority: [".py", ".ts", ".js", ".go", ".rs"]
    fuzzy_threshold: 0.6

  retry:
    max_retries: 2
    auto_resolve_threshold: 0.9
    ask_on_ambiguous: true

  file_tree_cache:
    enabled: true
    max_files: 50000
    max_depth: 10
    exclude_patterns:
      - "node_modules"
      - ".git"
      - "__pycache__"
      - ".venv"
      - "dist"
      - "build"
    watch_enabled: true

  context_injection:
    inject_on_session: true
    inject_on_failure: true
    tree_summary_max_depth: 2
    tree_summary_max_entries: 50
```
