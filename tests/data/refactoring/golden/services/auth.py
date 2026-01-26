"""Authentication service.

This module provides authentication-related functionality using the APIClient.
"""

from typing import Optional, Dict, Any
from ..core.client import APIClient


class AuthService:
    """Service for handling authentication operations.

    This service wraps the APIClient to provide authentication-specific
    functionality like login, logout, and token refresh.
    """

    def __init__(self, client: APIClient):
        """Initialize the auth service.

        Args:
            client: The APIClient instance to use for API calls.
        """
        self.client = client
        self._current_user: Optional[Dict[str, Any]] = None
        self._token: Optional[str] = None

    def login(self, username: str, password: str) -> bool:
        """Login a user with username and password.

        Args:
            username: The user's username.
            password: The user's password.

        Returns:
            True if login was successful.
        """
        credentials = {
            "username": username,
            "password": password,
        }
        response = self.client.new_api("/auth/login", method="POST", data=credentials)

        if response.get("status") == "success":
            self._token = response.get("token")
            self._current_user = response.get("user")
            return True
        return False

    def logout(self) -> bool:
        """Logout the current user.

        Returns:
            True if logout was successful.
        """
        if not self._token:
            return False

        response = self.client.new_api(
            "/auth/logout",
            method="POST",
            headers={"Authorization": f"Bearer {self._token}"},
        )

        if response.get("status") == "success":
            self._token = None
            self._current_user = None
            return True
        return False

    def refresh_token(self) -> Optional[str]:
        """Refresh the authentication token.

        Returns:
            The new token if refresh was successful, None otherwise.
        """
        if not self._token:
            return None

        response = self.client.new_api(
            "/auth/refresh",
            method="POST",
            headers={"Authorization": f"Bearer {self._token}"},
        )

        if response.get("status") == "success":
            self._token = response.get("token")
            return self._token
        return None

    @property
    def current_user(self) -> Optional[Dict[str, Any]]:
        """Get the currently logged in user."""
        return self._current_user

    @property
    def is_authenticated(self) -> bool:
        """Check if a user is currently authenticated."""
        return self._token is not None
