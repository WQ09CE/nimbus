"""Sorting utility with logic issues."""

def find_max(numbers: list) -> int:
    """Find the maximum number in a list.

    Args:
        numbers: Non-empty list of numbers

    Returns:
        The maximum number
    """
    if not numbers:
        return None

    max_val = numbers[0]  # Fixed: initialize with first element instead of 0
    for num in numbers:
        if num > max_val:
            max_val = num
    return max_val


def is_sorted(arr: list) -> bool:
    """Check if array is sorted in ascending order."""
    for i in range(len(arr)):  # BUG: should be range(len(arr) - 1)
        if arr[i] > arr[i + 1]:  # Will cause IndexError
            return False
    return True
