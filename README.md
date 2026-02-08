# Math Toolkit 数学工具包

> 一个简单而强大的Python数学工具包，演示 Nimbus Agent Framework 的工具调用能力。

## 📋 项目简介

Math Toolkit 是一个轻量级的数学运算工具包，提供了常用的数学计算功能：

- ➕ 基本四则运算
- 🔢 幂运算和阶乘计算  
- 📈 斐波那契数列生成
- 🛠️ 类型注解支持

## 🚀 快速开始

### 安装

```bash
# 克隆项目
git clone <repository-url>
cd math-toolkit

# 安装依赖（当前无外部依赖）
pip install -r requirements.txt

# 安装项目（可选）
pip install -e .
```

### 基本使用

```python
from math_toolkit import MathToolkit

# 创建工具包实例
toolkit = MathToolkit()

# 基本运算
print(toolkit.add(5, 3))        # 8
print(toolkit.multiply(4, 6))   # 24
print(toolkit.power(2, 8))      # 256.0

# 高级功能
print(toolkit.factorial(5))     # 120
print(toolkit.fibonacci(10))    # [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]

# 获取信息
print(toolkit.get_info())       # MathToolkit v1.0.0 - 数学工具包
```

## 🧪 运行测试

```bash
# 运行单元测试
python test_math_toolkit.py

# 或者使用 unittest
python -m unittest test_math_toolkit.py -v
```

## 📦 项目结构

```
math-toolkit/
├── math_toolkit.py        # 主模块文件
├── test_math_toolkit.py   # 单元测试
├── requirements.txt       # 项目依赖
├── setup.py              # 安装脚本
└── README.md             # 项目文档
```

## 🔧 API 参考

### MathToolkit 类

#### 方法列表

- `add(a, b)` - 加法运算
- `multiply(a, b)` - 乘法运算  
- `power(base, exponent)` - 幂运算
- `factorial(n)` - 阶乘计算（n >= 0）
- `fibonacci(n)` - 生成前n个斐波那契数
- `get_info()` - 获取工具包信息

#### 类型支持

- 支持 `int` 和 `float` 类型
- 包含完整的类型注解
- 异常处理（如负数阶乘）

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 🏷️ 标签

`python` `math` `toolkit` `nimbus` `agent-framework` `demo`

---

*此项目由 Nimbus Agent Framework 自动生成，展示了 AI Agent 的代码生成和项目管理能力。*