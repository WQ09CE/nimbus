def fib(n):
    """
    Return the nth Fibonacci number efficiently using an iterative approach.
    """
    if n < 0:
        raise ValueError("Input must be a non-negative integer")
    if n == 0:
        return 0
    if n == 1:
        return 1
    
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b

if __name__ == "__main__":
    # Test cases
    print(f"fib(0) = {fib(0)}")
    print(f"fib(1) = {fib(1)}")
    print(f"fib(10) = {fib(10)}")
