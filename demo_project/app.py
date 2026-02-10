#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flask Web应用主文件
提供计算器API服务，包含健康检查和计算功能端点
"""

# 导入必要的模块
from flask import Flask, request, jsonify
from flask_cors import CORS
from src.calculator import Calculator

# 创建Flask应用实例
app = Flask(__name__)

# 配置CORS支持所有来源
CORS(app)

# 创建全局Calculator实例
calculator = Calculator()


@app.route('/health', methods=['GET'])
def health_check():
    """
    健康检查端点
    返回API运行状态信息
    """
    return jsonify({
        "status": "ok",
        "message": "Calculator API is running"
    })


@app.route('/calculate', methods=['POST'])
def calculate():
    """
    计算端点
    接收POST请求，执行计算操作并返回结果
    """
    try:
        # 获取请求数据
        data = request.get_json()
        
        # 检查必需的参数
        if not data:
            return jsonify({
                "error": "请提供JSON格式的请求数据"
            }), 400
            
        operation = data.get('operation')
        a = data.get('a')
        b = data.get('b')
        
        # 验证参数
        if operation is None:
            return jsonify({
                "error": "缺少操作类型参数 'operation'"
            }), 400
            
        if a is None or b is None:
            return jsonify({
                "error": "缺少计算参数 'a' 或 'b'"
            }), 400
            
        # 执行计算
        if operation == 'add':
            result = calculator.add(a, b)
        elif operation == 'subtract':
            result = calculator.subtract(a, b)
        elif operation == 'multiply':
            result = calculator.multiply(a, b)
        elif operation == 'divide':
            result = calculator.divide(a, b)
        else:
            return jsonify({
                "error": f"不支持的操作类型: {operation}"
            }), 400
            
        # 返回计算结果
        return jsonify({
            "operation": operation,
            "a": a,
            "b": b,
            "result": result
        })
        
    except ValueError as e:
        # 处理计算错误（如除零错误）
        return jsonify({
            "error": str(e)
        }), 400
        
    except Exception as e:
        # 处理其他未预期的错误
        return jsonify({
            "error": f"服务器内部错误: {str(e)}"
        }), 500


@app.route('/history', methods=['GET'])
def get_history():
    """
    获取计算历史端点
    返回所有计算历史记录和记录总数
    """
    try:
        # 获取计算器的历史记录
        history = calculator.get_history()
        
        # 返回历史记录和数量
        return jsonify({
            "history": history,
            "count": len(history)
        })
        
    except Exception as e:
        # 处理获取历史记录时的错误
        return jsonify({
            "error": f"获取历史记录失败: {str(e)}"
        }), 500


@app.route('/history', methods=['DELETE'])
def clear_history():
    """
    清空计算历史端点
    删除所有计算历史记录
    """
    try:
        # 清空计算器的历史记录
        calculator.clear_history()
        
        # 返回清空成功的消息
        return jsonify({
            "message": "计算历史已清空"
        })
        
    except Exception as e:
        # 处理清空历史记录时的错误
        return jsonify({
            "error": f"清空历史记录失败: {str(e)}"
        }), 500


# 主程序入口
if __name__ == '__main__':
    # 启动Flask应用，监听所有接口的5000端口
    app.run(host='0.0.0.0', port=5000, debug=True)