"""Reference solution for the read-and-fix-test task."""


def bubble_sort(lst):
    """Sort a list using bubble sort."""
    arr = lst.copy()
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr


def find_min(lst):
    """Find the minimum element."""
    if not lst:
        return None
    min_val = lst[0]
    for val in lst[1:]:
        if val < min_val:
            min_val = val
    return min_val


def is_sorted(lst):
    """Check if list is sorted in ascending order."""
    for i in range(len(lst) - 1):
        if lst[i] > lst[i + 1]:
            return False
    return True
