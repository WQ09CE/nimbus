# Task: Implement a Fibonacci Function

Create a Python file named `solution.py` in the current working directory that implements a function called `fibonacci`.

## Requirements

1. The function should be named `fibonacci` and take a single integer parameter `n`
2. It should return the nth Fibonacci number (0-indexed)
3. The Fibonacci sequence starts: 0, 1, 1, 2, 3, 5, 8, 13, 21, ...
4. Handle edge cases:
   - `fibonacci(0)` should return `0`
   - `fibonacci(1)` should return `1`
   - Negative inputs should raise a `ValueError`

## Example Usage

```python
>>> fibonacci(0)
0
>>> fibonacci(1)
1
>>> fibonacci(10)
55
>>> fibonacci(-1)
ValueError: n must be non-negative
```

## Deliverable

A single file `solution.py` containing the `fibonacci` function.
