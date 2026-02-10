#!/usr/bin/env python3
"""
Math Toolkit - 数学工具包
演示 Dispatch 工具调用过程
"""

import math
from typing import Union, List

class MathToolkit:
    """数学工具类"""
    
    def __init__(self):
        self.name = "MathToolkit"
        self.version = "1.0.0"
    
    def add(self, a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
        """加法运算"""
        return a + b
    
    def multiply(self, a: Union[int, float], b: Union[int, float]) -> Union[int, float]:
        """乘法运算"""
        return a * b
    
    def power(self, base: Union[int, float], exponent: Union[int, float]) -> Union[int, float]:
        """幂运算"""
        return math.pow(base, exponent)
    
    def factorial(self, n: int) -> int:
        """阶乘计算"""
        if n < 0:
            raise ValueError("阶乘输入必须为非负整数")
        return math.factorial(n)
    
    def fibonacci(self, n: int) -> List[int]:
        """生成斐波那契数列"""
        if n <= 0:
            return []
        elif n == 1:
            return [0]
        elif n == 2:
            return [0, 1]
        
        fib = [0, 1]
        for i in range(2, n):
            fib.append(fib[i-1] + fib[i-2])
        return fib
    
    def get_info(self) -> str:
        """获取工具包信息"""
        return f"{self.name} v{self.version} - 数学工具包"

if __name__ == "__main__":
    # 测试代码
    toolkit = MathToolkit()
    print(toolkit.get_info())
    print(f"5 + 3 = {toolkit.add(5, 3)}")
    print(f"4 * 6 = {toolkit.multiply(4, 6)}")
    print(f"2^8 = {toolkit.power(2, 8)}")
    print(f"5! = {toolkit.factorial(5)}")
    print(f"前10个斐波那契数: {toolkit.fibonacci(10)}")