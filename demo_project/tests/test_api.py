#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API测试文件
测试Calculator类的基本导入和功能
"""

# 导入pytest测试框架
import pytest
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def test_basic_import():
    """
    测试基本导入功能
    验证能否成功导入Calculator类
    """
    try:
        # 尝试导入Calculator类
        from src.calculator import Calculator
        
        # 创建Calculator实例
        calc = Calculator()
        
        # 验证实例创建成功
        assert calc is not None
        assert isinstance(calc, Calculator)
        
    except ImportError as e:
        # 如果导入失败，测试失败
        pytest.fail(f"无法导入Calculator类: {e}")