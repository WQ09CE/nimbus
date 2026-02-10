# Nimbus 部署文件

将 Nimbus 部署为远程可访问的服务（Web UI + Discord Bot）。

---

## 🚀 快速开始

### 方式 1: 一键部署（推荐）

```bash
# 开发环境（本地测试）
./deploy/setup.sh development

# 生产环境（公网访问）
./deploy/setup.sh production
```

### 方式 2: 手动部署

详见 [DEPLOY.md](./DEPLOY.md) 完整指南。

---

## 📁 文件说明

| 文件 | 用途 |
|------|------|
| `setup.sh` | 一键部署脚本 |
| `DEPLOY.md` | 详细部署指南 |
| `nginx.conf` | Nginx 反向代理配置 |
| `com.nimbus.server.plist` | macOS launchd 服务配置 |
| `nimbus.service` | Linux systemd 服务配置 |
| `discord-bot.ts` | Discord Bot 实现 (在 `../bridge/`) |

---

## 🎯 部署目标

```
Internet → Nginx (erqing.wang)
             ├─ /         → Web UI (3000)
             ├─ /api      → Nimbus Server (4096)
             └─ Discord Bot → Nimbus API (4096)
```

---

## ⚡ 快速命令

### 服务管理

```bash
# 查看状态
./nimbus status

# 查看日志
./nimbus logs

# 重启服务
./nimbus restart
```

### Discord Bot

```bash
# 启动 Bot
screen -dmS nimbus-discord bash -c "cd bridge && npx tsx discord-bot.ts >> ../.logs/discord-bot.log 2>&1"

# 查看日志
tail -f .logs/discord-bot.log

# 停止 Bot
screen -S nimbus-discord -X quit
```

---

## 🔧 环境变量

创建项目根目录的 `.env` 文件：

```bash
# Discord Bot
DISCORD_BOT_TOKEN=your_token_here
BOT_PREFIX=!nimbus

# Nimbus API
NIMBUS_API_URL=http://localhost:4096

# Ports
PI_AI_PORT=3031
NIMBUS_PORT=4096
WEBUI_PORT=3000
```

---

## 🐛 故障排查

### 服务无法启动

```bash
# 检查端口占用
lsof -i :3000
lsof -i :4096

# 查看详细日志
tail -f .logs/nimbus.log
```

### Discord Bot 无响应

```bash
# 检查 Bot Token 是否正确
cat .env | grep DISCORD_BOT_TOKEN

# 检查 Nimbus API 连通性
curl http://localhost:4096/health

# 重启 Bot
screen -S nimbus-discord -X quit
screen -dmS nimbus-discord bash -c "cd bridge && npx tsx discord-bot.ts >> ../.logs/discord-bot.log 2>&1"
```

### Nginx 配置错误

```bash
# 测试配置
sudo nginx -t

# 查看错误日志
tail -f /var/log/nginx/nimbus-error.log
```

---

## 📚 文档

- **完整部署指南**: [DEPLOY.md](./DEPLOY.md)
- **Nimbus 主文档**: [../README.md](../README.md)
- **Web UI 指南**: [../WEB_UI_GUIDE.md](../WEB_UI_GUIDE.md)

---

## 🔒 安全提示

- 不要提交 `.env` 文件到 Git
- 不要泄露 Discord Bot Token
- 生产环境建议启用 HTTPS
- 使用防火墙限制访问

---

**创建时间**: 2026-02-09  
**维护**: Wukong 🐒
