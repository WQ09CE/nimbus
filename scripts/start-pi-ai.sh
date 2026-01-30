#!/bin/bash
# 启动 pi-ai HTTP 服务
# 
# Usage:
#   ./scripts/start-pi-ai.sh          # 前台运行
#   ./scripts/start-pi-ai.sh &        # 后台运行
#   PI_AI_PORT=8080 ./scripts/start-pi-ai.sh  # 自定义端口

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# 默认端口
PORT=${PI_AI_PORT:-3031}

echo "Starting pi-ai HTTP server on port $PORT..."
echo "Press Ctrl+C to stop"
echo ""

# 检查是否安装了依赖
if [ ! -d "node_modules/@mariozechner/pi-ai" ]; then
    echo "Installing @mariozechner/pi-ai..."
    npm install @mariozechner/pi-ai
fi

# 启动服务
exec npx tsx bridge/pi-ai-server.ts
