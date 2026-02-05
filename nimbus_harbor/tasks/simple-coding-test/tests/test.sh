#!/bin/bash
# Harbor test script for simple-coding-test task
# Verifies that the fibonacci function is correctly implemented

set -e

LOGS_DIR="${LOGS_DIR:-/logs/verifier}"
mkdir -p "$LOGS_DIR"

# Check if solution.py exists
if [ ! -f "solution.py" ]; then
    echo "FAIL: solution.py not found"
    echo "0" > "$LOGS_DIR/reward.txt"
    exit 0
fi

# Run Python tests
python3 << 'EOF'
import sys
sys.path.insert(0, '.')

try:
    from solution import fibonacci
except ImportError as e:
    print(f"FAIL: Cannot import fibonacci function: {e}")
    sys.exit(1)

# Test cases
test_cases = [
    (0, 0),
    (1, 1),
    (2, 1),
    (3, 2),
    (4, 3),
    (5, 5),
    (10, 55),
    (15, 610),
]

passed = 0
failed = 0

for n, expected in test_cases:
    try:
        result = fibonacci(n)
        if result == expected:
            print(f"PASS: fibonacci({n}) = {result}")
            passed += 1
        else:
            print(f"FAIL: fibonacci({n}) = {result}, expected {expected}")
            failed += 1
    except Exception as e:
        print(f"FAIL: fibonacci({n}) raised {type(e).__name__}: {e}")
        failed += 1

# Test negative input handling
try:
    fibonacci(-1)
    print("FAIL: fibonacci(-1) should raise ValueError")
    failed += 1
except ValueError:
    print("PASS: fibonacci(-1) correctly raises ValueError")
    passed += 1
except Exception as e:
    print(f"FAIL: fibonacci(-1) raised wrong exception: {type(e).__name__}")
    failed += 1

# Calculate score
total = passed + failed
score = passed / total if total > 0 else 0

print(f"\nResults: {passed}/{total} tests passed")
print(f"Score: {score:.2f}")

# Write reward (1 for full pass, partial for partial pass)
import os
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
