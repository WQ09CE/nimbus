# 简单计算器模块

一个简单易用的Python计算器模块，提供基本的数学运算功能。

## 功能特性

- 加法运算
- 减法运算  
- 乘法运算
- 除法运算（包含除零检查）
- 完整的单元测试覆盖
- 详细的文档说明

## 安装使用

### 基本使用

```python
from simple_calculator import Calculator

# 创建计算器实例
calc = Calculator()

# 执行基本运算
result_add = calc.add(5, 3)        # 结果: 8
result_sub = calc.subtract(10, 4)  # 结果: 6
result_mul = calc.multiply(6, 7)   # 结果: 42
result_div = calc.divide(15, 3)    # 结果: 5.0
```

### 错误处理

```python
try:
    result = calc.divide(10, 0)
except ValueError as e:
    print(f"错误: {e}")  # 输出: 错误: 除数不能为零
```

## 文件结构

```
simple_calculator/
├── __init__.py          # 包初始化文件
├── calculator.py        # 主计算器类
├── test_calculator.py   # 单元测试
└── README.md           # 说明文档
```

## 运行测试

在项目目录中运行以下命令来执行单元测试：

```bash
cd simple_calculator
python -m unittest test_calculator.py
```

或者直接运行测试文件：

```bash
python test_calculator.py
```

## API参考

### Calculator类

#### 方法

- `add(a, b)` - 返回两个数的和
- `subtract(a, b)` - 返回两个数的差
- `multiply(a, b)` - 返回两个数的积  
- `divide(a, b)` - 返回两个数的商，除数为0时抛出ValueError

所有方法都支持整数和浮点数参数。

## 开发信息

- 版本: 1.0.0
- 作者: Agent
- Python版本要求: Python 3.6+

## 许可证

此项目仅供演示使用。