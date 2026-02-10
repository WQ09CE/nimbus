"""
简单计算器模块

这是一个简单的Python计算器包，提供基本的数学运算功能。

主要组件：
- Calculator: 主计算器类，提供加减乘除运算
"""

from .calculator import Calculator

__version__ = "1.0.0"
__author__ = "Agent"
__email__ = "agent@example.com"

# 导出的公共API
__all__ = ["Calculator"]