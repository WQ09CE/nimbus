"""
Test cases for Calculator module
计算器模块的测试用例
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from calculator import Calculator
import pytest

class TestCalculator:
    """Calculator测试类"""
    
    def setup_method(self):
        """每个测试前的准备"""
        self.calc = Calculator()
    
    def test_addition(self):
        """测试加法"""
        assert self.calc.add(2, 3) == 5
        assert self.calc.add(-1, 1) == 0
        assert self.calc.add(0, 0) == 0
    
    def test_subtraction(self):
        """测试减法"""
        assert self.calc.subtract(5, 3) == 2
        assert self.calc.subtract(1, 1) == 0
        assert self.calc.subtract(0, 5) == -5
    
    def test_multiplication(self):
        """测试乘法"""
        assert self.calc.multiply(3, 4) == 12
        assert self.calc.multiply(-2, 3) == -6
        assert self.calc.multiply(0, 100) == 0
    
    def test_division(self):
        """测试除法"""
        assert self.calc.divide(10, 2) == 5
        assert self.calc.divide(7, 2) == 3.5
        assert self.calc.divide(-6, 3) == -2
    
    def test_division_by_zero(self):
        """测试除零错误"""
        try:
            self.calc.divide(5, 0)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert str(e) == "Cannot divide by zero!"
    
    def test_history(self):
        """测试历史记录功能"""
        self.calc.add(1, 2)
        self.calc.multiply(3, 4)
        
        history = self.calc.get_history()
        assert len(history) == 2
        assert "1 + 2 = 3" in history[0]
        assert "3 × 4 = 12" in history[1]
    
    def test_clear_history(self):
        """测试清空历史"""
        self.calc.add(1, 1)
        assert len(self.calc.get_history()) == 1
        
        self.calc.clear_history()
        assert len(self.calc.get_history()) == 0

if __name__ == "__main__":
    # 简单的手动测试
    test = TestCalculator()
    test.setup_method()
    
    try:
        test.test_addition()
        test.test_subtraction()
        test.test_multiplication() 
        test.test_division()
        test.test_division_by_zero()
        test.test_history()
        test.test_clear_history()
        print("✅ All tests passed!")
    except Exception as e:
        print(f"❌ Test failed: {e}")