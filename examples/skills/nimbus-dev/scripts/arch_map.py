#!/usr/bin/env python3
"""NimbusArchMap — 输出 Nimbus 项目架构全景图"""

import argparse
import os
from pathlib import Path
from collections import defaultdict

# Nimbus source root (relative to this script's expected execution context)
# The skill runs with cwd = skill directory, so we need to find the project root
def find_project_root():
    """Find nimbus project root by looking for pyproject.toml"""
    candidates = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent.parent.parent,  # examples/skills/nimbus-dev/scripts -> root
    ]
    for p in candidates:
        if (p / "pyproject.toml").exists() and (p / "src" / "nimbus").exists():
            return p
    # fallback: walk up from cwd
    p = Path.cwd()
    for _ in range(10):
        if (p / "pyproject.toml").exists():
            return p
        p = p.parent
    return Path.cwd()


MODULE_DESCRIPTIONS = {
    "core": "核心引擎 — vCPU, MMU, Scheduler, Session, Compaction",
    "tools": "工具系统 — ToolRegistry, ToolDefinition, Read/Write/Edit/Bash, Composite",
    "orchestration": "编排层 — Dispatch, Verify, ReviewCommittee, Prompts, WorkspaceDiff",
    "skills": "技能系统 — SKILL.md Loader, SkillManager, ScriptTool",
    "server": "HTTP 服务 — FastAPI, SessionV2, SSE, API routes",
    "os": "OS 抽象 — KernelGate (工具执行 + 权限隔离)",
    "adapters": "LLM 适配器 — pi-ai bridge, LLM factory",
    "cli": "CLI — nimbus serve 命令入口",
    "storage": "持久化 — SQLite session/message storage",
    "bridge": "Bridge — pi-ai HTTP/WebSocket 桥接",
    "agents": "Agent 模板 — 预定义 agent 配置",
    "data": "数据 — 静态资源/模板",
    "utils": "工具函数 — 通用辅助",
}


def count_lines(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return sum(1 for _ in f)
    except:
        return 0


def scan_module(module_path, module_name):
    """Scan a module directory and return stats."""
    files = []
    total_lines = 0
    for root, dirs, filenames in os.walk(module_path):
        # Skip __pycache__
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fn in filenames:
            if fn.endswith('.py'):
                fp = Path(root) / fn
                lines = count_lines(fp)
                total_lines += lines
                rel = fp.relative_to(module_path)
                files.append((str(rel), lines))
    
    files.sort(key=lambda x: -x[1])  # Sort by lines desc
    return {
        "name": module_name,
        "description": MODULE_DESCRIPTIONS.get(module_name, ""),
        "file_count": len(files),
        "total_lines": total_lines,
        "files": files,
    }


def print_module(mod, verbose=True):
    desc = mod["description"]
    print(f"\n## {mod['name']}/ — {desc}")
    print(f"   文件数: {mod['file_count']}  |  总行数: {mod['total_lines']:,}")
    
    if verbose and mod["files"]:
        print(f"   {'文件':<40} {'行数':>6}")
        print(f"   {'─'*40} {'─'*6}")
        for fn, lines in mod["files"][:10]:  # Top 10
            print(f"   {fn:<40} {lines:>6}")
        if len(mod["files"]) > 10:
            print(f"   ... 及其他 {len(mod['files']) - 10} 个文件")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--focus", default="all", help="聚焦模块: core, tools, orchestration, skills, server, all")
    args = parser.parse_args()

    root = find_project_root()
    src = root / "src" / "nimbus"
    
    if not src.exists():
        print(f"[Error] Nimbus source not found at {src}")
        return

    print(f"# Nimbus Architecture Map")
    print(f"项目根目录: {root}")
    print(f"源码目录: {src}")

    # Collect all modules
    modules = []
    for item in sorted(src.iterdir()):
        if item.is_dir() and item.name != '__pycache__':
            modules.append(scan_module(item, item.name))

    # Also count root files
    root_files = [(f.name, count_lines(f)) for f in src.glob("*.py")]
    root_lines = sum(l for _, l in root_files)

    # Filter by focus
    if args.focus != "all":
        focus_names = [f.strip() for f in args.focus.split(",")]
        modules = [m for m in modules if m["name"] in focus_names]
        if not modules:
            print(f"\n[Warning] 未找到模块: {args.focus}")
            print(f"可用模块: {', '.join(m['name'] for m in modules)}")
            return

    # Summary
    total_files = sum(m["file_count"] for m in modules) + len(root_files)
    total_lines = sum(m["total_lines"] for m in modules) + root_lines
    
    print(f"\n## 总览")
    print(f"模块数: {len(modules)}  |  Python 文件数: {total_files}  |  总代码行数: {total_lines:,}")
    
    # Bar chart
    print(f"\n## 代码量分布")
    max_lines = max((m["total_lines"] for m in modules), default=1)
    for mod in sorted(modules, key=lambda m: -m["total_lines"]):
        bar_len = int(30 * mod["total_lines"] / max_lines) if max_lines > 0 else 0
        bar = "█" * bar_len
        print(f"  {mod['name']:<16} {bar} {mod['total_lines']:>5}")

    # Module details
    print(f"\n## 模块详情")
    verbose = args.focus != "all"  # Show file details when focused
    for mod in sorted(modules, key=lambda m: -m["total_lines"]):
        print_module(mod, verbose=verbose or len(modules) <= 3)


if __name__ == "__main__":
    main()
