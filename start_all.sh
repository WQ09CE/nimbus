#!/bin/bash

# Kill existing processes
echo "Stopping existing services..."
kill -9 $(lsof -t -i:3031) 2>/dev/null
kill -9 $(lsof -t -i:4096) 2>/dev/null
kill -9 $(lsof -t -i:3000) 2>/dev/null

# Start Bridge
echo "Starting Bridge (3031)..."
cd bridge && nohup npm start > ../bridge.log 2>&1 &
BRIDGE_PID=$!
echo "Bridge PID: $BRIDGE_PID"
cd ..

# Wait for bridge
sleep 2

# Start AgentOS
echo "Starting AgentOS (4096)..."
# Using python -m nimbus.cli.main to avoid path issues
nohup python3 -m nimbus.cli.main serve --port 4096 > agentos.log 2>&1 &
AGENT_PID=$!
echo "AgentOS PID: $AGENT_PID"

# Wait for AgentOS
sleep 2

# Start Web UI
echo "Starting Web UI (3000)..."
cd web-ui && nohup npm run dev > ../webui.log 2>&1 &
WEBUI_PID=$!
echo "Web UI PID: $WEBUI_PID"

echo "All services started."
echo "Bridge Log: tail -f bridge.log"
echo "AgentOS Log: tail -f agentos.log"
echo "Web UI Log: tail -f webui.log"
