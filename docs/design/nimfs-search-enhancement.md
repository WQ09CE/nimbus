# NimFS 记忆搜索增强设计方案

> **Author**: Architect Agent  
> **Date**: 2025-01-XX  
> **Status**: Draft  
> **Scope**: `NimFSManager.search_memory` 增强 + 新增 `NimFSListMemory` 工具

---

## 1. 问题概述

当前 `NimFSSearchMemory` 存在以下限制：

| # | 问题 | 影响 |
|---|------|------|
| P1 | 搜索范围仅 title + tags，不含 summary/content | Agent 写入详细 summary 后搜不到 |
| P2 | 空 query 或 `*` 返回空结果 | 无法浏览所有记忆 |
| P3 | 不支持通配符（`*`, `?`） | `nim*` 匹配不到 "nimbus" |
| P4 | Memory 没有 ListAll 接口 | Artifacts 有 `NimFSListArtifacts`，Memory 没有对等接口 |
| P5 | token 过滤丢弃单字符 query | 搜 `*` 时 tokens 为空，走 `[query_lower]` 分支但仍匹配不到 |

**根因代码** (`manager.py` L466-472):

```python
searchable = entry.title.lower() + " " + " ".join(entry.tags).lower()
tokens = [t for t in query_lower.split() if len(t) > 1]
if not tokens:
    tokens = [query_lower]
if any(token in searchable for token in tokens):
    results.append(entry)
```

- `searchable` 只含 title + tags
- 空 query → `query_lower = ""` → `tokens = [""]` → `"" in searchable` 恒为 True（Python 行为），但实际从未走到这里因为上层可能校验
- `*` → `tokens = ["*"]` → `"*" in searchable` 为 False（字面匹配）

---

## 2. 设计方案

### 2.1 增强 `search_memory` 方法

#### 2.1.1 支持空 query 返回全量

**策略**：当 `query` 为空字符串、`*` 或 `**` 时，跳过关键词匹配，返回所有条目（仍受 `category`、`min_confidence`、`scope`、`top_k` 过滤）。

#### 2.1.2 搜索范围扩大到 summary (L0)

**策略**：将 L0 abstract 的内容加入 `searchable` 字符串。L0 文件很小（< 200 chars），加载开销可忽略。

> **不搜 L1/L2**：L2 可能非常大，全文扫描有性能风险。L1 是中等长度的概览，Phase 0 暂不纳入。Phase 1 升级到向量搜索后自然覆盖。

#### 2.1.3 通配符支持

**策略**：使用 `fnmatch` 模式。当 query token 中包含 `*` 或 `?` 时，使用 `fnmatch.fnmatch` 逐词匹配；否则保持现有子串匹配。

### 2.2 新增 `list_memory` 方法 + `NimFSListMemory` 工具

**对标** `list_artifacts` / `NimFSListArtifacts`，提供无需关键词的记忆列表接口。

---

## 3. 涉及文件与修改清单

| 文件 | 修改类型 | 说明 |
|------|---------|------|
| `src/nimbus/core/nimfs/manager.py` | 修改 | 增强 `search_memory`，新增 `list_memory` |
| `src/nimbus/tools/nimfs_tools.py` | 修改 | 新增 `nimfs_list_memory` 函数 + `NIMFS_LIST_MEMORY_TOOL` 定义，更新 `NIMFS_TOOLS` 和 `NIMFS_TOOL_FUNCTIONS` |
| `src/nimbus/tools/__init__.py` | 修改 | 导出新工具 |
| `src/nimbus/core/nimfs/__init__.py` | 不变 | `list_memory` 通过 `NimFSManager` 暴露，无需额外导出 |
| `tests/test_nimfs.py` | 修改 | 新增测试用例 |

---

## 4. 详细代码修改方案

### 4.1 `manager.py` — 增强 `search_memory`

**位置**：`search_memory` 方法（L401-473）

**修改后完整方法**：

```python
def search_memory(
    self,
    query: str,
    category: Optional[MemoryCategory] = None,
    top_k: int = 5,
    min_confidence: float = 0.0,
    scope: str = "project",  # "project" | "global" | "all"
) -> List[MemoryEntry]:
    """
    Keyword search over memory entries.

    Searches title, tags, and L0 summary for case-insensitive matches.
    Supports:
      - Empty query / "*" / "**": returns all entries (list mode)
      - Wildcard tokens (fnmatch): "nim*" matches "nimbus"
      - Plain tokens: substring match (existing behavior)

    Scans the specified scope(s) and returns top_k results sorted by
    updated_at descending.
    """
    import fnmatch

    query_stripped = query.strip()
    # "list all" mode: empty query, single *, or **
    list_all = query_stripped in ("", "*", "**")

    if not list_all:
        query_lower = query_stripped.lower()
        tokens = [t for t in query_lower.split() if len(t) > 0]
        if not tokens:
            list_all = True  # fallback: all-whitespace query

    results: List[MemoryEntry] = []

    search_roots: List[Path] = []
    if scope in ("project", "all"):
        search_roots.append(self.memory_root)
    if scope in ("global", "all"):
        search_roots.append(self.global_root)

    for base_root in search_roots:
        if not base_root.exists():
            continue

        categories = [category] if category else list(MemoryCategory)
        for cat in categories:
            cat_dir = base_root / cat.value
            if not cat_dir.exists():
                continue

            for entry_dir in cat_dir.iterdir():
                if not entry_dir.is_dir():
                    continue
                meta_path = entry_dir / "meta.json"
                if not meta_path.exists():
                    continue

                try:
                    entry = MemoryEntry.from_dict(_read_json(meta_path))
                except Exception:
                    continue

                if entry.confidence < min_confidence:
                    continue

                # List-all mode: skip keyword matching
                if list_all:
                    results.append(entry)
                    continue

                # Build searchable text: title + tags + L0 summary
                searchable = entry.title.lower() + " " + " ".join(entry.tags).lower()
                l0_path = entry_dir / "l0.abstract"
                if l0_path.exists():
                    try:
                        l0_text = l0_path.read_text(encoding="utf-8").strip().lower()
                        searchable += " " + l0_text
                    except Exception:
                        pass

                # Token matching: OR logic (any token matches → include)
                matched = False
                for token in tokens:
                    if "*" in token or "?" in token:
                        # Wildcard mode: check against each word in searchable
                        words = searchable.split()
                        if any(fnmatch.fnmatch(word, token) for word in words):
                            matched = True
                            break
                    else:
                        # Substring mode (original behavior)
                        if token in searchable:
                            matched = True
                            break

                if matched:
                    results.append(entry)

    results.sort(key=lambda e: e.updated_at, reverse=True)
    return results[:top_k]
```

**关键变更说明**：

1. **list_all 模式**（L5-7）：`query` 为 `""` / `"*"` / `"**"` 时跳过所有匹配逻辑，直接收集
2. **L0 纳入搜索**（L41-46）：读取 `l0.abstract` 追加到 `searchable`
3. **通配符**（L51-55）：token 含 `*` 或 `?` 时用 `fnmatch.fnmatch` 逐词比较
4. **token 过滤阈值**（L10）：从 `len(t) > 1` 改为 `len(t) > 0`，不再丢弃单字符 query
5. **完全向后兼容**：不含通配符的普通 query 走原有子串匹配逻辑

---

### 4.2 `manager.py` — 新增 `list_memory` 方法

**位置**：在 `search_memory` 方法之后、`load_context` 之前插入。

```python
def list_memory(
    self,
    category: Optional[MemoryCategory] = None,
    scope: str = "project",  # "project" | "global" | "all"
    top_k: int = 50,
) -> List[MemoryEntry]:
    """
    List all memory entries, optionally filtered by category and scope.

    Unlike search_memory, this requires no query and returns all entries.
    Sorted by updated_at descending. Use top_k to limit results.

    Args:
        category: Optional category filter.
        scope:    "project", "global", or "all".
        top_k:    Maximum entries to return (default 50).

    Returns:
        List of MemoryEntry sorted by updated_at descending.
    """
    # Delegate to search_memory with empty query (list-all mode)
    return self.search_memory(
        query="*",
        category=category,
        top_k=top_k,
        min_confidence=0.0,
        scope=scope,
    )
```

> **设计决策**：`list_memory` 是 `search_memory(query="*")` 的语义化封装。不重复扫描逻辑，避免代码重复。默认 `top_k=50`（高于 search 的 5），因为 list 场景通常需要看更多条目。

---

### 4.3 `nimfs_tools.py` — 新增 `nimfs_list_memory` 工具函数

**位置**：在 `nimfs_search_memory` 函数之后插入。

```python
async def nimfs_list_memory(
    category: str = "",
    scope: str = "all",
    top_k: str = "50",
    **ctx: Any,
) -> str:
    """
    List all memory entries in NimFS, optionally filtered by category and scope.

    No search query needed — returns all entries sorted by updated_at descending.

    Args:
        category: Optional category filter (leave empty for all categories).
        scope:    "project" | "global" | "all" (default "all").
        top_k:    Maximum entries to return (default "50").

    Returns:
        Formatted list of memory entries with L0 summaries.
    """
    cat_enum: Optional[MemoryCategory] = None
    if category.strip():
        try:
            cat_enum = MemoryCategory(category.lower())
        except ValueError:
            valid = [c.value for c in MemoryCategory]
            return f"❌ Invalid category '{category}'. Must be one of: {valid}"

    try:
        k = int(top_k)
    except ValueError:
        k = 50

    manager = _get_manager(**ctx)
    results = manager.list_memory(category=cat_enum, scope=scope, top_k=k)

    if not results:
        scope_label = f" (scope={scope})" if scope != "all" else ""
        cat_label = f" [{category}]" if category.strip() else ""
        return f"No memory entries found{cat_label}{scope_label}. NimFS memory may be empty."

    lines = [f"## NimFS Memory: {len(results)} entries\n"]
    for entry in results:
        # Load L0 abstract for preview
        try:
            l0 = manager.read_memory(entry.memory_id, layer=0)
            preview = l0[:150] + "..." if len(l0) > 150 else l0
        except Exception:
            preview = "(no preview available)"

        lines.append(
            f"### {entry.title}\n"
            f"- **Memory ID** : {entry.memory_id}\n"
            f"- **Category**  : {entry.category.value}\n"
            f"- **Scope**     : {entry.scope.value}\n"
            f"- **Confidence**: {entry.confidence:.2f}\n"
            f"- **Tags**      : {', '.join(entry.tags) or 'none'}\n"
            f"- **Updated**   : {entry.updated_at}\n"
            f"- **Abstract**  : {preview}\n"
        )

    return "\n".join(lines)
```

---

### 4.4 `nimfs_tools.py` — 新增工具定义

**位置**：在 `NIMFS_SEARCH_MEMORY_TOOL` 定义之后插入。

```python
NIMFS_LIST_MEMORY_TOOL: Dict[str, Any] = {
    "name": "NimFSListMemory",
    "description": (
        "List all memory entries in NimFS. No search query needed — "
        "returns all entries optionally filtered by category and scope. "
        "Use this to browse what knowledge has been stored."
    ),
    "function": nimfs_list_memory,
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Optional category filter: profile | preferences | entities | events | cases | patterns",
            },
            "scope": {
                "type": "string",
                "description": "project | global | all (default 'all')",
            },
            "top_k": {
                "type": "string",
                "description": "Max entries to return (default '50')",
            },
        },
        "required": [],
    },
}
```

---

### 4.5 `nimfs_tools.py` — 更新注册表

**修改 `NIMFS_TOOLS` 列表**（约 L480）：

```python
NIMFS_TOOLS: List[Dict[str, Any]] = [
    NIMFS_WRITE_ARTIFACT_TOOL,
    NIMFS_READ_ARTIFACT_TOOL,
    NIMFS_LIST_ARTIFACTS_TOOL,
    NIMFS_WRITE_MEMORY_TOOL,
    NIMFS_SEARCH_MEMORY_TOOL,
    NIMFS_LIST_MEMORY_TOOL,      # ← 新增
    NIMFS_LOAD_CONTEXT_TOOL,
]
```

**修改 `NIMFS_TOOL_FUNCTIONS` 字典**（约 L490）：

```python
NIMFS_TOOL_FUNCTIONS: Dict[str, Any] = {
    "NimFSWriteArtifact": nimfs_write_artifact,
    "NimFSReadArtifact":  nimfs_read_artifact,
    "NimFSListArtifacts": nimfs_list_artifacts,
    "NimFSWriteMemory":   nimfs_write_memory,
    "NimFSSearchMemory":  nimfs_search_memory,
    "NimFSListMemory":    nimfs_list_memory,    # ← 新增
    "NimFSLoadContext":   nimfs_load_context,
}
```

---

### 4.6 `nimfs_tools.py` — 更新 `NIMFS_SEARCH_MEMORY_TOOL` description

```python
NIMFS_SEARCH_MEMORY_TOOL: Dict[str, Any] = {
    "name": "NimFSSearchMemory",
    "description": (
        "Search long-term memory entries in NimFS by keyword. "
        "Searches titles, tags, and summaries (case-insensitive). "
        "Supports wildcards (* and ?). "
        "Use empty query or '*' to list all entries. "
        "Returns L0 summaries for matching entries."
    ),
    # ... parameters 不变
}
```

---

### 4.7 `tools/__init__.py` — 导出新工具

**位置**：在 nimfs_tools 的 import 区域（约 L50-70），添加 `nimfs_list_memory` 和 `NIMFS_LIST_MEMORY_TOOL`。

具体取决于现有导入方式。当前代码批量导入 `NIMFS_TOOLS` 和 `NIMFS_TOOL_FUNCTIONS`，这两个集合已在 4.5 中更新，所以 `__init__.py` **可能无需修改**——前提是它是通过 `NIMFS_TOOLS` 列表动态注册的。

**验证**：检查 `__init__.py` 中的注册逻辑。如果它硬编码了工具名列表，则需要添加 `"NimFSListMemory"`。

---

### 4.8 `tests/test_nimfs.py` — 新增测试

```python
# =========================================================================
# search_memory enhancements
# =========================================================================

def test_search_memory_wildcard_query(manager):
    """Wildcard pattern should match entry titles."""
    manager.write_memory(MemoryCategory.ENTITIES, "NimFSManager", "manager content")
    manager.write_memory(MemoryCategory.ENTITIES, "NimFSGC", "gc content")

    results = manager.search_memory("nimfs*")
    titles = [e.title for e in results]
    assert "NimFSManager" in titles
    assert "NimFSGC" in titles


def test_search_memory_star_returns_all(manager):
    """query='*' should return all entries."""
    manager.write_memory(MemoryCategory.EVENTS, "event1", "content1")
    manager.write_memory(MemoryCategory.CASES, "case1", "content2")
    manager.write_memory(MemoryCategory.PATTERNS, "pattern1", "content3")

    results = manager.search_memory("*", top_k=100)
    assert len(results) == 3


def test_search_memory_empty_query_returns_all(manager):
    """Empty query should return all entries."""
    manager.write_memory(MemoryCategory.EVENTS, "event1", "content1")
    manager.write_memory(MemoryCategory.CASES, "case1", "content2")

    results = manager.search_memory("", top_k=100)
    assert len(results) == 2


def test_search_memory_matches_summary(manager):
    """Search should match against L0 summary text."""
    manager.write_memory(
        MemoryCategory.CASES, "Generic title",
        content="Full content here",
        summary="UniqueKeywordInSummary is important",
    )

    results = manager.search_memory("uniquekeywordinsummary")
    assert len(results) == 1
    assert results[0].title == "Generic title"


def test_search_memory_no_match_in_summary(manager):
    """Ensure non-matching summary doesn't create false positives."""
    manager.write_memory(
        MemoryCategory.CASES, "Another title",
        content="content",
        summary="something else entirely",
    )

    results = manager.search_memory("nonexistent_xyz")
    assert len(results) == 0


# =========================================================================
# list_memory
# =========================================================================

def test_list_memory_returns_all(manager):
    """list_memory should return all entries without query."""
    manager.write_memory(MemoryCategory.EVENTS, "event1", "c1")
    manager.write_memory(MemoryCategory.CASES, "case1", "c2")
    manager.write_memory(MemoryCategory.PATTERNS, "pattern1", "c3")

    results = manager.list_memory()
    assert len(results) == 3


def test_list_memory_category_filter(manager):
    """list_memory with category should only return that category."""
    manager.write_memory(MemoryCategory.EVENTS, "event1", "c1")
    manager.write_memory(MemoryCategory.CASES, "case1", "c2")

    results = manager.list_memory(category=MemoryCategory.EVENTS)
    assert len(results) == 1
    assert results[0].category == MemoryCategory.EVENTS


def test_list_memory_scope_filter(manager):
    """list_memory respects scope parameter."""
    manager.write_memory(MemoryCategory.PROFILE, "local", "c1",
                         scope=MemoryScope.PROJECT)
    manager.write_memory(MemoryCategory.PROFILE, "global", "c2",
                         scope=MemoryScope.GLOBAL)

    project_results = manager.list_memory(scope="project")
    global_results = manager.list_memory(scope="global")
    all_results = manager.list_memory(scope="all")

    # project 和 global 的条目分别存储在不同 root
    assert len(all_results) >= len(project_results)
    assert len(all_results) >= len(global_results)


def test_list_memory_empty(manager):
    """list_memory on empty NimFS should return empty list."""
    results = manager.list_memory()
    assert results == []
```

---

## 5. 向后兼容性分析

| 变更 | 兼容性 | 理由 |
|------|--------|------|
| `search_memory` 空 query → 返回全量 | ✅ 兼容 | 之前空 query 行为未定义（返回空或全部），现在明确为返回全部 |
| 搜索范围扩大到 summary | ✅ 兼容 | 搜索结果是超集，不会丢失原有匹配 |
| 通配符支持 | ✅ 兼容 | 只有 token 含 `*`/`?` 时才启用 fnmatch，普通 query 不受影响 |
| `list_memory` 新增方法 | ✅ 兼容 | 纯新增，不影响现有 API |
| `NimFSListMemory` 新增工具 | ✅ 兼容 | 纯新增，不影响现有工具 |
| token 过滤 `len(t) > 1` → `len(t) > 0` | ⚠️ 微调 | 单字符搜索（如搜 "A"）现在能命中，之前被丢弃后走 fallback 分支。行为更符合预期 |

---

## 6. 性能考量

| 操作 | 开销 | 评估 |
|------|------|------|
| 读取 L0 abstract | 每条目一次小文件读取（< 200B） | 可忽略，Phase 0 记忆条目数 < 100 |
| `fnmatch` 通配符匹配 | 逐词比对 | O(words × tokens)，在小数据集上无感 |
| `list_all` 全量扫描 | 与当前扫描逻辑相同 | 无额外开销，只是跳过匹配步骤 |

**Phase 1 升级路径**：当记忆条目 > 500 时，建议引入 SQLite 索引或向量数据库（如 ChromaDB），替代目录扫描。本次修改不阻碍未来升级。

---

## 7. `load_context` 的间接受益

`load_context`（L474-530）内部调用 `search_memory(current_goal, top_k=8, scope="all")`。本次增强后：

1. **搜索范围扩大**：goal 中的关键词现在也能匹配 L0 summary，提升 context injection 的召回率
2. **通配符**：如果 goal 包含通配符模式（罕见但可能），也能正确处理

无需修改 `load_context` 本身。

---

## 8. 工具对称性（最终状态）

增强后 NimFS 工具集的对称结构：

| 操作 | Artifacts | Memory |
|------|-----------|--------|
| **Write** | `NimFSWriteArtifact` | `NimFSWriteMemory` |
| **Read** | `NimFSReadArtifact` | _(via SearchMemory preview)_ |
| **List** | `NimFSListArtifacts` | `NimFSListMemory` ← **新增** |
| **Search** | _(N/A)_ | `NimFSSearchMemory` ← **增强** |
| **Context** | — | `NimFSLoadContext` |

> **Future TODO**：考虑新增 `NimFSReadMemory` 工具（指定 memory_id + layer 直接读取），目前 Agent 只能通过 SearchMemory 的 L0 preview 查看内容。不在本次 scope 内。

---

## 9. 实施步骤

1. **修改 `manager.py`**：增强 `search_memory` + 新增 `list_memory`（约 50 行改动）
2. **修改 `nimfs_tools.py`**：新增 `nimfs_list_memory` + 更新注册表（约 80 行新增）
3. **修改 `tools/__init__.py`**：确认新工具被导出（可能 0 行改动）
4. **新增测试**：`test_nimfs.py` 添加 ~80 行测试用例
5. **运行 `pytest tests/test_nimfs.py`** 确认所有测试通过

**预计总改动量**：~210 行新增/修改代码
