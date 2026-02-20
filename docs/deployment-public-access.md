# Nimbus 外网访问部署方案

## 当前状态

- **架构**: 纯 Python (FastAPI + DirectAdapter/LiteLLM)
- **默认绑定**: `127.0.0.1:4096` (仅本地访问)
- **CORS**: 已配置为 `allow_origins=["*"]` (开发模式)
- **认证**: 无 (需要添加)

## 方案对比

### 方案 1: 快速开启（测试/内网使用）⚡

**优点**: 1 分钟搞定
**缺点**: 无安全保护，不适合生产环境

```bash
# 直接绑定 0.0.0.0
./nimbus stop
NIMBUS_HOST=0.0.0.0 ./nimbus start

# 或者修改启动脚本
nimbus serve --host 0.0.0.0 --port 4096
```

**适用场景**:
- 内网测试
- 开发调试
- VPN 内部使用

---

### 方案 2: 生产部署（推荐）🛡️

完整的安全加固 + 反向代理方案

#### 2.1 添加 API 认证

**步骤 1**: 在配置文件添加认证支持

```python
# src/nimbus/config.py
@dataclass
class NimbusConfig:
    # ... 现有配置 ...
    api_token: str | None = None  # API 访问令牌
    enable_auth: bool = False     # 是否启用认证

    def __post_init__(self):
        # ... 现有逻辑 ...

        # 读取 API token
        if "NIMBUS_API_TOKEN" in os.environ:
            self.api_token = os.environ["NIMBUS_API_TOKEN"]
            self.enable_auth = True
        elif self.api_token:
            self.enable_auth = True
```

**步骤 2**: 添加认证中间件

```python
# src/nimbus/server/middleware/auth.py
from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

async def verify_token(credentials: HTTPAuthorizationCredentials | None) -> bool:
    """验证 API token"""
    from nimbus.config import get_config
    cfg = get_config()

    if not cfg.enable_auth:
        return True  # 未启用认证，放行

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token"
        )

    if credentials.credentials != cfg.api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token"
        )

    return True

class AuthMiddleware:
    """认证中间件"""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 健康检查端点不需要认证
        if scope["path"] == "/api/v1/health":
            await self.app(scope, receive, send)
            return

        # 其他 API 端点需要认证
        if scope["path"].startswith("/api/"):
            from nimbus.config import get_config
            cfg = get_config()

            if cfg.enable_auth:
                # 检查 Authorization header
                headers = dict(scope.get("headers", []))
                auth_header = headers.get(b"authorization", b"").decode()

                if not auth_header.startswith("Bearer "):
                    # 返回 401
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "Missing or invalid authorization header"}
                    )
                    await response(scope, receive, send)
                    return

                token = auth_header[7:]  # Remove "Bearer "
                if token != cfg.api_token:
                    response = JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid token"}
                    )
                    await response(scope, receive, send)
                    return

        await self.app(scope, receive, send)
```

**步骤 3**: 在 app.py 注册中间件

```python
# src/nimbus/server/app.py
from .middleware.auth import AuthMiddleware

def create_app() -> FastAPI:
    app = FastAPI(...)

    # 添加认证中间件（在 CORS 之后）
    app.add_middleware(AuthMiddleware)

    # ... 其他配置 ...
```

**步骤 4**: 使用方式

```bash
# 生成强随机 token
export NIMBUS_API_TOKEN=$(openssl rand -hex 32)

# 启动服务（启用认证）
NIMBUS_HOST=0.0.0.0 NIMBUS_API_TOKEN=$NIMBUS_API_TOKEN ./nimbus start

# 客户端调用
curl -H "Authorization: Bearer $NIMBUS_API_TOKEN" \
     http://your-server:4096/api/v1/sessions
```

#### 2.2 使用 Caddy 反向代理（自动 HTTPS）

**为什么用 Caddy**:
- ✅ 自动申请和续期 Let's Encrypt 证书
- ✅ 配置极简（比 nginx 简单 10 倍）
- ✅ 自动 HTTP → HTTPS 重定向
- ✅ 内置速率限制、访问日志

**安装 Caddy**:

```bash
# macOS
brew install caddy

# Linux
sudo apt install -y caddy  # Debian/Ubuntu
sudo yum install caddy      # CentOS/RHEL
```

**Caddyfile 配置**:

```caddyfile
# /etc/caddy/Caddyfile (Linux)
# 或 ~/Caddyfile (本地测试)

nimbus.yourdomain.com {
    # 自动 HTTPS（需要公网 IP + 域名）

    # 反向代理到 nimbus
    reverse_proxy localhost:4096 {
        # 健康检查
        health_uri /api/v1/health
        health_interval 10s

        # 超时设置
        timeout 300s
    }

    # 速率限制（每秒 10 请求）
    rate_limit {
        zone nimbus_api {
            key {remote_host}
            events 10
            window 1s
        }
    }

    # 访问日志
    log {
        output file /var/log/caddy/nimbus-access.log
        format json
    }

    # 禁止访问敏感路径
    @admin {
        path /admin*
    }
    respond @admin 403
}
```

**启动 Caddy**:

```bash
# 前台运行（测试）
caddy run --config Caddyfile

# 后台运行
sudo systemctl start caddy
sudo systemctl enable caddy
```

#### 2.3 Systemd 服务配置

```ini
# /etc/systemd/system/nimbus.service
[Unit]
Description=Nimbus Agent Framework
After=network.target

[Service]
Type=simple
User=nimbus
WorkingDirectory=/home/nimbus/nimbus
Environment="NIMBUS_HOST=127.0.0.1"
Environment="NIMBUS_PORT=4096"
Environment="NIMBUS_API_TOKEN=your-secret-token-here"
ExecStart=/home/nimbus/nimbus/nimbus start --no-ui
Restart=always
RestartSec=10

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/home/nimbus/nimbus/.nimbus /home/nimbus/nimbus/.logs

[Install]
WantedBy=multi-user.target
```

**启用服务**:

```bash
sudo systemctl daemon-reload
sudo systemctl start nimbus
sudo systemctl enable nimbus
sudo systemctl status nimbus
```

---

### 方案 3: Docker 部署 🐳

**Dockerfile**:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装 uv
RUN pip install uv

# 复制项目文件
COPY . .

# 安装依赖
RUN uv sync

# 暴露端口
EXPOSE 4096

# 设置环境变量
ENV NIMBUS_HOST=0.0.0.0
ENV NIMBUS_PORT=4096

# 启动服务
CMD ["uv", "run", "nimbus", "serve", "--host", "0.0.0.0", "--port", "4096"]
```

**docker-compose.yml**:

```yaml
version: '3.8'

services:
  nimbus:
    build: .
    ports:
      - "4096:4096"
    environment:
      - NIMBUS_API_TOKEN=${NIMBUS_API_TOKEN}
      - GEMINI_API_KEY=${GEMINI_API_KEY}
    volumes:
      - ./data:/app/.nimbus
      - ./logs:/app/.logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4096/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3

  caddy:
    image: caddy:latest
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy_data:/data
      - caddy_config:/config
    restart: unless-stopped
    depends_on:
      - nimbus

volumes:
  caddy_data:
  caddy_config:
```

**启动**:

```bash
# 设置环境变量
export NIMBUS_API_TOKEN=$(openssl rand -hex 32)
export GEMINI_API_KEY=your-key

# 启动
docker-compose up -d

# 查看日志
docker-compose logs -f
```

---

## 安全检查清单 ✅

部署前务必完成：

- [ ] **启用 API 认证**：`NIMBUS_API_TOKEN` 环境变量
- [ ] **使用 HTTPS**：Caddy 自动证书或手动配置
- [ ] **限制 CORS**：不要使用 `allow_origins=["*"]`
- [ ] **配置防火墙**：只开放必要端口（80/443）
- [ ] **速率限制**：防止 API 滥用
- [ ] **日志监控**：记录所有 API 访问
- [ ] **定期更新**：保持依赖最新
- [ ] **备份数据**：定期备份 `.nimbus/` 目录

---

## 快速开始（推荐流程）

### 本地测试（5 分钟）

```bash
# 1. 生成 token
export NIMBUS_API_TOKEN=$(openssl rand -hex 32)
echo "Token: $NIMBUS_API_TOKEN"

# 2. 启动服务（绑定所有接口）
NIMBUS_HOST=0.0.0.0 ./nimbus start

# 3. 测试（从另一台机器）
curl -H "Authorization: Bearer $NIMBUS_API_TOKEN" \
     http://YOUR_IP:4096/api/v1/health
```

### 生产部署（30 分钟）

```bash
# 1. 安装 Caddy
brew install caddy  # macOS
# 或 sudo apt install caddy  # Linux

# 2. 配置域名 DNS
# 添加 A 记录：nimbus.yourdomain.com -> YOUR_SERVER_IP

# 3. 创建 Caddyfile
cat > Caddyfile <<EOF
nimbus.yourdomain.com {
    reverse_proxy localhost:4096
}
EOF

# 4. 启动 Caddy
caddy run --config Caddyfile &

# 5. 启动 Nimbus
export NIMBUS_API_TOKEN=$(openssl rand -hex 32)
NIMBUS_HOST=127.0.0.1 ./nimbus start

# 6. 测试
curl -H "Authorization: Bearer $NIMBUS_API_TOKEN" \
     https://nimbus.yourdomain.com/api/v1/health
```

---

## 常见问题

**Q: 需要开放哪些端口？**
A:
- 如果用 Caddy：只开放 80/443
- 如果直接暴露：开放 4096（不推荐）

**Q: HTTPS 证书怎么办？**
A: Caddy 自动搞定，0 配置

**Q: 如何限制访问来源？**
A:
1. 使用 Caddy 的 IP 白名单
2. 修改 CORS 配置只允许特定域名
3. 使用 VPN/Tailscale

**Q: 性能够吗？**
A:
- 单实例：可处理 100+ 并发会话
- 需要更高：使用 `--workers 4` 多进程
- 极高负载：多机 + 负载均衡

---

## 下一步

根据你的需求选择：

1. **快速测试**: 直接 `NIMBUS_HOST=0.0.0.0 ./nimbus start`
2. **安全部署**: 添加认证 + Caddy 反向代理
3. **生产级**: Docker + Systemd + 监控

需要我帮你实现哪个方案？
