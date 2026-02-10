"""
Simple Calculator Module
演示用的简单计算器
"""

class Calculator:
    """一个基础计算器类"""
    
    def __init__(self):
        self.history = []
    
    def add(self, a: float, b: float) -> float:
        """加法运算"""
        result = a + b
        self.history.append(f"{a} + {b} = {result}")
        return result
    
    def subtract(self, a: float, b: float) -> float:
        """减法运算"""
        result = a - b
        self.history.append(f"{a} - {b} = {result}")
        return result
    
    def multiply(self, a: float, b: float) -> float:
        """乘法运算"""
        result = a * b
        self.history.append(f"{a} × {b} = {result}")
        return result
    
    def divide(self, a: float, b: float) -> float:
        """除法运算"""
        if b == 0:
            raise ValueError("Cannot divide by zero!")
        result = a / b
        self.history.append(f"{a} ÷ {b} = {result}")
        return result
    
    def get_history(self) -> list:
        """获取计算历史"""
        return self.history.copy()
    
    def clear_history(self):
        """清空计算历史"""
        self.history.clear()

if __name__ == "__main__":
    calc = Calculator()
    print("🧮 Calculator Demo")
    print(f"10 + 5 = {calc.add(10, 5)}")
    print(f"10 - 3 = {calc.subtract(10, 3)}")
    print(f"4 × 6 = {calc.multiply(4, 6)}")
    print(f"15 ÷ 3 = {calc.divide(15, 3)}")
    
    print("\n📚 History:")
    for operation in calc.get_history():
        print(f"  {operation}")