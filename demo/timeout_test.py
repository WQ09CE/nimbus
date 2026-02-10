"""
Timeout strategy test - verifies iteration-based budget works.
Created to test the new Executor iteration budget mechanism.
"""


def fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number."""
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


def is_prime(n: int) -> bool:
    """Check if a number is prime."""
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


if __name__ == "__main__":
    # Fibonacci test
    for i in range(10):
        print(f"fib({i}) = {fibonacci(i)}")

    # Prime test
    primes = [n for n in range(50) if is_prime(n)]
    print(f"Primes under 50: {primes}")

    print("ALL OK")
