"""Tests for the APIClient class.

These tests verify that the APIClient.new_api() method works correctly.
"""

import pytest
from ..core.client import APIClient


class TestAPIClient:
    """Tests for APIClient functionality."""

    @pytest.fixture
    def client(self):
        """Create a test client."""
        return APIClient(base_url="https://api.test.com", api_key="test-key")

    def test_new_api_get_request(self, client):
        """Test new_api with GET request."""
        response = client.new_api("/users", method="GET")

        assert response["status"] == "success"
        assert response["method"] == "GET"
        assert "/users" in response["url"]

    def test_new_api_post_request(self, client):
        """Test new_api with POST request."""
        data = {"name": "test", "value": 123}
        response = client.new_api("/items", method="POST", data=data)

        assert response["status"] == "success"
        assert response["method"] == "POST"
        assert response["data"] == data

    def test_new_api_with_custom_headers(self, client):
        """Test new_api with custom headers."""
        headers = {"X-Custom-Header": "test-value"}
        response = client.new_api("/test", headers=headers)

        assert response["status"] == "success"

    def test_new_api_put_request(self, client):
        """Test new_api with PUT request."""
        data = {"id": 1, "updated": True}
        response = client.new_api("/items/1", method="PUT", data=data)

        assert response["status"] == "success"
        assert response["method"] == "PUT"

    def test_new_api_delete_request(self, client):
        """Test new_api with DELETE request."""
        response = client.new_api("/items/1", method="DELETE")

        assert response["status"] == "success"
        assert response["method"] == "DELETE"

    def test_connect_uses_new_api(self, client):
        """Test that connect() uses new_api internally."""
        result = client.connect()
        assert result is True

    def test_client_initialization(self, client):
        """Test client initialization."""
        assert client.base_url == "https://api.test.com"
        assert client.api_key == "test-key"

    def test_client_without_api_key(self):
        """Test client without API key."""
        client = APIClient(base_url="https://api.test.com")
        response = client.new_api("/public")

        assert response["status"] == "success"


class TestAPIClientEdgeCases:
    """Edge case tests for APIClient."""

    def test_new_api_empty_endpoint(self):
        """Test new_api with empty endpoint."""
        client = APIClient(base_url="https://api.test.com")
        response = client.new_api("")

        assert response["status"] == "success"

    def test_new_api_none_data(self):
        """Test new_api with None data."""
        client = APIClient(base_url="https://api.test.com")
        response = client.new_api("/test", data=None)

        assert response["data"] is None
