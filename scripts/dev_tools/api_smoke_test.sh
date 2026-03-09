#!/bin/bash
set -e

echo "1. Creating session..."
SESSION_RES=$(curl -s -X POST http://localhost:4096/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "API Test"}')
SESSION_ID=$(echo $SESSION_RES | grep -o '"id":"[^"]*' | cut -d'"' -f4)

if [ -z "$SESSION_ID" ]; then
    echo "Failed to create session: $SESSION_RES"
    exit 1
fi
echo "Session created: $SESSION_ID"

echo "2. Sending chat message requiring a tool (List files in current directory)..."
curl -i -s -N -X POST http://localhost:4096/api/v1/sessions/$SESSION_ID/chat \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"content": "Please call a tool to list files in the current directory"}'
