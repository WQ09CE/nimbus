#!/bin/bash
# Harbor test script for bug-fix-test task
# Verifies that the calculator bugs are fixed

set -e

LOGS_DIR="${LOGS_DIR:-/logs/verifier}"
mkdir -p "$LOGS_DIR"

# Check if calculator.py exists
if [ ! -f "calculator.py" ]; then
    echo "FAIL: calculator.py not found"
    echo "0" > "$LOGS_DIR/reward.txt"
    exit 0
fi

# Run Python tests
python3 << 'EOF'
import sys, os
sys.path.insert(0, '.')
from calculator import Calculator

c = Calculator()
passed = 0
failed = 0

tests = [
    ("add(2,3)", c.add(2, 3), 5),
    ("add(-1,1)", c.add(-1, 1), 0),
    ("subtract(10,3)", c.subtract(10, 3), 7),
    ("subtract(5,5)", c.subtract(5, 5), 0),
    ("multiply(4,5)", c.multiply(4, 5), 20),
    ("multiply(0,100)", c.multiply(0, 100), 0),
    ("multiply(-2,3)", c.multiply(-2, 3), -6),
]

for name, result, expected in tests:
    if result == expected:
        print(f"PASS: {name} = {result}")
        passed += 1
    else:
        print(f"FAIL: {name} = {result}, expected {expected}")
        failed += 1

# Test divide by zero
try:
    c.divide(10, 0)
    print("FAIL: divide(10,0) should raise ValueError or ZeroDivisionError")
    failed += 1
except (ValueError, ZeroDivisionError):
    print("PASS: divide(10,0) correctly raises exception")
    passed += 1

# Test normal divide
try:
    r = c.divide(10, 2)
    if r == 5.0:
        print(f"PASS: divide(10,2) = {r}")
        passed += 1
    else:
        print(f"FAIL: divide(10,2) = {r}, expected 5.0")
        failed += 1
except Exception as e:
    print(f"FAIL: divide(10,2) raised {e}")
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
