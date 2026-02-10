"""
用户管理API主入口文件
"""

from fastapi import FastAPI
from app.routes.user_routes import router as user_router

app = FastAPI(
    title="用户管理API",
    description="一个简单的用户管理系统API",
    version="1.0.0"
)

# 注册路由
app.include_router(user_router, prefix="/api/v1", tags=["用户管理"])

@app.get("/")
async def root():
    """
    根路径，返回API欢迎信息
    """
    return {"message": "欢迎使用用户管理API", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    """
    健康检查端点
    """
    return {"status": "healthy", "message": "API运行正常"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)