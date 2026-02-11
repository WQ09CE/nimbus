#!/usr/bin/env python3
"""NimbusHealthCheck — 项目健康检查"""

import argparse
import subprocess
import sys
from pathlib import Path


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


def run_cmd(cmd, cwd, timeout=30):
    """Run a command and return (success, output)."""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired:
        return False, f"[Timeout after {timeout}s]"
    except Exception as e:
        return False, f"[Error: {e}]"


def check_critical_files(root):
    """Check that critical files exist."""
    print("\n## 关键文件完整性")
    critical = [
        "pyproject.toml",
        "src/nimbus/__init__.py",
        "src/nimbus/agentos.py",
        "src/nimbus/tools/base.py",
        "src/nimbus/tools/composite.py",
        "src/nimbus/tools/read.py",
        "src/nimbus/tools/write.py",
        "src/nimbus/tools/edit.py",
        "src/nimbus/tools/bash.py",
        "src/nimbus/tools/memo.py",
        "src/nimbus/orchestration/prompts.py",
        "src/nimbus/orchestration/tools.py",
        "src/nimbus/orchestration/dispatch_tool.py",
        "src/nimbus/orchestration/review_tool.py",
        "src/nimbus/skills/manager.py",
        "src/nimbus/skills/loader.py",
        "src/nimbus/skills/tools.py",
        "src/nimbus/server/session_v2.py",
        "src/nimbus/os/gate.py",
        "src/nimbus/core/runtime/vcpu.py",
        "src/nimbus/core/memory/mmu.py",
    ]
    
    ok_count = 0
    for f in critical:
        path = root / f
        exists = path.exists()
        status = "✅" if exists else "❌"
        if exists:
            ok_count += 1
        print(f"  {status} {f}")
    
    print(f"\n  结果: {ok_count}/{len(critical)} 文件存在")
    return ok_count == len(critical)


def check_imports(root):
    """Check that core nimbus modules can be imported."""
    print("\n## Import 检查")
    
    imports = [
        ("nimbus", "包入口"),
        ("nimbus.agentos", "AgentOS 主模块"),
        ("nimbus.tools.base", "ToolRegistry/ToolDefinition"),
        ("nimbus.tools.composite", "CompositeToolRegistry"),
        ("nimbus.orchestration.prompts", "PromptManager"),
        ("nimbus.orchestration.dispatch_tool", "DispatchTool"),
        ("nimbus.skills.manager", "SkillManager"),
        ("nimbus.core.runtime.vcpu", "vCPU"),
        ("nimbus.core.memory.mmu", "MMU"),
    ]
    
    ok_count = 0
    for module, desc in imports:
        cmd = f'{sys.executable} -c "import {module}"'
        success, output = run_cmd(cmd, root, timeout=10)
        status = "✅" if success else "❌"
        if success:
            ok_count += 1
        else:
            desc += f" — {output[:80]}"
        print(f"  {status} import {module}  ({desc})")
    
    print(f"\n  结果: {ok_count}/{len(imports)} 模块可导入")
    return ok_count == len(imports)


def check_tool_categories(root):
    """Check that all registered tools have a category."""
    print("\n## 工具分类检查")
    
    import tempfile
    check_code = (
        "from nimbus.tools.base import ToolRegistry, ToolDefinition\n"
        "r = ToolRegistry()\n"
        "for n in ['Read','Write','Edit','Bash']:\n"
        "    r.register(ToolDefinition(name=n, description=n, category='core'), lambda: None)\n"
        "s = r.get_categories_summary()\n"
        "for c, t in s.items():\n"
        "    if t: print('  ' + c + ': ' + ', '.join(t))\n"
        "print('OK')\n"
    )
    tmp = Path(root) / ".nimbus" / "_health_check_tmp.py"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(check_code)
    cmd = f'{sys.executable} {tmp}'
    success, output = run_cmd(cmd, root, timeout=10)
    if success:
        print(output)
        print("  ✅ ToolCategory 系统正常")
    else:
        print(f"  ❌ ToolCategory 系统异常: {output[:200]}")
    return success


def check_tests(root):
    """Run pytest."""
    print("\n## 测试运行")
    
    # Quick: only unit tests, skip slow/e2e
    cmd = f"{sys.executable} -m pytest tests/test_tools_base.py tests/test_skill_integration_v2.py -x -q --tb=short 2>&1 | tail -10"
    success, output = run_cmd(cmd, root, timeout=60)
    print(f"  {output}")
    status = "✅ 测试通过" if success else "❌ 测试失败"
    print(f"\n  {status}")
    return success


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", default="quick", choices=["quick", "test", "full"],
                        help="检查范围: quick, test, full")
    args = parser.parse_args()

    root = find_project_root()
    print(f"# Nimbus Health Check")
    print(f"项目根目录: {root}")
    print(f"检查范围: {args.scope}")

    results = []

    # Always run
    results.append(("关键文件", check_critical_files(root)))
    results.append(("Import", check_imports(root)))
    results.append(("工具分类", check_tool_categories(root)))

    # test / full
    if args.scope in ("test", "full"):
        results.append(("测试", check_tests(root)))

    # Summary
    print(f"\n{'='*50}")
    print(f"## 健康检查总结")
    all_ok = True
    for name, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        if not ok:
            all_ok = False
        print(f"  {status}  {name}")

    print(f"\n{'🟢 项目健康' if all_ok else '🔴 存在问题，请检查上方详情'}")


if __name__ == "__main__":
    main()
