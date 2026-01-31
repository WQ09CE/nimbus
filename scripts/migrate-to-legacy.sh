#!/bin/bash
#
# 将废弃模块移到 legacy 目录
#
# Usage:
#   ./scripts/migrate-to-legacy.sh --dry-run  # 预览
#   ./scripts/migrate-to-legacy.sh            # 执行
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$PROJECT_DIR/src/nimbus"
LEGACY_DIR="$SRC_DIR/legacy"

DRY_RUN=false
if [ "$1" = "--dry-run" ] || [ "$1" = "-n" ]; then
    DRY_RUN=true
    echo "🔍 DRY RUN MODE - 不会实际执行"
    echo ""
fi

# ============================================================================
# 工具函数
# ============================================================================

move_module() {
    local src="$1"
    local dst="$2"
    
    if [ -e "$src" ]; then
        if [ "$DRY_RUN" = true ]; then
            echo "  [MOVE] $src → $dst"
        else
            mkdir -p "$(dirname "$dst")"
            mv "$src" "$dst"
            echo "  ✓ $src → $dst"
        fi
    else
        echo "  [SKIP] $src (不存在)"
    fi
}

# ============================================================================
# Phase 1: 创建 legacy 目录结构
# ============================================================================

echo "📁 Phase 1: 创建 legacy 目录结构"
echo "═══════════════════════════════════════"

if [ "$DRY_RUN" = false ]; then
    mkdir -p "$LEGACY_DIR"
    mkdir -p "$LEGACY_DIR/core"
    mkdir -p "$LEGACY_DIR/tools"
fi

# ============================================================================
# Phase 2: 移动整个模块
# ============================================================================

echo ""
echo "📦 Phase 2: 移动废弃模块"
echo "═══════════════════════════════════════"

# 完全废弃的模块
move_module "$SRC_DIR/kernel" "$LEGACY_DIR/kernel"
move_module "$SRC_DIR/skills" "$LEGACY_DIR/skills"
move_module "$SRC_DIR/domain" "$LEGACY_DIR/domain"
move_module "$SRC_DIR/acp" "$LEGACY_DIR/acp"
move_module "$SRC_DIR/services" "$LEGACY_DIR/services"
move_module "$SRC_DIR/apps" "$LEGACY_DIR/apps"
move_module "$SRC_DIR/tui" "$LEGACY_DIR/tui"
move_module "$SRC_DIR/storage" "$LEGACY_DIR/storage"
move_module "$SRC_DIR/llm" "$LEGACY_DIR/llm"

# ============================================================================
# Phase 3: 移动 core 中废弃的部分
# ============================================================================

echo ""
echo "📦 Phase 3: 移动 core 中废弃的部分"
echo "═══════════════════════════════════════"

# 保留 logging.py，移动其他
move_module "$SRC_DIR/core/planner" "$LEGACY_DIR/core/planner"
move_module "$SRC_DIR/core/runtime" "$LEGACY_DIR/core/runtime"
move_module "$SRC_DIR/core/task" "$LEGACY_DIR/core/task"
move_module "$SRC_DIR/core/agent.py" "$LEGACY_DIR/core/agent.py"
move_module "$SRC_DIR/core/agent_config.py" "$LEGACY_DIR/core/agent_config.py"
move_module "$SRC_DIR/core/agents_config.py" "$LEGACY_DIR/core/agents_config.py"
move_module "$SRC_DIR/core/memory.py" "$LEGACY_DIR/core/memory.py"
move_module "$SRC_DIR/core/checkpoint.py" "$LEGACY_DIR/core/checkpoint.py"
move_module "$SRC_DIR/core/context.py" "$LEGACY_DIR/core/context.py"
move_module "$SRC_DIR/core/executor.py" "$LEGACY_DIR/core/executor.py"
move_module "$SRC_DIR/core/factory.py" "$LEGACY_DIR/core/factory.py"
move_module "$SRC_DIR/core/permission.py" "$LEGACY_DIR/core/permission.py"
move_module "$SRC_DIR/core/tracing.py" "$LEGACY_DIR/core/tracing.py"
move_module "$SRC_DIR/core/vector_store.py" "$LEGACY_DIR/core/vector_store.py"
move_module "$SRC_DIR/core/runtime.py" "$LEGACY_DIR/core/runtime.py"

# ============================================================================
# Phase 4: 移动 tools 中废弃的部分
# ============================================================================

echo ""
echo "📦 Phase 4: 移动 tools 中废弃的部分"
echo "═══════════════════════════════════════"

# 保留: base.py, read.py, edit.py, grep.py, sandbox.py, __init__.py, glob.py, bash.py, write.py
move_module "$SRC_DIR/tools/subagent.py" "$LEGACY_DIR/tools/subagent.py"
move_module "$SRC_DIR/tools/websearch.py" "$LEGACY_DIR/tools/websearch.py"
move_module "$SRC_DIR/tools/webfetch.py" "$LEGACY_DIR/tools/webfetch.py"
move_module "$SRC_DIR/tools/search.py" "$LEGACY_DIR/tools/search.py"
move_module "$SRC_DIR/tools/filetree.py" "$LEGACY_DIR/tools/filetree.py"
move_module "$SRC_DIR/tools/batch.py" "$LEGACY_DIR/tools/batch.py"
move_module "$SRC_DIR/tools/resolver.py" "$LEGACY_DIR/tools/resolver.py"
move_module "$SRC_DIR/tools/middleware.py" "$LEGACY_DIR/tools/middleware.py"

# ============================================================================
# Phase 5: 创建 legacy __init__.py
# ============================================================================

echo ""
echo "📝 Phase 5: 创建 legacy __init__.py"
echo "═══════════════════════════════════════"

if [ "$DRY_RUN" = false ]; then
    cat > "$LEGACY_DIR/__init__.py" << 'EOF'
"""
Legacy modules - 已废弃，保留供参考

这些模块已被 v2 架构替代：
- kernel/ → v2/core/runtime/
- core/ → v2/core/
- skills/ → (已移除)
- acp/ → (已移除)
- tui/ → v2/tui/
- llm/ → v2/llm/ + v2/adapters/
- storage/ → (已移除)

不建议在新代码中使用这些模块。
"""
EOF
    echo "  ✓ 创建 $LEGACY_DIR/__init__.py"
fi

# ============================================================================
# 完成
# ============================================================================

echo ""
echo "═══════════════════════════════════════"

if [ "$DRY_RUN" = true ]; then
    echo "🔍 DRY RUN 完成 - 没有实际移动文件"
    echo ""
    echo "要执行迁移，运行:"
    echo "  ./scripts/migrate-to-legacy.sh"
else
    echo "✅ 迁移完成!"
    echo ""
    echo "⚠️  需要手动更新 import:"
    echo "   1. 检查 from nimbus.core.logging 的引用"
    echo "   2. 运行测试: pytest tests/test_v2_*.py -v"
    echo ""
fi
