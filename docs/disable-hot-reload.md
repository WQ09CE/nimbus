# 禁用热重载配置说明

## 背景

为了避免在与 AI 聊天过程中，AI 修改前端代码导致页面自动刷新中断对话，我们禁用了 Next.js 的热重载功能。

## 已实施的更改

### 1. Next.js 配置更改 (`web-ui/next.config.mjs`)

```javascript
webpack: (config, { dev, isServer }) => {
  if (dev && !isServer) {
    // 禁用 Fast Refresh (React Hot Reload)
    config.watchOptions = {
      ignored: ['**/*'],  // 忽略所有文件变化
    };
  }
  return config;
},
```

### 2. 启动脚本更改 (`./nimbus`)

- 添加环境变量 `FAST_REFRESH=false`
- 新增 `reload-ui` 命令用于手动重启

```bash
# 启动时禁用热重载
FAST_REFRESH=false npm run dev

# 手动重载命令
./nimbus reload-ui
```

## 使用说明

### 正常开发流程

1. **启动服务**:
   ```bash
   ./nimbus start
   ```

2. **进行代码修改**: 
   - 修改 `web-ui/src/` 下的任何文件
   - 页面**不会**自动刷新

3. **查看更改**:
   ```bash
   ./nimbus reload-ui
   ```
   这会重启 web-ui 服务，加载最新代码

### 可用命令

```bash
./nimbus start         # 启动所有服务（热重载已禁用）
./nimbus reload-ui     # 仅重启 web-ui（应用代码更改）
./nimbus stop          # 停止所有服务  
./nimbus status        # 查看服务状态
./nimbus logs webui    # 查看 web-ui 日志
```

## 优势

✅ **聊天不中断**: 与 AI 对话时不会因代码更改而刷新页面  
✅ **状态保持**: 表单输入、滚动位置等都会保留  
✅ **手动控制**: 需要查看更改时，手动重载即可  
✅ **开发友好**: `reload-ui` 命令快速且专门针对前端

## 注意事项

⚠️ **需要手动重载**: 代码更改后需要运行 `./nimbus reload-ui` 才能看到效果  
⚠️ **调试时记住**: 如果页面行为异常，先尝试 `reload-ui` 确保是最新代码  

## 如果需要恢复热重载

1. 编辑 `web-ui/next.config.mjs`，删除 `webpack` 配置
2. 编辑 `./nimbus`，移除 `FAST_REFRESH=false` 环境变量
3. 重启服务：`./nimbus restart`

## 相关文件

- `web-ui/next.config.mjs` - Next.js 配置文件
- `./nimbus` - 启动脚本  
- `docs/disable-hot-reload.md` - 本说明文档