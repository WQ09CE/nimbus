#!/bin/bash
# 启动 Nimbus Server + Web UI
#
# Usage:
#   ./run-web.sh

set -e

echo "🚀 Starting Nimbus with Web UI"
echo "================================"
echo ""

# Check if nimbus is installed
if ! command -v nimbus &> /dev/null; then
    echo "❌ Nimbus not installed. Installing..."
    pip install -e .
fi

# Check if web-ui dependencies are installed
if [ ! -d "web-ui/node_modules" ]; then
    echo "📦 Installing Web UI dependencies..."
    cd web-ui && npm install && cd ..
fi

# Start nimbus server in background
echo "🔧 Starting Nimbus server @ :4096..."
nimbus serve --host 0.0.0.0 --port 4096 &
NIMBUS_PID=$!

# Wait for server to be ready
sleep 3

# Start web UI
echo "🌐 Starting Web UI @ :3000..."
cd web-ui
npm run dev &
WEB_PID=$!

echo ""
echo "✅ Running!"
echo "   Nimbus Server: http://0.0.0.0:4096"
echo "   Web UI:        http://0.0.0.0:3000"
echo ""
echo "Press Ctrl+C to stop both services"

# Trap Ctrl+C to kill both processes
trap "echo '🛑 Stopping...'; kill $NIMBUS_PID $WEB_PID 2>/dev/null; exit" INT

# Wait for either process to exit
wait
