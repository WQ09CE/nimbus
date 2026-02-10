def fibonacci(n):
    """
    返回斐波那契数列的第 n 项。
    如果 n <= 0 返回 0，n=1 返回 1。
    """
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b

if __name__ == "__main__":
    # 简单测试
    for i in range(10):
        print(f"fibonacci({i}) = {fibonacci(i)}")
