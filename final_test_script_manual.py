import os

def fib(n):
    if n <= 1:
        return n
    return fib(n-1) + fib(n-2)

print("Starting Final Test (Manual)...")
print(f"Fibonacci(20): {fib(20)}")
print(f"CWD: {os.getcwd()}")
print("Files:", os.listdir('.'))
print("Final Test Completed Successfully.")
