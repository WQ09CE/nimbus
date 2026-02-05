#!/bin/bash
# Harbor test script for read-and-fix-test task
# Verifies that the sorting module bugs are fixed

set -e

LOGS_DIR="${LOGS_DIR:-/logs/verifier}"
mkdir -p "$LOGS_DIR"

# Check if sorter.py exists
if [ ! -f "sorter.py" ]; then
    echo "FAIL: sorter.py not found"
    echo "0" > "$LOGS_DIR/reward.txt"
    exit 0
fi

# Run Python tests
python3 << 'EOF'
import sys, os
sys.path.insert(0, '.')

from sorter import bubble_sort, find_min, is_sorted

passed = 0
failed = 0

# --- Test bubble_sort ---

# Test basic sorting
try:
    result = bubble_sort([3, 1, 4, 1, 5, 9, 2, 6])
    expected = [1, 1, 2, 3, 4, 5, 6, 9]
    if result == expected:
        print(f"PASS: bubble_sort([3,1,4,1,5,9,2,6]) = {result}")
        passed += 1
    else:
        print(f"FAIL: bubble_sort([3,1,4,1,5,9,2,6]) = {result}, expected {expected}")
        failed += 1
except Exception as e:
    print(f"FAIL: bubble_sort() raised {type(e).__name__}: {e}")
    failed += 1

# Test already sorted
try:
    result = bubble_sort([1, 2, 3])
    expected = [1, 2, 3]
    if result == expected:
        print(f"PASS: bubble_sort([1,2,3]) = {result}")
        passed += 1
    else:
        print(f"FAIL: bubble_sort([1,2,3]) = {result}, expected {expected}")
        failed += 1
except Exception as e:
    print(f"FAIL: bubble_sort() raised {type(e).__name__}: {e}")
    failed += 1

# Test reverse sorted
try:
    result = bubble_sort([5, 4, 3, 2, 1])
    expected = [1, 2, 3, 4, 5]
    if result == expected:
        print(f"PASS: bubble_sort([5,4,3,2,1]) = {result}")
        passed += 1
    else:
        print(f"FAIL: bubble_sort([5,4,3,2,1]) = {result}, expected {expected}")
        failed += 1
except Exception as e:
    print(f"FAIL: bubble_sort() raised {type(e).__name__}: {e}")
    failed += 1

# Test empty list
try:
    result = bubble_sort([])
    expected = []
    if result == expected:
        print(f"PASS: bubble_sort([]) = {result}")
        passed += 1
    else:
        print(f"FAIL: bubble_sort([]) = {result}, expected {expected}")
        failed += 1
except Exception as e:
    print(f"FAIL: bubble_sort([]) raised {type(e).__name__}: {e}")
    failed += 1

# Test does not mutate original
try:
    original = [3, 1, 2]
    result = bubble_sort(original)
    if original == [3, 1, 2] and result == [1, 2, 3]:
        print(f"PASS: bubble_sort does not mutate original list")
        passed += 1
    else:
        print(f"FAIL: bubble_sort mutated original list or returned wrong result")
        failed += 1
except Exception as e:
    print(f"FAIL: bubble_sort() raised {type(e).__name__}: {e}")
    failed += 1

# --- Test find_min ---

# Test basic find_min
try:
    result = find_min([3, 1, 4, 1, 5])
    if result == 1:
        print(f"PASS: find_min([3,1,4,1,5]) = {result}")
        passed += 1
    else:
        print(f"FAIL: find_min([3,1,4,1,5]) = {result}, expected 1")
        failed += 1
except Exception as e:
    print(f"FAIL: find_min() raised {type(e).__name__}: {e}")
    failed += 1

# Test single element
try:
    result = find_min([42])
    if result == 42:
        print(f"PASS: find_min([42]) = {result}")
        passed += 1
    else:
        print(f"FAIL: find_min([42]) = {result}, expected 42")
        failed += 1
except Exception as e:
    print(f"FAIL: find_min([42]) raised {type(e).__name__}: {e}")
    failed += 1

# Test with negative numbers
try:
    result = find_min([5, -3, 2, -7, 0])
    if result == -7:
        print(f"PASS: find_min([5,-3,2,-7,0]) = {result}")
        passed += 1
    else:
        print(f"FAIL: find_min([5,-3,2,-7,0]) = {result}, expected -7")
        failed += 1
except Exception as e:
    print(f"FAIL: find_min() raised {type(e).__name__}: {e}")
    failed += 1

# Test empty list
try:
    result = find_min([])
    if result is None:
        print(f"PASS: find_min([]) = None")
        passed += 1
    else:
        print(f"FAIL: find_min([]) = {result}, expected None")
        failed += 1
except Exception as e:
    print(f"FAIL: find_min([]) raised {type(e).__name__}: {e}")
    failed += 1

# --- Test is_sorted ---

# Test sorted list
try:
    result = is_sorted([1, 2, 3, 4, 5])
    if result is True:
        print(f"PASS: is_sorted([1,2,3,4,5]) = {result}")
        passed += 1
    else:
        print(f"FAIL: is_sorted([1,2,3,4,5]) = {result}, expected True")
        failed += 1
except Exception as e:
    print(f"FAIL: is_sorted() raised {type(e).__name__}: {e}")
    failed += 1

# Test unsorted list
try:
    result = is_sorted([1, 3, 2])
    if result is False:
        print(f"PASS: is_sorted([1,3,2]) = {result}")
        passed += 1
    else:
        print(f"FAIL: is_sorted([1,3,2]) = {result}, expected False")
        failed += 1
except Exception as e:
    print(f"FAIL: is_sorted() raised {type(e).__name__}: {e}")
    failed += 1

# Test empty list
try:
    result = is_sorted([])
    if result is True:
        print(f"PASS: is_sorted([]) = {result}")
        passed += 1
    else:
        print(f"FAIL: is_sorted([]) = {result}, expected True")
        failed += 1
except Exception as e:
    print(f"FAIL: is_sorted([]) raised {type(e).__name__}: {e}")
    failed += 1

# Test single element
try:
    result = is_sorted([1])
    if result is True:
        print(f"PASS: is_sorted([1]) = {result}")
        passed += 1
    else:
        print(f"FAIL: is_sorted([1]) = {result}, expected True")
        failed += 1
except Exception as e:
    print(f"FAIL: is_sorted([1]) raised {type(e).__name__}: {e}")
    failed += 1

# Test with equal elements
try:
    result = is_sorted([2, 2, 2])
    if result is True:
        print(f"PASS: is_sorted([2,2,2]) = {result}")
        passed += 1
    else:
        print(f"FAIL: is_sorted([2,2,2]) = {result}, expected True")
        failed += 1
except Exception as e:
    print(f"FAIL: is_sorted() raised {type(e).__name__}: {e}")
    failed += 1

# Calculate score
total = passed + failed
score = passed / total if total > 0 else 0

print(f"\nResults: {passed}/{total} tests passed")
print(f"Score: {score:.2f}")

# Write reward
logs_dir = os.environ.get("LOGS_DIR", "/logs/verifier")
os.makedirs(logs_dir, exist_ok=True)
with open(f"{logs_dir}/reward.txt", "w") as f:
    f.write(f"{score}\n")

sys.exit(0 if failed == 0 else 1)
EOF

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo "All tests passed!"
else
    echo "Some tests failed."
fi

exit 0
