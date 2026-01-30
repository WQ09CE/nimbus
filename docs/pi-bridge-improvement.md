# Pi Bridge 连接改进方案

## 当前问题

### 1. 架构概览

```
┌─────────────┐     stdin/stdout      ┌─────────────┐      HTTP       ┌─────────┐
│   Nimbus    │◄────────────────────►│  pi-bridge  │◄───────────────►│  pi-ai  │
│   Server    │      JSON-RPC         │   (Node)    │                 │  Cloud  │
└─────────────┘                       └─────────────┘                 └─────────┘
     Python                               TypeScript                    Anthropic
```

### 2. 当前实现的问题

| 问题 | 描述 | 影响 |
|------|------|------|
| **无超时** | `_call()` 和 `_read_line()` 没有超时 | 请求可能无限等待，导致"连接中"卡死 |
| **无心跳** | 不知道子进程是否还活着 | 子进程崩溃后无法感知 |
| **无重连** | 连接断开后不会恢复 | 需要重启整个 server |
| **阻塞 I/O** | 使用 `run_in_executor` 读写 stdin/stdout | 线程池可能耗尽 |
| **无状态管理** | 没有连接状态追踪 | UI 无法显示正确的连接状态 |
| **无请求取消** | 无法取消正在进行的请求 | 用户无法中断长时间运行的请求 |

### 3. 问题代码示例

```python
# 当前实现 - 可能无限等待
async def _call(self, method: str, params: dict | None = None) -> Any:
    self._send_request(method, params)
    async for data in self._read_messages():  # ← 没有超时！
        if "id" in data:
            return data.get("result")
    raise RuntimeError("No response received")  # ← 永远不会执行

# 当前实现 - 阻塞线程池
async def _read_line(self) -> str | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, self._process.stdout.readline)  # ← 阻塞
```

## 改进方案

### 方案 A: 最小改动 - 添加超时和健康检查

**适用场景**：快速修复，不改变架构

```python
class PiClient:
    DEFAULT_TIMEOUT = 60.0  # 秒
    HEALTH_CHECK_INTERVAL = 30.0
    
    async def _call(self, method: str, params: dict | None = None, timeout: float = None) -> Any:
        """带超时的 RPC 调用"""
        timeout = timeout or self.DEFAULT_TIMEOUT
        self._send_request(method, params)
        
        try:
            async with asyncio.timeout(timeout):
                async for data in self._read_messages():
                    if "id" in data:
                        if data.get("error"):
                            raise RpcError(data["error"])
                        return data.get("result")
        except asyncio.TimeoutError:
            # 超时后检查子进程状态
            if self._process.poll() is not None:
                raise ConnectionError("pi-bridge process died")
            raise TimeoutError(f"RPC call '{method}' timed out after {timeout}s")
    
    async def _health_check_loop(self):
        """后台健康检查"""
        while self._running:
            try:
                await self._call("ping", timeout=5.0)
                self._healthy = True
            except Exception as e:
                self._healthy = False
                logger.warning(f"Health check failed: {e}")
                await self._try_reconnect()
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)
    
    async def _try_reconnect(self):
        """尝试重连"""
        logger.info("Attempting to reconnect to pi-bridge...")
        await self.stop()
        await asyncio.sleep(1)
        await self.start()
```

**改动点**：
- `_call()` 添加 `asyncio.timeout()`
- 添加后台健康检查任务
- 添加 `_try_reconnect()` 方法

### 方案 B: 中等改动 - 使用 asyncio subprocess

**适用场景**：解决阻塞问题，提高并发性能

```python
class PiClient:
    async def start(self):
        """使用 asyncio subprocess"""
        if self._bridge_path.endswith(".ts"):
            cmd = ["npx", "tsx", self._bridge_path]
        else:
            cmd = [self._node_path, self._bridge_path]
        
        # 使用 asyncio subprocess - 非阻塞
        self._process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # 启动读取任务
        self._reader_task = asyncio.create_task(self._reader_loop())
        
        # 验证连接
        result = await self._call("ping", timeout=5.0)
        if not result.get("pong"):
            raise RuntimeError("Failed to connect to pi-bridge")
    
    async def _reader_loop(self):
        """非阻塞消息读取循环"""
        while self._running:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    break
                data = json.loads(line.decode())
                await self._dispatch_message(data)
            except Exception as e:
                logger.error(f"Reader error: {e}")
                break
        
        self._healthy = False
        logger.warning("Reader loop ended, connection lost")
    
    async def _dispatch_message(self, data: dict):
        """分发消息到等待的请求"""
        if "id" in data:
            # RPC 响应
            request_id = data["id"]
            if request_id in self._pending_requests:
                future = self._pending_requests.pop(request_id)
                future.set_result(data)
        else:
            # 事件/通知
            await self._handle_notification(data)
```

**改动点**：
- 使用 `asyncio.create_subprocess_exec()` 替代 `subprocess.Popen()`
- 添加独立的 reader loop
- 使用 Future 管理 pending requests

### 方案 C: 大改动 - 完整的连接管理

**适用场景**：生产级别的稳定性要求

```python
class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class PiBridgeConnection:
    """完整的连接管理器"""
    
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.state = ConnectionState.DISCONNECTED
        self._process: asyncio.subprocess.Process | None = None
        self._pending_requests: Dict[int, asyncio.Future] = {}
        self._request_id = 0
        self._reconnect_attempts = 0
        
        # 事件
        self.on_state_change: Callable[[ConnectionState], None] | None = None
        self.on_message: Callable[[dict], None] | None = None
    
    async def connect(self):
        """建立连接"""
        self._set_state(ConnectionState.CONNECTING)
        
        try:
            await self._start_process()
            await self._verify_connection()
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_attempts = 0
            
            # 启动后台任务
            self._reader_task = asyncio.create_task(self._reader_loop())
            self._health_task = asyncio.create_task(self._health_loop())
            
        except Exception as e:
            self._set_state(ConnectionState.ERROR)
            raise ConnectionError(f"Failed to connect: {e}")
    
    async def call(
        self, 
        method: str, 
        params: dict | None = None,
        timeout: float | None = None,
    ) -> Any:
        """带完整错误处理的 RPC 调用"""
        if self.state != ConnectionState.CONNECTED:
            raise ConnectionError(f"Not connected (state={self.state})")
        
        timeout = timeout or self.config.default_timeout
        request_id = self._next_request_id()
        
        # 创建 Future 等待响应
        future = asyncio.Future()
        self._pending_requests[request_id] = future
        
        try:
            # 发送请求
            self._send_request(request_id, method, params)
            
            # 等待响应
            async with asyncio.timeout(timeout):
                response = await future
            
            if response.get("error"):
                raise RpcError(response["error"])
            
            return response.get("result")
            
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise TimeoutError(f"RPC '{method}' timed out after {timeout}s")
        
        except asyncio.CancelledError:
            self._pending_requests.pop(request_id, None)
            raise
    
    async def _health_loop(self):
        """健康检查循环"""
        while self.state == ConnectionState.CONNECTED:
            await asyncio.sleep(self.config.health_check_interval)
            
            try:
                await self.call("ping", timeout=self.config.health_check_timeout)
            except Exception as e:
                logger.warning(f"Health check failed: {e}")
                await self._handle_connection_lost()
                break
    
    async def _handle_connection_lost(self):
        """处理连接丢失"""
        if self.config.auto_reconnect:
            self._set_state(ConnectionState.RECONNECTING)
            await self._reconnect_with_backoff()
        else:
            self._set_state(ConnectionState.DISCONNECTED)
    
    async def _reconnect_with_backoff(self):
        """指数退避重连"""
        while self._reconnect_attempts < self.config.max_reconnect_attempts:
            self._reconnect_attempts += 1
            delay = min(
                self.config.reconnect_base_delay * (2 ** self._reconnect_attempts),
                self.config.reconnect_max_delay
            )
            
            logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_attempts})")
            await asyncio.sleep(delay)
            
            try:
                await self.connect()
                return
            except Exception as e:
                logger.warning(f"Reconnect failed: {e}")
        
        self._set_state(ConnectionState.ERROR)
        raise ConnectionError("Max reconnect attempts exceeded")


@dataclass
class ConnectionConfig:
    """连接配置"""
    bridge_path: str | None = None
    default_timeout: float = 60.0
    health_check_interval: float = 30.0
    health_check_timeout: float = 5.0
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 5
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 30.0
```

**改动点**：
- 完整的状态机
- 事件回调机制
- 指数退避重连
- 可配置的超时和重试参数

## 推荐实施路径

### 第一阶段：紧急修复（1-2 小时）

1. 给 `_call()` 添加 `asyncio.timeout()`
2. 给 `_read_line()` 添加超时检查
3. 检测子进程状态

```python
# 最小修复
async def _call(self, method: str, params: dict | None = None, timeout: float = 60.0) -> Any:
    self._send_request(method, params)
    
    try:
        async with asyncio.timeout(timeout):
            async for data in self._read_messages():
                if "id" in data:
                    if data.get("error"):
                        raise RuntimeError(f"RPC Error: {data['error']}")
                    return data.get("result")
    except asyncio.TimeoutError:
        # 检查子进程是否还活着
        if self._process and self._process.poll() is not None:
            raise ConnectionError("pi-bridge process died unexpectedly")
        raise TimeoutError(f"RPC call '{method}' timed out after {timeout}s")
    
    raise RuntimeError("No response received")
```

### 第二阶段：稳定性改进（1-2 天）

1. 实现方案 B：使用 asyncio subprocess
2. 添加健康检查
3. 添加基本的重连机制

### 第三阶段：生产就绪（1 周）

1. 实现方案 C：完整的连接管理
2. 添加监控指标
3. 添加连接状态 API（供 UI 显示）
4. 添加单元测试和集成测试

## UI 改进建议

### 连接状态显示

```typescript
// web-ui 可以显示连接状态
interface ConnectionStatus {
  state: 'connected' | 'connecting' | 'reconnecting' | 'error';
  lastPing?: number;  // 最后一次心跳时间
  error?: string;
}

// 在 UI 中显示
<ConnectionIndicator status={connectionStatus} />
```

### 超时提示

```typescript
// 当请求超时时，显示友好提示
if (error.type === 'timeout') {
  showToast({
    type: 'warning',
    message: '请求超时，正在重试...',
    action: { label: '取消', onClick: cancelRequest }
  });
}
```

## 测试用例

```python
# tests/test_pi_client.py

@pytest.mark.asyncio
async def test_call_timeout():
    """测试 RPC 调用超时"""
    client = PiClient()
    await client.start()
    
    # 模拟慢响应
    with pytest.raises(TimeoutError):
        await client._call("slow_method", timeout=0.1)

@pytest.mark.asyncio
async def test_auto_reconnect():
    """测试自动重连"""
    client = PiClient(auto_reconnect=True)
    await client.start()
    
    # 模拟连接丢失
    client._process.kill()
    await asyncio.sleep(2)
    
    # 应该自动重连
    assert client.state == ConnectionState.CONNECTED

@pytest.mark.asyncio
async def test_health_check():
    """测试健康检查"""
    client = PiClient(health_check_interval=1.0)
    await client.start()
    
    await asyncio.sleep(2)
    assert client._healthy == True
```

## 监控指标

```python
# 添加 Prometheus 风格的指标
class PiClientMetrics:
    requests_total = Counter("pi_bridge_requests_total", "Total RPC requests")
    requests_failed = Counter("pi_bridge_requests_failed", "Failed RPC requests")
    request_duration = Histogram("pi_bridge_request_duration_seconds", "Request duration")
    reconnects_total = Counter("pi_bridge_reconnects_total", "Total reconnection attempts")
    connection_state = Gauge("pi_bridge_connection_state", "Current connection state")
```

## 总结

| 方案 | 工作量 | 稳定性提升 | 推荐场景 |
|------|--------|-----------|----------|
| A: 最小改动 | 1-2 小时 | ⭐⭐ | 紧急修复 |
| B: asyncio subprocess | 1-2 天 | ⭐⭐⭐ | 短期改进 |
| C: 完整连接管理 | 1 周 | ⭐⭐⭐⭐⭐ | 生产就绪 |

**建议**：先实施方案 A 解决当前的卡死问题，然后逐步实施方案 B 和 C。
