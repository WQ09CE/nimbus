# Nimbus 远程部署指南

将 Nimbus 部署为可通过网页和 Discord 远程访问的服务。

---

## 🎯 目标架构

```
Internet
   ↓
erqing.wang (Nginx)
   ├─→ /         → Web UI (3000)
   ├─→ /api      → Nimbus Server (4096)
   └─→ Discord Bot → Nimbus API (4096)
```

---

## 📋 前置要求

### 系统依赖
```bash
# macOS
brew install nginx

# Linux
sudo apt install nginx
```

### Node.js 依赖
```bash
cd bridge
npm install discord.js node-fetch
```

### 环境变量
创建 `.env` 文件：
```bash
# Discord Bot
DISCORD_BOT_TOKEN=your_bot_token_here
BOT_PREFIX=!nimbus

# Nimbus API
NIMBUS_API_URL=http://localhost:4096

# OpenWrt 公网访问
PUBLIC_IP=125.118.182.100
INTERNAL_IP=192.168.2.240
```

---

## 🚀 Step 1: 配置服务自启动

### macOS (launchd)

```bash
# 1. 复制 plist 文件
sudo cp deploy/com.nimbus.server.plist /Library/LaunchDaemons/

# 2. 加载服务
sudo launchctl load /Library/LaunchDaemons/com.nimbus.server.plist

# 3. 启动服务
sudo launchctl start com.nimbus.server

# 查看状态
sudo launchctl list | grep nimbus
```

### Linux (systemd)

```bash
# 1. 复制 service 文件
sudo cp deploy/nimbus.service /etc/systemd/system/

# 2. 重载配置
sudo systemctl daemon-reload

# 3. 启动并启用服务
sudo systemctl enable nimbus.service
sudo systemctl start nimbus.service

# 查看状态
sudo systemctl status nimbus
```

---

## 🌐 Step 2: 配置 Nginx 反向代理

### macOS (Homebrew Nginx)

```bash
# 1. 复制配置文件
sudo cp deploy/nginx.conf /usr/local/etc/nginx/servers/nimbus.conf

# 2. 测试配置
sudo nginx -t

# 3. 重载 Nginx
sudo nginx -s reload
```

### Linux

```bash
# 1. 复制配置文件
sudo cp deploy/nginx.conf /etc/nginx/sites-available/nimbus

# 2. 创建软链接
sudo ln -s /etc/nginx/sites-available/nimbus /etc/nginx/sites-enabled/

# 3. 测试配置
sudo nginx -t

# 4. 重载 Nginx
sudo systemctl reload nginx
```

### 修改 Web UI API 地址

编辑 `web-ui/.env.local`：
```bash
# 开发环境（本地）
NEXT_PUBLIC_API_BASE_URL=http://localhost:4096

# 生产环境（公网）
NEXT_PUBLIC_API_BASE_URL=http://erqing.wang/api
```

---

## 🤖 Step 3: 部署 Discord Bot

### 3.1 创建 Discord Bot

1. 访问 [Discord Developer Portal](https://discord.com/developers/applications)
2. 点击 "New Application" 创建应用
3. 进入 "Bot" 页面，点击 "Add Bot"
4. 复制 Token，保存到 `.env` 文件
5. 启用 "MESSAGE CONTENT INTENT"

### 3.2 邀请 Bot 到服务器

使用以下 URL（替换 YOUR_CLIENT_ID）：
```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=3072&scope=bot
```

权限说明：
- `3072` = Send Messages + Read Message History

### 3.3 启动 Bot

#### 方式 A: 使用 screen（推荐）

```bash
screen -dmS nimbus-discord bash -c "cd bridge && npx tsx discord-bot.ts >> ../.logs/discord-bot.log 2>&1"

# 查看日志
tail -f .logs/discord-bot.log

# 进入 screen
screen -r nimbus-discord
```

#### 方式 B: 使用 PM2

```bash
# 安装 PM2
npm install -g pm2

# 启动
pm2 start bridge/discord-bot.ts --name nimbus-discord --interpreter npx --interpreter-args tsx

# 查看状态
pm2 status

# 查看日志
pm2 logs nimbus-discord

# 开机自启
pm2 startup
pm2 save
```

---

## 🔒 Step 4: 配置 OpenWrt 端口转发

登录 OpenWrt 路由器：
```bash
ssh root@192.168.2.1
# 密码: Wq917352!
```

### 添加端口转发规则

**方式 A: Web 界面**
1. 访问 http://192.168.2.1
2. Network → Firewall → Port Forwards
3. 添加规则：
   - Name: `Nimbus-WebUI`
   - Protocol: `TCP`
   - External port: `3000`
   - Internal IP: `192.168.2.240`
   - Internal port: `3000`
4. 重复添加 API 规则（4096 端口）

**方式 B: 命令行**
```bash
# Web UI (3000)
uci add firewall redirect
uci set firewall.@redirect[-1].name='Nimbus-WebUI'
uci set firewall.@redirect[-1].src='wan'
uci set firewall.@redirect[-1].src_dport='3000'
uci set firewall.@redirect[-1].dest='lan'
uci set firewall.@redirect[-1].dest_ip='192.168.2.240'
uci set firewall.@redirect[-1].dest_port='3000'
uci set firewall.@redirect[-1].proto='tcp'

# Nimbus API (4096)
uci add firewall redirect
uci set firewall.@redirect[-1].name='Nimbus-API'
uci set firewall.@redirect[-1].src='wan'
uci set firewall.@redirect[-1].src_dport='4096'
uci set firewall.@redirect[-1].dest='lan'
uci set firewall.@redirect[-1].dest_ip='192.168.2.240'
uci set firewall.@redirect[-1].dest_port='4096'
uci set firewall.@redirect[-1].proto='tcp'

# 提交配置
uci commit firewall
/etc/init.d/firewall restart
```

---

## ✅ Step 5: 验证部署

### 本地测试

```bash
# 1. 检查服务状态
./nimbus status

# 2. 测试 API
curl http://localhost:4096/health

# 3. 访问 Web UI
open http://localhost:3000
```

### 远程测试

```bash
# 1. 测试公网访问
curl http://erqing.wang/api/health

# 2. 访问 Web UI
open http://erqing.wang

# 3. 测试 Discord Bot
# 在 Discord 频道发送: !nimbus hello
```

---

## 🔧 维护命令

### 服务管理

```bash
# macOS
sudo launchctl stop com.nimbus.server
sudo launchctl start com.nimbus.server
sudo launchctl unload /Library/LaunchDaemons/com.nimbus.server.plist

# Linux
sudo systemctl stop nimbus
sudo systemctl start nimbus
sudo systemctl restart nimbus
sudo systemctl status nimbus
```

### 查看日志

```bash
# Nimbus 服务日志
tail -f .logs/nimbus.log

# Discord Bot 日志
tail -f .logs/discord-bot.log

# Nginx 日志
tail -f /var/log/nginx/nimbus-access.log
tail -f /var/log/nginx/nimbus-error.log
```

### 更新代码

```bash
# 1. 停止服务
sudo launchctl stop com.nimbus.server  # macOS
sudo systemctl stop nimbus              # Linux

# 2. 拉取最新代码
git pull

# 3. 安装依赖
cd web-ui && npm install
cd ../bridge && npm install

# 4. 启动服务
sudo launchctl start com.nimbus.server  # macOS
sudo systemctl start nimbus             # Linux

# 5. 重启 Discord Bot
screen -S nimbus-discord -X quit
screen -dmS nimbus-discord bash -c "cd bridge && npx tsx discord-bot.ts >> ../.logs/discord-bot.log 2>&1"
```

---

## 🐛 故障排查

### 问题 1: Web UI 无法访问

```bash
# 检查端口是否监听
lsof -i :3000

# 检查 Nginx 配置
sudo nginx -t

# 查看 Nginx 错误日志
tail -f /var/log/nginx/nimbus-error.log
```

### 问题 2: Discord Bot 无响应

```bash
# 查看 Bot 日志
tail -f .logs/discord-bot.log

# 检查 Nimbus API 连通性
curl http://localhost:4096/health

# 重启 Bot
screen -S nimbus-discord -X quit
screen -dmS nimbus-discord bash -c "cd bridge && npx tsx discord-bot.ts >> ../.logs/discord-bot.log 2>&1"
```

### 问题 3: SSE 连接断开

检查 Nginx SSE 配置：
```nginx
proxy_set_header Connection '';
proxy_set_header X-Accel-Buffering no;
proxy_buffering off;
proxy_cache off;
```

---

## 🔒 安全建议

1. **启用 HTTPS**
   - 使用 Let's Encrypt 申请免费证书
   - 配置 Nginx SSL

2. **限制 API 访问**
   - 添加 API Key 认证
   - 使用 Nginx 限流

3. **防火墙配置**
   - 仅开放必要端口（80, 443）
   - 使用 fail2ban 防止暴力攻击

4. **Discord Bot 安全**
   - 不要泄露 Bot Token
   - 限制 Bot 权限为最小必要权限

---

## 📚 相关文档

- [Nimbus README](../README.md)
- [Web UI Guide](../WEB_UI_GUIDE.md)
- [Discord.js Documentation](https://discord.js.org/)
- [Nginx Reverse Proxy Guide](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)

---

**创建时间**: 2026-02-09  
**作者**: Wukong 🐒
