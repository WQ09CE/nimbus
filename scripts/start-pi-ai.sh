#!/bin/bash
# 启动 pi-ai HTTP 服务
# 
# Usage:
#   ./scripts/start-pi-ai.sh           # 前台运行
#   ./scripts/start-pi-ai.sh --daemon  # 后台运行 (daemon 模式)
#   PI_AI_PORT=8080 ./scripts/start-pi-ai.sh  # 自定义端口

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# 默认端口
PORT=${PI_AI_PORT:-3031}
LOG_FILE="/tmp/pi-ai-server.log"

# 检查是否安装了依赖
if [ ! -d "node_modules/@mariozechner/pi-ai" ]; then
    echo "Installing @mariozechner/pi-ai..."
    npm install @mariozechner/pi-ai
fi

# Daemon 模式
if [ "$1" = "--daemon" ] || [ "$1" = "-d" ]; then
    # 检查是否已经在运行
    if lsof -i :$PORT >/dev/null 2>&1; then
        echo "pi-ai server already running on port $PORT"
        exit 0
    fi
    
    echo "Starting pi-ai server in daemon mode..."
    # 使用 setsid 彻底脱离终端
    setsid npx tsx bridge/pi-ai-server.ts > "$LOG_FILE" 2>&1 &
    
    # 等待启动
    sleep 2
    
    if curl -s "http://localhost:$PORT/health" >/dev/null 2>&1; then
        echo "✅ pi-ai server started on port $PORT"
        echo "   Log: $LOG_FILE"
    else
        echo "❌ Failed to start. Check $LOG_FILE"
        exit 1
    fi
    exit 0
fi

# 前台模式
echo "Starting pi-ai HTTP server on port $PORT..."
echo "Press Ctrl+C to stop"
echo ""
exec npx tsx bridge/pi-ai-server.ts
