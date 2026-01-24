# OpenCode TUI Integration Test Report

## Test Date
2026-01-24

## Summary
Tested OpenCode TUI connecting to Nimbus Server with OpenCode compatibility layer. The connection was attempted but failed due to missing API endpoints.

## Environment Setup

### 1. OpenCode CLI Status
```bash
$ which opencode
(not in PATH)

$ ~/.opencode/bin/opencode --version
1.1.12
```

**Status**: OpenCode CLI installed at `~/.opencode/bin/opencode`

### 2. Nimbus Server Status
```bash
$ curl http://localhost:8080/health
{"healthy":true}
```

**Status**: Nimbus server running on port 8080

### 3. OpenCode API Endpoints Test
```bash
$ curl http://localhost:8080/session
[]

$ curl http://localhost:8080/sessions
{"detail":"Not Found"}
```

**Status**: `/session` endpoint exists (returns empty array), but `/sessions` returns 404

## Integration Test

### Test Command
```bash
~/.opencode/bin/opencode attach http://localhost:8080
```

### Test Result
**FAILED** - TUI crashed with JavaScript error

### Error Details
```
TypeError: undefined is not an object (evaluating 'Object.keys(sync.data.mcp)')
    at keys (unknown)
    at <anonymous> (src/cli/cmd/tui/routes/home.tsx:36:39)
    at runComputation (../../node_modules/.bun/solid-js@1.9.10/node_modules/solid-js/dist/dev.js:742:22)
    ...
```

### Root Cause Analysis
OpenCode TUI expects certain data structures in the server response:
1. **MCP (Model Context Protocol) data**: The error shows TUI is trying to access `sync.data.mcp` which is undefined
2. **Config sync**: OpenCode TUI expects configuration data that includes MCP server information

## API Compatibility Gap

Based on the error and OpenCode's expected behavior, the following endpoints/data are missing or incomplete:

| Expected by OpenCode | Current Nimbus Status | Gap |
|----------------------|----------------------|-----|
| `/config` with MCP data | Returns minimal config without MCP | MCP field missing |
| `/mcp` endpoint | Not implemented | Missing endpoint |
| Sync data structure | Not implemented | Missing data sync |

## Nimbus OpenCode API Implementation Status

### Implemented Endpoints
```python
# Session management
GET /session              # List sessions - ✓
POST /session             # Create session - ✓
GET /session/{id}         # Get session - ✓
DELETE /session/{id}      # Delete session - ✓

# Messages
GET /session/{id}/message     # Get messages - ✓
POST /session/{id}/message    # Send message (SSE) - ✓
POST /session/{id}/abort      # Abort session - ✓

# Events
GET /event                    # Global event stream - ✓

# Permissions
POST /permission/{id}         # Respond to permission - ✓

# Health & Info
GET /health                   # Health check - ✓
GET /                         # Root endpoint - ✓

# Config & Providers
GET /config                   # Get config - ✓ (incomplete)
GET /provider                 # List providers - ✓
GET /agent                    # List agents - ✓
```

### Missing Endpoints (Required by OpenCode TUI)
```python
GET /mcp                      # MCP server list - ✗
POST /mcp                     # Register MCP server - ✗
GET /config/mcp               # MCP configuration - ✗
GET /sync                     # Data sync endpoint - ✗
```

## Recommendations

### 1. Add MCP Support to `/config` Endpoint
**File**: `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/server/compat/opencode.py:583`

```python
@router.get("/config")
async def get_config():
    """Get current configuration."""
    return {
        "model": {"default": "nimbus"},
        "provider": {"default": "nimbus"},
        "theme": "dark",
        "mcp": {}  # ADD THIS - Empty MCP config
    }
```

### 2. Add MCP Endpoint Stubs
Add to opencode.py:

```python
@router.get("/mcp")
async def list_mcp_servers():
    """List MCP servers (stub)."""
    return []

@router.post("/mcp")
async def register_mcp_server(data: dict):
    """Register MCP server (stub)."""
    return {"id": "stub", "status": "not_supported"}
```

### 3. Add Sync Endpoint
```python
@router.get("/sync")
async def get_sync_data():
    """Get sync data for TUI."""
    return {
        "data": {
            "mcp": {},
            "config": {},
            "sessions": []
        }
    }
```

## Next Steps

1. **Quick Fix**: Add `mcp: {}` to `/config` response
2. **Short Term**: Implement MCP endpoint stubs
3. **Long Term**: Consider full MCP protocol support
4. **Testing**: Re-test with minimal MCP data

## Test Evidence Files

- Server implementation: `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/server/compat/opencode.py`
- OpenCode binary: `~/.opencode/bin/opencode`
- Server health: http://localhost:8080/health (responding)

## Conclusion

**Integration Status**: BLOCKED

The OpenCode TUI cannot currently connect to Nimbus Server due to missing MCP-related data in the API responses. The core session/message APIs are implemented correctly, but the TUI initialization requires MCP configuration data.

**Blocking Issue**: Missing `mcp` field in `/config` response causing JavaScript TypeError

**Estimated Fix Effort**: 30 minutes (add MCP stubs to config and create stub endpoints)
