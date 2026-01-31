"""String processor with type issues."""

def process_items(items):
    """Process a list of items and return their string representation.

    Args:
        items: A list of items to process

    Returns:
        Processed string with all items joined by comma
    """
    result = ""
    for item in items:
        result += item  # BUG: doesn't handle non-string items
    return result


def get_user_info(user_dict):
    """Get formatted user info.

    Args:
        user_dict: Dictionary with 'name' and 'age' keys

    Returns:
        Formatted string "Name: X, Age: Y"
    """
    # BUG: doesn't handle missing keys
    return f"Name: {user_dict['name']}, Age: {user_dict['age']}"
