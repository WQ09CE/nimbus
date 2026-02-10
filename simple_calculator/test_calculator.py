"""
计算器模块的单元测试
"""

import unittest
from calculator import Calculator


class TestCalculator(unittest.TestCase):
    """Calculator类的单元测试"""
    
    def setUp(self):
        """测试前的准备工作"""
        self.calc = Calculator()
    
    def test_add(self):
        """测试加法功能"""
        self.assertEqual(self.calc.add(2, 3), 5)
        self.assertEqual(self.calc.add(-1, 1), 0)
        self.assertEqual(self.calc.add(0.5, 0.5), 1.0)
        self.assertEqual(self.calc.add(-5, -3), -8)
    
    def test_subtract(self):
        """测试减法功能"""
        self.assertEqual(self.calc.subtract(5, 3), 2)
        self.assertEqual(self.calc.subtract(1, 1), 0)
        self.assertEqual(self.calc.subtract(-1, -1), 0)
        self.assertEqual(self.calc.subtract(10, 15), -5)
    
    def test_multiply(self):
        """测试乘法功能"""
        self.assertEqual(self.calc.multiply(3, 4), 12)
        self.assertEqual(self.calc.multiply(-2, 5), -10)
        self.assertEqual(self.calc.multiply(0, 100), 0)
        self.assertEqual(self.calc.multiply(0.5, 4), 2.0)
    
    def test_divide(self):
        """测试除法功能"""
        self.assertEqual(self.calc.divide(10, 2), 5)
        self.assertEqual(self.calc.divide(7, 2), 3.5)
        self.assertEqual(self.calc.divide(-8, 4), -2)
        self.assertEqual(self.calc.divide(0, 5), 0)
    
    def test_divide_by_zero(self):
        """测试除零异常"""
        with self.assertRaises(ValueError) as context:
            self.calc.divide(5, 0)
        self.assertEqual(str(context.exception), "除数不能为零")


if __name__ == '__main__':
    unittest.main()