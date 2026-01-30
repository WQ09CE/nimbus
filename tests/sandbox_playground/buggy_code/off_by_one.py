"""Calculator with a bug."""

def sum_range(start: int, end: int) -> int:
    """Calculate sum of numbers from start to end (inclusive).

    Example: sum_range(1, 3) should return 1 + 2 + 3 = 6
    """
    total = 0
    for i in range(start, end):  # BUG: should be range(start, end + 1)
        total += i
    return total


def test_sum_range():
    assert sum_range(1, 3) == 6  # This will fail!
    assert sum_range(0, 5) == 15  # This will also fail!
