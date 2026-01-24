"""Integration tests for OpenCode compatibility API.

This test suite verifies that Nimbus server provides OpenCode-compatible
endpoints that can be consumed by OpenCode TUI.
"""

import json
import pytest
import httpx
from typing import Dict, Any


BASE_URL = "http://localhost:8080"


class TestOpenCodeHealthEndpoints:
    """Test health and info endpoints."""

    def test_root_endpoint(self):
        """Test root endpoint returns status."""
        response = httpx.get(f"{BASE_URL}/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["server"] == "nimbus"

    def test_health_endpoint(self):
        """Test health check endpoint."""
        response = httpx.get(f"{BASE_URL}/health")
        assert response.status_code == 200
        data = response.json()
        assert data["healthy"] is True

    def test_global_health_endpoint(self):
        """Test global health endpoint with version."""
        response = httpx.get(f"{BASE_URL}/global/health")
        assert response.status_code == 200
        data = response.json()
        assert data["healthy"] is True
        assert "version" in data


class TestOpenCodeConfigEndpoints:
    """Test configuration endpoints."""

    def test_config_endpoint(self):
        """Test config endpoint returns required fields."""
        response = httpx.get(f"{BASE_URL}/config")
        assert response.status_code == 200
        data = response.json()
        assert "model" in data
        assert "provider" in data
        # FIXME: OpenCode TUI requires 'mcp' field
        # assert "mcp" in data

    def test_config_providers_endpoint(self):
        """Test config providers endpoint."""
        response = httpx.get(f"{BASE_URL}/config/providers")
        assert response.status_code == 200
        data = response.json()
        # OpenCode TUI expects {providers: [], default: {}}
        assert "providers" in data
        assert "default" in data
        assert isinstance(data["providers"], list)
        assert len(data["providers"]) > 0


class TestOpenCodeProviderEndpoints:
    """Test provider and agent endpoints."""

    def test_list_providers(self):
        """Test listing providers."""
        response = httpx.get(f"{BASE_URL}/provider")
        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert "defaults" in data
        assert "connected" in data
        assert len(data["providers"]) > 0
        assert data["providers"][0]["id"] == "nimbus"

    def test_list_agents(self):
        """Test listing agents."""
        response = httpx.get(f"{BASE_URL}/agent")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert data[0]["id"] == "nimbus"


class TestOpenCodeSessionEndpoints:
    """Test session management endpoints."""

    def test_list_sessions_empty(self):
        """Test listing sessions when none exist."""
        response = httpx.get(f"{BASE_URL}/session")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_create_session(self):
        """Test creating a new session."""
        payload = {
            "directory": "/tmp/test",
            "title": "Test Session"
        }
        response = httpx.post(f"{BASE_URL}/session", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert "title" in data
        assert "time" in data
        assert "created" in data["time"]
        assert "updated" in data["time"]
        return data["id"]

    def test_get_session(self):
        """Test getting session details."""
        # First create a session
        session_id = self.test_create_session()

        # Then get it
        response = httpx.get(f"{BASE_URL}/session/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id

    def test_delete_session(self):
        """Test deleting a session."""
        # First create a session
        session_id = self.test_create_session()

        # Then delete it
        response = httpx.delete(f"{BASE_URL}/session/{session_id}")
        assert response.status_code == 204

        # Verify it's gone
        response = httpx.get(f"{BASE_URL}/session/{session_id}")
        assert response.status_code == 404


class TestOpenCodePathEndpoints:
    """Test path and environment endpoints."""

    def test_get_path(self):
        """Test getting path information."""
        response = httpx.get(f"{BASE_URL}/path")
        assert response.status_code == 200
        data = response.json()
        assert "cwd" in data
        assert "home" in data
        assert "config" in data

    def test_get_vcs(self):
        """Test getting VCS information."""
        response = httpx.get(f"{BASE_URL}/vcs")
        assert response.status_code == 200
        data = response.json()
        assert "type" in data

    def test_get_lsp(self):
        """Test getting LSP information."""
        response = httpx.get(f"{BASE_URL}/lsp")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestOpenCodeProjectEndpoints:
    """Test project management endpoints."""

    def test_list_projects(self):
        """Test listing projects."""
        response = httpx.get(f"{BASE_URL}/project")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_current_project(self):
        """Test getting current project."""
        response = httpx.get(f"{BASE_URL}/project/current")
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert "path" in data
        assert "name" in data


@pytest.mark.skip(reason="MCP endpoints not yet implemented")
class TestOpenCodeMCPEndpoints:
    """Test MCP (Model Context Protocol) endpoints."""

    def test_list_mcp_servers(self):
        """Test listing MCP servers."""
        response = httpx.get(f"{BASE_URL}/mcp")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


if __name__ == "__main__":
    print("OpenCode API Compatibility Tests")
    print("=" * 50)
    print(f"Testing server at: {BASE_URL}")
    print()

    # Quick connectivity check
    try:
        response = httpx.get(f"{BASE_URL}/health", timeout=2)
        if response.status_code == 200:
            print("✓ Server is running and healthy")
        else:
            print("✗ Server returned unexpected status:", response.status_code)
            exit(1)
    except httpx.ConnectError:
        print("✗ Cannot connect to server. Is it running?")
        print("  Start with: uv run nimbus serve --port 8080")
        exit(1)

    print()
    print("Run with pytest for full test suite:")
    print("  pytest tests/test_opencode_api.py -v")
