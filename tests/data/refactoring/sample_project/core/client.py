"""API Client implementation.

This module provides the main APIClient class for interacting with the backend API.
"""

from typing import Any, Dict, Optional


class APIClient:
    """Client for interacting with the backend API.

    This client provides methods for making API requests. The main method
    is old_api() which handles the communication with the server.

    Example:
        client = APIClient(base_url="https://api.example.com")
        result = client.old_api("/users", method="GET")
    """

    def __init__(self, base_url: str, api_key: Optional[str] = None):
        """Initialize the API client.

        Args:
            base_url: The base URL for the API.
            api_key: Optional API key for authentication.
        """
        self.base_url = base_url
        self.api_key = api_key
        self._session = None

    def old_api(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Make an API request to the specified endpoint.

        This is the primary method for making API calls. It handles
        authentication, request formatting, and response parsing.

        Args:
            endpoint: The API endpoint (e.g., "/users").
            method: HTTP method (GET, POST, PUT, DELETE).
            data: Optional request body data.
            headers: Optional additional headers.

        Returns:
            The parsed JSON response as a dictionary.

        Raises:
            APIError: If the request fails.
        """
        url = f"{self.base_url}{endpoint}"
        request_headers = {"Content-Type": "application/json"}

        if self.api_key:
            request_headers["Authorization"] = f"Bearer {self.api_key}"

        if headers:
            request_headers.update(headers)

        # Simulate API call (in real implementation, would use requests/aiohttp)
        return {
            "status": "success",
            "url": url,
            "method": method,
            "data": data,
        }

    def connect(self) -> bool:
        """Establish connection to the API server.

        Returns:
            True if connection is successful.
        """
        # Test connection using old_api
        response = self.old_api("/health", method="GET")
        return response.get("status") == "success"

    def disconnect(self) -> None:
        """Close the connection to the API server."""
        self._session = None
