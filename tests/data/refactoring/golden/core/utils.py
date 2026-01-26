"""Utility functions for API operations.

This module provides helper functions that use the APIClient.
"""

from typing import List, Dict, Any, Optional
from .client import APIClient


def fetch_all_users(client: APIClient) -> List[Dict[str, Any]]:
    """Fetch all users from the API.

    Args:
        client: The APIClient instance to use.

    Returns:
        List of user dictionaries.
    """
    response = client.new_api("/users", method="GET")
    return response.get("data", [])


def create_user(
    client: APIClient,
    username: str,
    email: str,
    role: str = "user",
) -> Dict[str, Any]:
    """Create a new user via the API.

    Args:
        client: The APIClient instance to use.
        username: The username for the new user.
        email: The email address for the new user.
        role: The user role (default: "user").

    Returns:
        The created user data.
    """
    user_data = {
        "username": username,
        "email": email,
        "role": role,
    }
    response = client.new_api("/users", method="POST", data=user_data)
    return response


def batch_update_users(
    client: APIClient,
    updates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Update multiple users in batch.

    Args:
        client: The APIClient instance to use.
        updates: List of update dictionaries with user_id and fields to update.

    Returns:
        List of update results.
    """
    results = []
    for update in updates:
        user_id = update.pop("user_id")
        response = client.new_api(
            f"/users/{user_id}",
            method="PUT",
            data=update,
        )
        results.append(response)
    return results


def delete_user(client: APIClient, user_id: int) -> bool:
    """Delete a user by ID.

    Args:
        client: The APIClient instance to use.
        user_id: The ID of the user to delete.

    Returns:
        True if deletion was successful.
    """
    response = client.new_api(f"/users/{user_id}", method="DELETE")
    return response.get("status") == "success"
