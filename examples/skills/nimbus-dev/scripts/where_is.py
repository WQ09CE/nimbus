#!/usr/bin/env python3
"""NimbusWhereIs — 在 Nimbus 代码库中智能定位概念/类/函数"""

import argparse
import os
import re
from pathlib import Path
from collections import defaultdict


def find_project_root():
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent.parent.parent,
    ]
    for p in candidates:
        if (p / "pyproject.toml").exists() and (p / "src" / "nimbus").exists():
            return p
    p = Path.cwd()
    for _ in range(10):
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path.cwd()


# Concept → search hints mapping
# Helps translate high-level concepts to grep-able patterns
CONCEPT_MAP = {
    # Architecture
    "vcpu": ["class VCPU", "vcpu.py", "Think-Act-Observe"],
    "mmu": ["class MMU", "mmu.py", "MemoryManagement"],
    "gate": ["class KernelGate", "gate.py", "syscall"],
    "scheduler": ["class Scheduler", "scheduler.py", "DAG"],
    "process": ["class Process", "spawn("],
    
    # Tools
    "tool registry": ["class ToolRegistry", "ToolDefinition", "register("],
    "tool definition": ["class ToolDefinition", "ToolParameter", "category"],
    "composite": ["class CompositeToolRegistry", "composite.py"],
    "tool category": ["ToolCategory", "list_by_category", "get_categories_summary"],
    "read tool": ["async def read_file", "READ_TOOL"],
    "write tool": ["async def write_file", "WRITE_TOOL"],
    "edit tool": ["async def edit_file", "EDIT_TOOL", "old_text"],
    "bash tool": ["async def run_bash", "BASH_TOOL"],
    "memo": ["class MemoManager", "MEMO_TOOL_DEF", "memo.py"],
    
    # Orchestration
    "dispatch": ["class DispatchTool", "async def dispatch", "DISPATCH_TOOL_DEF"],
    "verify": ["async def verify", "run_verify_checks", "VERIFY_TOOL_DEF"],
    "review": ["class ReviewTool", "ReviewCommittee", "REVIEW_TOOL_DEF"],
    "prompt": ["class PromptManager", "CORE_INSTRUCTIONS", "EXECUTOR_INSTRUCTIONS"],
    "corebash": ["CoreBash", "register_core_bash", "is_command_readonly"],
    "workspace diff": ["class WorkspaceDiff", "take_snapshot", "diff_snapshots"],
    
    # Skills
    "skill": ["class SkillManager", "SKILL.md", "SkillManifest"],
    "skill loader": ["load_skill_manifest", "SkillLoaderError"],
    "skill tool": ["class ScriptTool", "ScriptTool"],
    "reload skills": ["ReloadSkills", "reload_skills"],
    
    # Server
    "session": ["class SessionManagerV2", "session_v2.py", "create_session"],
    "api": ["api.py", "@router", "FastAPI"],
    "sse": ["class SSEHub", "sse.py"],
    
    # AgentOS
    "agentos": ["class AgentOS", "create_agent_os"],
    "profile": ["class AgentProfile", "profile.py"],
    "compaction": ["class CompactionEngine", "compaction.py"],
}


def search_files(src_dir, patterns, max_results=20):
    """Search for patterns in Python files. Returns list of (file, line_no, line)."""
    results = []
    
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fn in files:
            if not fn.endswith('.py'):
                continue
            filepath = Path(root) / fn
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    for line_no, line in enumerate(f, 1):
                        for pattern in patterns:
                            if pattern.lower() in line.lower():
                                rel = filepath.relative_to(src_dir.parent.parent)  # relative to project root
                                results.append((str(rel), line_no, line.rstrip(), pattern))
                                break  # Don't match same line multiple times
            except:
                continue
    
    return results[:max_results]


def search_definitions(src_dir, query, max_results=20):
    """Search for class/function definitions matching query."""
    results = []
    query_lower = query.lower().replace(" ", "").replace("_", "")
    
    # Patterns for definitions
    def_patterns = [
        (r'^class\s+(\w+)', "class"),
        (r'^(?:async\s+)?def\s+(\w+)', "func"),
        (r'^(\w+)\s*=\s*', "var"),  # Top-level assignments
    ]
    
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fn in files:
            if not fn.endswith('.py'):
                continue
            filepath = Path(root) / fn
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    for line_no, line in enumerate(f, 1):
                        for pattern, kind in def_patterns:
                            m = re.match(pattern, line)
                            if m:
                                name = m.group(1)
                                name_normalized = name.lower().replace("_", "")
                                if query_lower in name_normalized or name_normalized in query_lower:
                                    rel = filepath.relative_to(src_dir.parent.parent)
                                    results.append((str(rel), line_no, line.rstrip(), kind))
            except:
                continue
    
    # Sort: exact matches first, then by type (class > func > var)
    type_order = {"class": 0, "func": 1, "var": 2}
    results.sort(key=lambda r: (type_order.get(r[3], 9), r[0]))
    return results[:max_results]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True, help="要查找的内容")
    args = parser.parse_args()

    root = find_project_root()
    src = root / "src" / "nimbus"
    query = args.query.strip()

    print(f"# NimbusWhereIs: \"{query}\"")
    print(f"搜索范围: {src}")

    # 1. Check concept map
    query_lower = query.lower().replace("-", " ").replace("_", " ")
    concept_patterns = None
    for concept, patterns in CONCEPT_MAP.items():
        if query_lower in concept or concept in query_lower:
            concept_patterns = patterns
            print(f"\n## 概念匹配: {concept}")
            break

    # 2. Search definitions (class/function names)
    print(f"\n## 定义搜索 (class/def)")
    defs = search_definitions(src, query)
    if defs:
        for filepath, line_no, line, kind in defs:
            tag = {"class": "📦", "func": "⚡", "var": "📌"}.get(kind, "")
            print(f"  {tag} {filepath}:{line_no}  →  {line.strip()}")
    else:
        print(f"  (未找到匹配的定义)")

    # 3. Search by concept patterns or raw query
    print(f"\n## 代码搜索")
    if concept_patterns:
        results = search_files(src, concept_patterns)
    else:
        # Use the query itself as pattern
        results = search_files(src, [query])
    
    if results:
        # Group by file
        by_file = defaultdict(list)
        for filepath, line_no, line, pattern in results:
            by_file[filepath].append((line_no, line))
        
        for filepath, matches in sorted(by_file.items()):
            print(f"\n  📄 {filepath}")
            for line_no, line in matches[:5]:
                # Truncate long lines
                display = line.strip()
                if len(display) > 100:
                    display = display[:97] + "..."
                print(f"     L{line_no}: {display}")
            if len(matches) > 5:
                print(f"     ... 及其他 {len(matches)-5} 处")
    else:
        print(f"  (未找到匹配内容)")
    
    total = len(defs) + (len(results) if results else 0)
    print(f"\n共找到 {total} 处匹配")


if __name__ == "__main__":
    main()
