# 用户管理API

这是一个基于FastAPI的简单用户管理系统演示项目。

## 项目结构

```
user_management_api/
├── app/                    # 主应用代码
│   ├── models/            # 数据模型
│   ├── routes/            # API路由
│   └── utils/             # 工具函数
├── tests/                 # 测试代码
├── config/                # 配置文件
├── main.py               # 应用入口文件
├── requirements.txt      # 项目依赖
└── README.md            # 项目说明文档
```

## 功能特性

- 用户注册和登录
- 用户信息管理
- RESTful API设计
- 数据验证
- 错误处理
- 自动化测试

## 快速开始

### 1. 安装依赖

```bash
cd user_management_api
pip install -r requirements.txt
```

### 2. 运行应用

```bash
python main.py
```

或者使用uvicorn：

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. 访问API

- 应用首页: http://localhost:8000
- API文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

## API端点

### 用户管理
- `POST /api/v1/users/` - 创建用户
- `GET /api/v1/users/` - 获取用户列表
- `GET /api/v1/users/{user_id}` - 获取用户详情
- `PUT /api/v1/users/{user_id}` - 更新用户信息
- `DELETE /api/v1/users/{user_id}` - 删除用户

## 测试

```bash
pytest tests/
```

## 技术栈

- **FastAPI**: 现代高性能的Python Web框架
- **SQLAlchemy**: Python SQL工具包和对象关系映射(ORM)
- **Pydantic**: 数据验证和设置管理
- **pytest**: Python测试框架
- **uvicorn**: ASGI服务器

## 开发规范

- 遵循PEP 8代码规范
- 使用类型注解
- 编写单元测试
- 添加详细的文档字符串

## 许可证

MIT License