# Harbor Task Environment for read-and-fix-test
FROM python:3.12-slim

# Set working directory
WORKDIR /workspace

# Install basic tools
RUN apt-get update && apt-get install -y \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create logs directory
RUN mkdir -p /logs/verifier

# Pre-create the buggy file
RUN cat > sorter.py << 'PYEOF'
def bubble_sort(lst):
    """Sort a list using bubble sort."""
    arr = lst.copy()
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr[j] < arr[j + 1]:  # BUG: should be > for ascending
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr

def find_min(lst):
    """Find the minimum element."""
    if not lst:
        return None
    min_val = lst[0]
    for val in lst[1:]:
        if val > min_val:  # BUG: should be < to find minimum
            min_val = val
    return min_val

def is_sorted(lst):
    """Check if list is sorted in ascending order."""
    for i in range(len(lst)):  # BUG: should be len(lst) - 1 to avoid IndexError
        if lst[i] > lst[i + 1]:
            return False
    return True
PYEOF

# Default command
CMD ["/bin/bash"]
