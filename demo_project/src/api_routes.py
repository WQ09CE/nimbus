# API 路由定义文件
# 定义 Flask 应用的所有 API 端点

from flask import Blueprint, request, jsonify
from .calculator import Calculator

# 创建 API 蓝图
api_bp = Blueprint('api', __name__, url_prefix='/api')

# 创建计算器实例
calc = Calculator()

@api_bp.route('/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'healthy',
        'message': '服务运行正常'
    })

@api_bp.route('/add', methods=['POST'])
def add():
    """加法计算端点"""
    try:
        data = request.get_json()
        if not data or 'a' not in data or 'b' not in data:
            return jsonify({
                'error': '缺少必要参数 a 或 b'
            }), 400
        
        result = calc.add(data['a'], data['b'])
        return jsonify({
            'result': result,
            'operation': 'add',
            'operands': [data['a'], data['b']]
        })
    except Exception as e:
        return jsonify({
            'error': f'计算错误: {str(e)}'
        }), 500

@api_bp.route('/subtract', methods=['POST'])
def subtract():
    """减法计算端点"""
    try:
        data = request.get_json()
        if not data or 'a' not in data or 'b' not in data:
            return jsonify({
                'error': '缺少必要参数 a 或 b'
            }), 400
        
        result = calc.subtract(data['a'], data['b'])
        return jsonify({
            'result': result,
            'operation': 'subtract',
            'operands': [data['a'], data['b']]
        })
    except Exception as e:
        return jsonify({
            'error': f'计算错误: {str(e)}'
        }), 500

@api_bp.route('/multiply', methods=['POST'])
def multiply():
    """乘法计算端点"""
    try:
        data = request.get_json()
        if not data or 'a' not in data or 'b' not in data:
            return jsonify({
                'error': '缺少必要参数 a 或 b'
            }), 400
        
        result = calc.multiply(data['a'], data['b'])
        return jsonify({
            'result': result,
            'operation': 'multiply',
            'operands': [data['a'], data['b']]
        })
    except Exception as e:
        return jsonify({
            'error': f'计算错误: {str(e)}'
        }), 500

@api_bp.route('/divide', methods=['POST'])
def divide():
    """除法计算端点"""
    try:
        data = request.get_json()
        if not data or 'a' not in data or 'b' not in data:
            return jsonify({
                'error': '缺少必要参数 a 或 b'
            }), 400
        
        result = calc.divide(data['a'], data['b'])
        return jsonify({
            'result': result,
            'operation': 'divide',
            'operands': [data['a'], data['b']]
        })
    except ValueError as e:
        return jsonify({
            'error': f'计算错误: {str(e)}'
        }), 400
    except Exception as e:
        return jsonify({
            'error': f'计算错误: {str(e)}'
        }), 500