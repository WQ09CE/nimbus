#!/usr/bin/env python3
"""
Math Toolkit 单元测试
"""

import unittest
import sys
import os

# 添加当前目录到路径，以便导入 math_toolkit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from math_toolkit import MathToolkit

class TestMathToolkit(unittest.TestCase):
    """MathToolkit 测试类"""
    
    def setUp(self):
        """测试前设置"""
        self.toolkit = MathToolkit()
    
    def test_add(self):
        """测试加法运算"""
        self.assertEqual(self.toolkit.add(2, 3), 5)
        self.assertEqual(self.toolkit.add(-1, 1), 0)
        self.assertEqual(self.toolkit.add(2.5, 3.5), 6.0)
    
    def test_multiply(self):
        """测试乘法运算"""
        self.assertEqual(self.toolkit.multiply(3, 4), 12)
        self.assertEqual(self.toolkit.multiply(-2, 5), -10)
        self.assertEqual(self.toolkit.multiply(2.5, 4), 10.0)
    
    def test_power(self):
        """测试幂运算"""
        self.assertEqual(self.toolkit.power(2, 3), 8)
        self.assertEqual(self.toolkit.power(5, 0), 1)
        self.assertEqual(self.toolkit.power(4, 0.5), 2)
    
    def test_factorial(self):
        """测试阶乘计算"""
        self.assertEqual(self.toolkit.factorial(0), 1)
        self.assertEqual(self.toolkit.factorial(5), 120)
        
        # 测试异常情况
        with self.assertRaises(ValueError):
            self.toolkit.factorial(-1)
    
    def test_fibonacci(self):
        """测试斐波那契数列"""
        self.assertEqual(self.toolkit.fibonacci(0), [])
        self.assertEqual(self.toolkit.fibonacci(1), [0])
        self.assertEqual(self.toolkit.fibonacci(2), [0, 1])
        self.assertEqual(self.toolkit.fibonacci(7), [0, 1, 1, 2, 3, 5, 8])
    
    def test_get_info(self):
        """测试信息获取"""
        info = self.toolkit.get_info()
        self.assertIn("MathToolkit", info)
        self.assertIn("1.0.0", info)

if __name__ == "__main__":
    # 运行所有测试
    unittest.main(verbosity=2)