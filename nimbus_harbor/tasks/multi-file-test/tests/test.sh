#!/bin/bash
# Harbor test script for multi-file-test task
# Verifies that the mathutils package is correctly implemented

set -e

LOGS_DIR="${LOGS_DIR:-/logs/verifier}"
mkdir -p "$LOGS_DIR"

# Check if mathutils package exists
if [ ! -d "mathutils" ]; then
    echo "FAIL: mathutils/ directory not found"
    echo "0" > "$LOGS_DIR/reward.txt"
    exit 0
fi

if [ ! -f "mathutils/__init__.py" ]; then
    echo "FAIL: mathutils/__init__.py not found"
    echo "0" > "$LOGS_DIR/reward.txt"
    exit 0
fi

# Run Python tests
python3 << 'EOF'
import sys, os, math
sys.path.insert(0, '.')

passed = 0
failed = 0

# --- Test stats.py functions ---

# Test mean()
try:
    from mathutils.stats import mean
    result = mean([1, 2, 3, 4, 5])
    if result == 3.0:
        print(f"PASS: mean([1,2,3,4,5]) = {result}")
        passed += 1
    else:
        print(f"FAIL: mean([1,2,3,4,5]) = {result}, expected 3.0")
        failed += 1
except Exception as e:
    print(f"FAIL: mean() raised {type(e).__name__}: {e}")
    failed += 1

try:
    result = mean([10])
    if result == 10.0:
        print(f"PASS: mean([10]) = {result}")
        passed += 1
    else:
        print(f"FAIL: mean([10]) = {result}, expected 10.0")
        failed += 1
except Exception as e:
    print(f"FAIL: mean([10]) raised {type(e).__name__}: {e}")
    failed += 1

# Test median()
try:
    from mathutils.stats import median
    result = median([3, 1, 2])
    if result == 2:
        print(f"PASS: median([3,1,2]) = {result}")
        passed += 1
    else:
        print(f"FAIL: median([3,1,2]) = {result}, expected 2")
        failed += 1
except Exception as e:
    print(f"FAIL: median() raised {type(e).__name__}: {e}")
    failed += 1

try:
    result = median([1, 2, 3, 4])
    if result == 2.5:
        print(f"PASS: median([1,2,3,4]) = {result}")
        passed += 1
    else:
        print(f"FAIL: median([1,2,3,4]) = {result}, expected 2.5")
        failed += 1
except Exception as e:
    print(f"FAIL: median() raised {type(e).__name__}: {e}")
    failed += 1

# Test mode()
try:
    from mathutils.stats import mode
    result = mode([1, 2, 2, 3, 3, 3])
    if result == 3:
        print(f"PASS: mode([1,2,2,3,3,3]) = {result}")
        passed += 1
    else:
        print(f"FAIL: mode([1,2,2,3,3,3]) = {result}, expected 3")
        failed += 1
except Exception as e:
    print(f"FAIL: mode() raised {type(e).__name__}: {e}")
    failed += 1

try:
    result = mode([1, 1, 2, 2])
    if result == 1:
        print(f"PASS: mode([1,1,2,2]) = {result} (tie -> smallest)")
        passed += 1
    else:
        print(f"FAIL: mode([1,1,2,2]) = {result}, expected 1 (tie -> smallest)")
        failed += 1
except Exception as e:
    print(f"FAIL: mode() raised {type(e).__name__}: {e}")
    failed += 1

# --- Test geometry.py functions ---

# Test circle_area()
try:
    from mathutils.geometry import circle_area
    result = circle_area(1)
    if abs(result - math.pi) < 1e-9:
        print(f"PASS: circle_area(1) = {result}")
        passed += 1
    else:
        print(f"FAIL: circle_area(1) = {result}, expected {math.pi}")
        failed += 1
except Exception as e:
    print(f"FAIL: circle_area() raised {type(e).__name__}: {e}")
    failed += 1

try:
    result = circle_area(5)
    expected = math.pi * 25
    if abs(result - expected) < 1e-9:
        print(f"PASS: circle_area(5) = {result}")
        passed += 1
    else:
        print(f"FAIL: circle_area(5) = {result}, expected {expected}")
        failed += 1
except Exception as e:
    print(f"FAIL: circle_area(5) raised {type(e).__name__}: {e}")
    failed += 1

# Test rectangle_area()
try:
    from mathutils.geometry import rectangle_area
    result = rectangle_area(4, 5)
    if result == 20:
        print(f"PASS: rectangle_area(4,5) = {result}")
        passed += 1
    else:
        print(f"FAIL: rectangle_area(4,5) = {result}, expected 20")
        failed += 1
except Exception as e:
    print(f"FAIL: rectangle_area() raised {type(e).__name__}: {e}")
    failed += 1

try:
    result = rectangle_area(0, 10)
    if result == 0:
        print(f"PASS: rectangle_area(0,10) = {result}")
        passed += 1
    else:
        print(f"FAIL: rectangle_area(0,10) = {result}, expected 0")
        failed += 1
except Exception as e:
    print(f"FAIL: rectangle_area(0,10) raised {type(e).__name__}: {e}")
    failed += 1

# Test triangle_area()
try:
    from mathutils.geometry import triangle_area
    result = triangle_area(6, 4)
    if result == 12.0:
        print(f"PASS: triangle_area(6,4) = {result}")
        passed += 1
    else:
        print(f"FAIL: triangle_area(6,4) = {result}, expected 12.0")
        failed += 1
except Exception as e:
    print(f"FAIL: triangle_area() raised {type(e).__name__}: {e}")
    failed += 1

try:
    result = triangle_area(10, 5)
    if result == 25.0:
        print(f"PASS: triangle_area(10,5) = {result}")
        passed += 1
    else:
        print(f"FAIL: triangle_area(10,5) = {result}, expected 25.0")
        failed += 1
except Exception as e:
    print(f"FAIL: triangle_area(10,5) raised {type(e).__name__}: {e}")
    failed += 1

# --- Test __init__.py exports ---
try:
    from mathutils import mean, median, mode, circle_area, rectangle_area, triangle_area
    print("PASS: All functions importable from mathutils package")
    passed += 1
except ImportError as e:
    print(f"FAIL: Cannot import from mathutils: {e}")
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
