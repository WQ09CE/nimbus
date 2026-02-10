#!/usr/bin/env python3
"""
Math Toolkit 安装脚本
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="math-toolkit",
    version="1.0.0",
    author="Nimbus Agent",
    author_email="agent@nimbus.ai",
    description="一个简单的数学工具包",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/nimbus/math-toolkit",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.8",
    install_requires=[
        # 当前无外部依赖
    ],
    extras_require={
        "dev": [
            "pytest>=6.0",
            "black>=21.0",
            "flake8>=3.8",
        ]
    }
)