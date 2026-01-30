#!/bin/bash

# Kill existing processes
echo "Stopping Nimbus Server..."
pkill -f "nimbus serve" || true

echo "Stopping Web UI..."
pkill -f "next dev" || true

# Wait a moment
sleep 2

# Start Nimbus Server
echo "Starting Nimbus Server..."
nohup /opt/homebrew/Caskroom/miniconda/base/bin/nimbus serve --host 0.0.0.0 --port 4096 --log-level debug > nimbus.log 2>&1 &
NIMBUS_PID=$!
echo "Nimbus Server started with PID $NIMBUS_PID"

# Start Web UI
echo "Starting Web UI..."
cd /Users/wangqing/sourcecode/agent/agent-framework/nimbus/web-ui
nohup npm run dev -- -p 3000 -H 0.0.0.0 > webui.log 2>&1 &
WEBUI_PID=$!
echo "Web UI started with PID $WEBUI_PID"

echo "Done."
