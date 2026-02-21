#!/usr/bin/env python3
"""
migrate_memo_to_nimfs.py
========================
迁移脚本：将 .nimbus/memo_global.md 的内容分类后写入 NimFS Memory 分区。

迁移策略：
  1. 读取 .nimbus/memo_global.md
  2. 按 Markdown H2 (##) 章节拆分内容
  3. 根据章节标题关键词将每段归类为 NimFS MemoryCategory:
       - 核心架构 / MMU / 设计 → patterns
       - VCPU / 结论 / 研究   → cases
       - 里程碑 / 阶段 / 进度  → events
       - 用户偏好 / 风格       → preferences
       - 组件 / 文件 / 模块    → entities
       - 其余                  → patterns (默认)
  4. 调用 NimFSManager.write_memory() 写入 Memory 分区
  5. 将 memo_global.md 重命名为 memo_global.md.bak
  6. 如果存在当前进程的 memo_{session_id}.md，也一并备份

运行方式：
  cd /path/to/nimbus
  python scripts/migrate_memo_to_nimfs.py [--workspace /path/to/workspace]
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# 确保能 import nimbus 包
# ---------------------------------------------------------------------------
_WORKSPACE = Path(__file__).parent.parent  # scripts/../ = nimbus/
sys.path.insert(0, str(_WORKSPACE / "src"))

from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope


# ---------------------------------------------------------------------------
# 章节分类规则 (关键词 → MemoryCategory)
# ---------------------------------------------------------------------------
_CATEGORY_RULES: List[Tuple[List[str], MemoryCategory]] = [
    # cases: 经验、结论、研究、修复、问题（放在最前，防止被 patterns 抢走）
    (["结论", "研究", "vcpu", "根源", "建议", "修复", "audit", "session audit", "逻辑研究"],
     MemoryCategory.CASES),
    # events: 里程碑、进度、历史事件
    (["里程碑", "milestone", "阶段性", "阶段", "进度", "已完成", "已实现", "发布", "2025", "2024"],
     MemoryCategory.EVENTS),
    # patterns: 架构、设计、技术规范
    (["架构", "mmu", "memory management", "设计", "布局", "配置", "参数", "机制", "nimfs", "核心"],
     MemoryCategory.PATTERNS),
    # entities: 文件、组件、模块、工具
    (["文件", "组件", "模块", "工具", "tool", "agent", "dispatch", "src/", ".py"],
     MemoryCategory.ENTITIES),
    # preferences: 用户偏好、风格
    (["偏好", "风格", "约定", "规范", "preference"],
     MemoryCategory.PREFERENCES),
]

_DEFAULT_CATEGORY = MemoryCategory.PATTERNS


def classify_section(title: str, body: str) -> MemoryCategory:
    """根据章节标题和正文关键词，判断最合适的 MemoryCategory。
    
    优先以标题关键词匹配，再看正文。
    每条规则取最高匹配分，防止少数词汇误导分类。
    """
    title_lower = title.lower()
    body_lower = body[:500].lower()

    best_category = _DEFAULT_CATEGORY
    best_score = 0

    for keywords, category in _CATEGORY_RULES:
        score = 0
        for kw in keywords:
            kw_l = kw.lower()
            if kw_l in title_lower:
                score += 3  # 标题命中权重更高
            elif kw_l in body_lower:
                score += 1
        if score > best_score:
            best_score = score
            best_category = category

    return best_category


def split_into_sections(content: str) -> List[Tuple[str, str]]:
    """
    按 Markdown H2 (##) 拆分文档为 [(section_title, section_body), ...]。
    开头没有 H2 之前的内容归为 "General" 节。
    """
    lines = content.splitlines()
    sections: List[Tuple[str, str]] = []
    current_title = "General"
    current_lines: List[str] = []

    for line in lines:
        if line.startswith("## "):
            # 保存上一节（非空则存）
            body = "\n".join(current_lines).strip()
            if body:
                sections.append((current_title, body))
            current_title = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 最后一节
    body = "\n".join(current_lines).strip()
    if body:
        sections.append((current_title, body))

    # 过滤掉几乎为空的节 (只有注释或空行)
    filtered = []
    for title, body in sections:
        meaningful = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
        if meaningful:
            filtered.append((title, body))

    return filtered


def make_tags(title: str, category: MemoryCategory) -> List[str]:
    """生成标签列表。"""
    tags = [category.value, "migrated-from-memo"]
    # 提取标题里括号中的说明 (如 "2025-02-20")
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", title)
    tags.extend(dates)
    return tags


def generate_summary(title: str, body: str) -> str:
    """从节内容生成简短摘要 (<= 200 字符)。"""
    # 取正文第一个非空非注释行
    for line in body.splitlines():
        clean = line.strip().lstrip("#").strip()
        clean = re.sub(r"<!--.*?-->", "", clean).strip()
        if clean and len(clean) > 5:
            summary = f"[{title}] {clean}"
            return summary[:197] + "..." if len(summary) > 200 else summary
    return title[:200]


def backup_file(path: Path) -> None:
    """将文件重命名为 .bak，若 .bak 已存在则加序号。"""
    bak = path.with_suffix(path.suffix + ".bak")
    if bak.exists():
        # 找一个不冲突的名字
        i = 1
        while True:
            candidate = path.parent / f"{path.name}.bak.{i}"
            if not candidate.exists():
                bak = candidate
                break
            i += 1
    path.rename(bak)
    print(f"  ✅ 已备份: {path.name} → {bak.name}")


def migrate(workspace: Path) -> None:
    nimbus_dir = workspace / ".nimbus"
    global_memo = nimbus_dir / "memo_global.md"

    if not global_memo.exists():
        print(f"❌ 未找到 {global_memo}，跳过迁移。")
        return

    print(f"\n{'='*60}")
    print(f"  NimFS Memo 迁移脚本")
    print(f"{'='*60}")
    print(f"  工作区  : {workspace}")
    print(f"  源文件  : {global_memo}")
    print()

    # 1. 读取 memo_global.md
    content = global_memo.read_text(encoding="utf-8")
    print(f"  📄 读取 memo_global.md ({len(content)} 字节)")

    # 2. 按 H2 章节拆分
    sections = split_into_sections(content)
    if not sections:
        print("  ⚠️  未找到任何有效内容章节，检查文件格式。")
        return

    print(f"  📑 拆分为 {len(sections)} 个章节:\n")
    for i, (title, body) in enumerate(sections, 1):
        print(f"     [{i}] {title} ({len(body)} 字符)")
    print()

    # 3. 初始化 NimFSManager
    manager = NimFSManager(workspace)
    print(f"  🗂️  NimFS 根目录: {manager.project_root}")
    print()

    # 4. 逐节写入 Memory 分区
    written_ids = []
    for i, (title, body) in enumerate(sections, 1):
        category = classify_section(title, body)
        # 全局 memo 的内容 → global scope
        scope = MemoryScope.GLOBAL
        summary = generate_summary(title, body)
        tags = make_tags(title, category)

        print(f"  ✍️  [{i}/{len(sections)}] 写入: {title!r}")
        print(f"       类别: {category.value} | 标签: {tags}")

        memory_id = manager.write_memory(
            category=category,
            title=title,
            content=body,
            summary=summary,
            confidence=0.9,
            source="migrate_memo_to_nimfs",
            tags=tags,
            scope=scope,
        )
        written_ids.append(memory_id)
        print(f"       memory_id: {memory_id}")
    print()

    # 5. 备份 memo_global.md
    print("  🔒 开始备份旧 memo 文件...")
    backup_file(global_memo)

    # 6. 备份当前进程/会话的 memo_{session_id}.md
    #    匹配 memo_proc-*.md 和 memo_chat-*.md
    session_patterns = [
        str(nimbus_dir / "memo_proc-*.md"),
        str(nimbus_dir / "memo_chat-*.md"),
    ]
    session_memos = []
    for pattern in session_patterns:
        session_memos.extend(glob.glob(pattern))

    if session_memos:
        print(f"\n  🔒 发现 {len(session_memos)} 个 session memo 文件，逐一备份...")
        for memo_path_str in sorted(session_memos):
            memo_path = Path(memo_path_str)
            backup_file(memo_path)
    else:
        print("  ℹ️  未发现 session memo 文件（memo_proc-*.md / memo_chat-*.md）。")

    # 7. 打印汇总
    print(f"\n{'='*60}")
    print(f"  ✅ 迁移完成！")
    print(f"     写入 Memory 条目数  : {len(written_ids)}")
    print(f"     Memory 存储路径     : {manager.memory_root}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="迁移 memo_global.md 到 NimFS Memory 分区")
    parser.add_argument(
        "--workspace",
        type=str,
        default=str(_WORKSPACE),
        help="Nimbus 工作区路径（默认自动检测）",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.exists():
        print(f"❌ 工作区路径不存在: {workspace}")
        sys.exit(1)

    migrate(workspace)


if __name__ == "__main__":
    main()
