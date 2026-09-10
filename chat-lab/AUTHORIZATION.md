# Dennis 上线后需要授权的事项

本文件保留授权门槛。2026-09-10 更新：token 已安全落盘，`identify` 已验证 Telegram 连接；
经 Dennis 授权，已安装 PostgreSQL 18.6 并启动专用 user service，受限应用角色、schema 和 DSN 已配置。
初始空库备份／恢复检查通过，但不是定时或异地备份。精确私聊身份仍待人工确认，allowlist 为空，
gateway／worker 模板已安装但未启用。没有启用代码工具、群聊权限或用户 lingering。

## 1. Telegram 身份与 token

- 用你的 Telegram 账号在 **BotFather** 创建一个新的私人 Nimbus bot。
- token 放到本机 `~/.config/nimbus-chat-lab/telegram-token`：目录 0700、文件 0600。
  **不要贴到聊天记录、仓库、命令行参数或截图里。**
- 给新 bot 私聊发一次 `/start`，网关保持关闭，然后可由我们执行：

  ```bash
  export NIMBUS_TG_TOKEN_FILE="$HOME/.config/nimbus-chat-lab/telegram-token"
  cd ~/Projects/nimbus-telegram/chat-lab
  uv run nimbus-chat-lab identify
  ```

  只输出 bot/user/chat 数字 ID，不打印消息正文，不推进 polling offset，也不自动授权。
  你确认哪个是自己的身份后，才写入精确 allowlist。
- 首次只开私聊。群聊加入、群 chat ID 和允许调用的 user ID 另行确认；privacy mode 保持开启。
- 不抢占已有 bot 的 poller，也不擅自删除 webhook。

## 2. 常驻 PostgreSQL 与用户服务

- 已部署专用 PostgreSQL 18.6，仅 Unix socket；系统默认 `postgresql.service` 未启动。
- 数据目录、无登录 owner／受限 app 角色和初始备份已分离，详见 [部署说明](deploy/LOCAL_POSTGRES.md)。
  `pgserver` 自带 PG 16.2 仍只用于合成测试；定时／异地备份、内容保留和磁盘告警仍待落实。
- PG user unit 已启用；确认数字私聊身份后才启用 gateway 和 worker A，真实对话成功后再开 B。
  `Linger=no`，目前只保证用户登录期间的服务生命周期，无人登录常驻另行确认。
- worker 的 Pi/Codex 通路已用真实 Astra 验证，不需要额外 API key。服务用户与登录用户不同时需正常重新授权，不能复制别人 token。

## 3. 开代码工具前的系统权限门槛

当前主机没有 `podman` / `runsc`，Docker socket 对当前用户拒绝访问；没有尝试 sudo 或放宽 socket 权限。

需要你确认安装 rootless Podman＋gVisor 的方式、用户 namespace/cgroup 配置及资源预算。
真正启用前必须证明：实际 runtime 是 runsc、无宿主敏感挂载／token、网络和资源约束有效、所有文件与 shell 工具都走沙箱、取消/TTL/操作 ID 与清理符合要求。

**在此之前，Telegram bot 的工具集保持空，绝不回退为本地 Bash。** 开发用 computer use 独立可用，不受此授权阻塞，也不会被转交给 bot 用户。

## 4. 最后实机验收

有 token 后做一次真实 Telegram 对话、draft、`/status`、`/cancel`、`/new`，再做一次中断后的真实 follow-up。
现在的 Telegram 测试使用模拟传输，不会冒充已通过真实平台验收。

上线后还需确认内容保留期、磁盘告警和备份恢复；不把公司代码或凭据送入私人 Telegram bot。
