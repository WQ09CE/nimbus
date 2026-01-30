#!/bin/bash
# 启动 Pi TUI + Nimbus Context Filter
#
# Usage:
#   ./run-pi-nimbus.sh
#
# Nimbus 会自动过滤失败的 tool calls，保持上下文干净

cd "$(dirname "$0")"

echo "Pi + Nimbus Context Filter"
echo "=========================="
echo "失败的 tool calls 会被自动过滤"
echo "命令: /gc 查看过滤状态"
echo ""

pi -e ./pi-extension/nimbus-context-filter.ts
