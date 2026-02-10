"""
简单计算器模块
包含基本的数学运算功能
"""


class Calculator:
    """简单计算器类，提供基本的数学运算功能"""
    
    def add(self, a, b):
        """加法运算
        
        Args:
            a (float): 第一个数字
            b (float): 第二个数字
            
        Returns:
            float: 两数之和
        """
        return a + b
    
    def subtract(self, a, b):
        """减法运算
        
        Args:
            a (float): 被减数
            b (float): 减数
            
        Returns:
            float: 两数之差
        """
        return a - b
    
    def multiply(self, a, b):
        """乘法运算
        
        Args:
            a (float): 第一个数字
            b (float): 第二个数字
            
        Returns:
            float: 两数之积
        """
        return a * b
    
    def divide(self, a, b):
        """除法运算
        
        Args:
            a (float): 被除数
            b (float): 除数
            
        Returns:
            float: 两数之商
            
        Raises:
            ValueError: 当除数为0时抛出异常
        """
        if b == 0:
            raise ValueError("除数不能为零")
        return a / b