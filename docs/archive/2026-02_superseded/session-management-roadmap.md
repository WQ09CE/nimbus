# Nimbus Session Management Enhancement Roadmap

## 📋 Executive Summary

**Status**: 🟢 **Approved & In Progress** (Phase 1 Started)
**Committee Decision**: Approved with Conditions (Feb 2, 2026)
**Key Constraint**: **NO PICKLE Usage** (Use Pydantic/JSON only)

本文档详细分析了Nimbus当前的session实现，并制定了工业级多session管理、持久化、恢复和隔离的实现计划。

## 🔍 Current State Analysis

### 现有Session实现架构

```ascii
                        Nimbus Session Architecture (Current)
                                      │
                   ┌──────────────────┼──────────────────┐
                   │                  │                  │
           ┌───────▼────────┐ ┌───────▼────────┐ ┌──────▼──────┐
           │ SessionManager │ │ SessionManagerV2│ │SQLiteStorage│
           │   (core)       │ │   (server)      │ │ (storage)   │
           └───────┬────────┘ └───────┬────────┘ └──────┬──────┘
                   │                  │                 │
           ┌───────▼────────┐ ┌───────▼────────┐ ┌──────▼──────┐
           │   SessionEntry │ │    AgentOS     │ │   Tables    │
           │   (JSONL)      │ │  (instances)   │ │(sessions,   │
           │                │ │                │ │ messages)   │
           └────────────────┘ └────────────────┘ └─────────────┘
```

### 现有实现优势

#### ✅ **SessionManager (core/session.py)**
- **JSONL持久化格式** - 类似Pi的设计，简单可靠
- **Tree结构支持** - 分支和回溯，支持复杂对话流
- **完整的Entry类型** - user/assistant/tool_result/compaction等
- **Navigation支持** - 可以跳转到历史任意节点

#### ✅ **SessionManagerV2 (server/session_v2.py)**  
- **AgentOS集成** - 每个session独立的AgentOS实例
- **实时事件流** - SSE hub支持，事件驱动
- **权限管理** - PermissionManager集成
- **LLM client共享** - 资源复用优化

#### ✅ **SQLiteStorage (storage/sqlite.py)**
- **关系型存储** - sessions, messages, dags表结构
- **异步操作** - aiosqlite支持
- **事务支持** - 数据一致性保证

### 现有实现问题

#### ❌ **多Session隔离不完善**
```python
# 问题：多session共享LLM client，可能有状态污染
self._shared_llm_client = adapter  # 共享实例
```

#### ❌ **资源管理不完整**
```python
# 问题：AgentOS实例没有生命周期管理
self._sessions[session_id] = agent_os  # 无限增长
```

#### ❌ **恢复机制不健全**
```python
# 问题：没有从中断点精确恢复的机制
# SessionManager支持navigation，但不支持execution state恢复
```

#### ❌ **并发控制有限**
```python
# 问题：简单的lock，没有考虑复杂并发场景
async with self._lock:  # 粗粒度锁
```

## 🎯 Target Architecture

### 工业级Session管理目标

```ascii
                     Enhanced Multi-Session Management
                                   │
    ┌──────────────────────────────┼──────────────────────────────┐
    │                              │                              │
    │         Session Pool         │        Persistence Layer     │
    │                              │                              │
    │  ┌─────────────────────────┐ │ ┌─────────────────────────┐  │
    │  │   SessionInstance       │ │ │     StateStore          │  │
    │  │   ┌───────────────────┐ │ │ │  ┌─────────────────┐    │  │
    │  │   │   AgentOS         │ │ │ │  │  SessionState   │    │  │
    │  │   │   ┌─────────────┐ │ │ │ │  │  ExecutionState │    │  │
    │  │   │   │    vCPU     │ │ │ │ │  │  MemoryState    │    │  │
    │  │   │   │    MMU      │ │ │ │ │  │  TaskDAGState   │    │  │
    │  │   │   │  Scheduler  │ │ │ │ │  └─────────────────┘    │  │
    │  │   │   └─────────────┘ │ │ │ └─────────────────────────┘  │
    │  │   └───────────────────┘ │ └─────────────────────────────┘  │
    │  │  Resource Isolation     │                                  │
    │  └─────────────────────────┘          Recovery Manager       │
    └──────────────────────────────┼──────────────────────────────┘
                                   │
                    ┌───────────────▼───────────────┐
                    │      Management Layer         │
                    │  ┌─────────────────────────┐  │
                    │  │   SessionController     │  │
                    │  │   LifecycleManager      │  │
                    │  │   ResourceQuota         │  │
                    │  │   ConcurrencyControl    │  │
                    │  └─────────────────────────┘  │
                    └───────────────────────────────┘
```

## 🚀 Implementation Roadmap

### Phase 1: Enhanced Session Persistence (Weeks 1-2)

#### **1.1 State Checkpoint System**
**Status**: ✅ Implemented (Pydantic Models)

```python
# src/nimbus/core/persistence.py
class SessionCheckpointModel(BaseModel):
    """Top-level session checkpoint"""
    schema_version: int = 1
    session_id: str
    timestamp: float
    step_index: int
    
    # Core States (JSON Serializable)
    execution_state: ExecutionStateModel
    memory_snapshot: MemorySnapshotModel
    
    # Metadata
    reason: str
    can_resume: bool
```

#### **1.2 Enhanced SessionEntry Types**
```python
# 扩展现有SessionEntry，添加新类型
EntryType = Literal[
    # 现有类型...
    "checkpoint",      # 检查点
    "interruption",    # 中断点
    "resumption",      # 恢复点
    "resource_event",  # 资源事件
    "isolation_event", # 隔离事件
    "error_boundary",  # 错误边界
]
```

#### **1.3 Atomic State Operations**
```python
class SessionStateManager:
    """原子化的session状态管理"""
    
    async def create_checkpoint(
        self, 
        session_id: str, 
        reason: str = "periodic"
    ) -> str:
        """创建检查点，确保原子性"""
        
    async def restore_from_checkpoint(
        self, 
        session_id: str, 
        checkpoint_id: str
    ) -> bool:
        """从检查点精确恢复"""
        
    async def rollback_to_stable(
        self, 
        session_id: str
    ) -> bool:
        """回滚到最近的稳定状态"""
```

### Phase 2: Multi-Session Resource Management (Weeks 3-4)

#### **2.1 Session Pool Architecture**
```python
class SessionPool:
    """session实例池，管理生命周期和资源"""
    
    def __init__(
        self,
        max_active_sessions: int = 50,
        max_memory_per_session: int = 512 * 1024 * 1024,  # 512MB
        idle_timeout: float = 3600.0,  # 1小时
        checkpoint_interval: float = 300.0,  # 5分钟
    ):
        self._active: Dict[str, SessionInstance] = {}
        self._hibernated: Dict[str, SessionMetadata] = {}
        self._resource_monitor = ResourceMonitor()
        self._lifecycle_manager = LifecycleManager()
        
    async def get_or_create_session(
        self, 
        session_id: str,
        **config
    ) -> SessionInstance:
        """获取或创建session实例"""
        
    async def hibernate_session(self, session_id: str) -> bool:
        """休眠session，释放资源但保持状态"""
        
    async def wake_session(self, session_id: str) -> bool:
        """唤醒休眠的session"""
```

#### **2.2 Resource Isolation**
```python
class SessionInstance:
    """隔离的session实例"""
    
    def __init__(
        self, 
        session_id: str, 
        config: SessionConfig
    ):
        # 独立的AgentOS实例
        self.agent_os: AgentOS = None
        
        # 资源配额
        self.resource_quota = ResourceQuota(
            max_memory=config.max_memory,
            max_cpu_time=config.max_cpu_time,
            max_concurrent_tools=config.max_concurrent_tools,
        )
        
        # 独立的工作空间
        self.workspace = IsolatedWorkspace(session_id)
        
        # 独立的LLM客户端（如果配置要求）
        self.llm_client: Optional[LLMClient] = None
        
        # 状态管理
        self.state_manager = SessionStateManager(session_id)
        
    async def __aenter__(self):
        """资源初始化"""
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """资源清理"""
```

#### **2.3 Concurrency Control**
```python
class SessionConcurrencyController:
    """细粒度并发控制"""
    
    def __init__(self):
        self._session_locks: Dict[str, asyncio.RWLock] = {}
        self._operation_semaphore = asyncio.Semaphore(100)
        self._resource_locks: Dict[str, asyncio.Lock] = {}
        
    async def acquire_session_read(self, session_id: str):
        """获取session读锁"""
        
    async def acquire_session_write(self, session_id: str):
        """获取session写锁"""
        
    async def acquire_resource(self, resource_key: str):
        """获取资源锁（如文件、网络等）"""
```

### Phase 3: Interruption and Recovery (Weeks 5-6)

#### **3.1 Graceful Interruption**
```python
class InterruptionManager:
    """优雅中断管理"""
    
    async def request_interruption(
        self, 
        session_id: str,
        reason: str = "user_request",
        timeout: float = 30.0
    ) -> bool:
        """请求中断，给予清理时间"""
        
    async def force_interruption(self, session_id: str) -> bool:
        """强制中断（最后手段）"""
        
    async def register_interruption_handler(
        self,
        session_id: str,
        handler: Callable[[], Awaitable[None]]
    ):
        """注册中断处理器"""
```

#### **3.2 Resume Capability**
```python
class SessionRecoveryManager:
    """session恢复管理"""
    
    async def can_resume(self, session_id: str) -> Tuple[bool, str]:
        """检查是否可以恢复"""
        
    async def resume_session(
        self, 
        session_id: str,
        from_checkpoint: Optional[str] = None
    ) -> ResumeResult:
        """恢复session执行"""
        
    async def repair_corrupted_state(
        self, 
        session_id: str
    ) -> bool:
        """修复损坏的状态"""
        
    async def get_recovery_options(
        self, 
        session_id: str
    ) -> List[RecoveryOption]:
        """获取恢复选项"""
```

#### **3.3 Progress Tracking**
```python
class SessionProgressTracker:
    """session进度跟踪"""
    
    def track_operation(
        self, 
        session_id: str, 
        operation: str
    ) -> ProgressContext:
        """跟踪操作进度"""
        
    async def get_progress(self, session_id: str) -> ProgressInfo:
        """获取当前进度"""
        
    async def estimate_remaining_time(
        self, 
        session_id: str
    ) -> Optional[float]:
        """估计剩余时间"""
```

### Phase 4: Advanced Features (Weeks 7-8)

#### **4.1 Session Migration**
```python
class SessionMigrationManager:
    """session迁移管理（跨进程、跨机器）"""
    
    async def export_session(
        self, 
        session_id: str
    ) -> SessionExport:
        """导出session完整状态"""
        
    async def import_session(
        self, 
        export_data: SessionExport
    ) -> str:
        """导入session"""
        
    async def clone_session(
        self, 
        source_session_id: str,
        target_session_id: str
    ) -> bool:
        """克隆session"""
```

#### **4.2 Session Analytics**
```python
class SessionAnalytics:
    """session分析和优化"""
    
    async def analyze_performance(
        self, 
        session_id: str
    ) -> PerformanceReport:
        """性能分析"""
        
    async def recommend_optimizations(
        self, 
        session_id: str
    ) -> List[OptimizationSuggestion]:
        """优化建议"""
        
    async def predict_resource_needs(
        self, 
        session_config: SessionConfig
    ) -> ResourcePrediction:
        """资源需求预测"""
```

## 💻 Implementation Details

### Database Schema Extensions

```sql
-- 扩展现有schema
CREATE TABLE session_checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    execution_state BLOB NOT NULL,
    vcpu_state BLOB NOT NULL,
    mmu_snapshot BLOB NOT NULL,
    scheduler_state BLOB NOT NULL,
    workspace_state BLOB NOT NULL,
    reason TEXT NOT NULL,
    can_resume BOOLEAN NOT NULL DEFAULT 1,
    recovery_hints TEXT, -- JSON
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);

CREATE TABLE session_resources (
    session_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    allocated_at REAL NOT NULL,
    quota_limit INTEGER,
    current_usage INTEGER DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES sessions (id),
    PRIMARY KEY (session_id, resource_type, resource_id)
);

CREATE TABLE session_operations (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    started_at REAL NOT NULL,
    completed_at REAL,
    status TEXT NOT NULL DEFAULT 'running',
    progress_info TEXT, -- JSON
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);
```

### Configuration Structure

```python
@dataclass
class EnhancedSessionConfig:
    # 基础配置
    session_id: str
    name: Optional[str] = None
    workspace_path: Optional[str] = None
    
    # 资源配额
    max_memory: int = 512 * 1024 * 1024  # 512MB
    max_cpu_time: float = 3600.0  # 1小时
    max_concurrent_tools: int = 5
    max_file_descriptors: int = 100
    
    # 持久化配置
    checkpoint_interval: float = 300.0  # 5分钟
    auto_save: bool = True
    compression_enabled: bool = True
    
    # 隔离配置
    isolated_workspace: bool = True
    dedicated_llm_client: bool = False
    network_isolation: bool = False
    
    # 恢复配置
    auto_resume: bool = True
    max_resume_attempts: int = 3
    resume_timeout: float = 60.0
    
    # 监控配置
    enable_analytics: bool = True
    enable_progress_tracking: bool = True
    log_level: str = "INFO"
```

## 🔍 Testing Strategy

### Unit Tests
- [ ] SessionPool资源管理
- [ ] SessionInstance隔离
- [ ] Checkpoint创建/恢复
- [ ] 中断/恢复流程
- [ ] 并发控制

### Integration Tests
- [ ] 多session并行执行
- [ ] 资源配额限制
- [ ] 长时间运行恢复
- [ ] 异常情况处理
- [ ] 数据库事务一致性

### Performance Tests
- [ ] 大量session创建/销毁
- [ ] 内存/CPU使用监控
- [ ] Checkpoint性能测试
- [ ] 恢复时间测试
- [ ] 并发性能压测

## 📈 Success Metrics

### 可靠性指标
- **Session恢复成功率** > 99.5%
- **数据丢失率** < 0.01%
- **状态一致性** 100%

### 性能指标
- **Session创建时间** < 100ms
- **Checkpoint时间** < 5s
- **恢复时间** < 30s
- **内存使用** < 配额限制

### 用户体验指标
- **中断响应时间** < 3s
- **进度更新延迟** < 1s
- **并发支持** > 50 sessions

## ⚠️ Risk Mitigation

### 技术风险
- **状态序列化复杂性** → 增量checkpoint + 状态验证
- **并发竞态条件** → 细粒度锁 + 事务隔离
- **内存泄漏** → 定期GC + 资源监控

### 运维风险
- **数据库锁冲突** → 连接池 + 读写分离
- **磁盘空间不足** → 自动清理 + 告警
- **系统资源耗尽** → 动态配额 + 优雅降级

## 🎯 Migration Plan

### 向后兼容
- 现有SessionManager API保持不变
- 逐步迁移到新的SessionPool
- 数据格式兼容现有JSONL

### 迁移步骤
1. **Phase 1**: 新增组件，并行运行
2. **Phase 2**: 增量迁移现有session
3. **Phase 3**: 切换默认实现
4. **Phase 4**: 废弃旧组件

## 🔒 Core Components Stabilization Strategy

### Core组件影响分析

Session管理增强会涉及到部分core组件，但影响范围可控：

#### **涉及的Core组件影响程度**

```ascii
Core Component Impact Analysis:
┌─────────────────────────────────────────────────────────────┐
│ Component        │ Impact Level │ Required Changes           │
├─────────────────────────────────────────────────────────────┤
│ Tools & Gate     │ ⭐ Very Low   │ Optional state save/restore │
│ Error Handler    │ ⭐⭐ Low       │ Session error types        │
│ vCPU            │ ⭐⭐⭐ Medium   │ Checkpoint/restore methods │
│ MMU             │ ⭐⭐⭐⭐ High    │ Memory snapshot/serialization│
└─────────────────────────────────────────────────────────────┘
```

### 固化策略设计

#### **1. vCPU扩展接口**
```python
# 现有vCPU不动，只增加session相关方法
class vCPU:
    # 现有方法保持不变...
    async def think(self, prompt: str) -> str: ...
    async def execute_action(self, action: ActionIR) -> ToolResult: ...
    
    # 新增：session管理相关（可选功能）
    def enable_session_management(self, session_manager):
        """可选启用session管理"""
        self._session_manager = session_manager
        
    async def create_checkpoint(self) -> vCPUState:
        """保存vCPU执行状态"""
        return vCPUState(
            current_step=self._current_step,
            execution_context=self._context,
            pending_actions=self._pending_actions,
            iteration_count=self._iteration_count,
        )
        
    async def restore_from_checkpoint(self, state: vCPUState):
        """从检查点恢复vCPU状态"""
        self._current_step = state.current_step
        self._context = state.execution_context
        self._pending_actions = state.pending_actions
        self._iteration_count = state.iteration_count
        
    async def handle_interruption(self):
        """优雅处理中断请求"""
        # 完成当前操作，保存状态
        await self._complete_current_operation()
        if hasattr(self, '_session_manager'):
            await self._session_manager.create_checkpoint(self)
```

#### **2. MMU扩展接口**
```python
# 现有MMU核心不动，增加可选的序列化功能
class MMU:
    # 现有核心方法保持不变...
    async def retrieve(self, query: str) -> List[Message]: ...
    async def store(self, message: Message): ...
    
    # 新增：可选的状态管理
    def enable_persistence(self, storage_backend):
        """可选启用持久化"""
        self._storage_backend = storage_backend
        
    async def create_memory_snapshot(self) -> MemorySnapshot:
        """创建内存快照"""
        return MemorySnapshot(
            conversation_history=self._conversation_history,
            compressed_summaries=self._compressed_summaries,
            retrieval_index=self._retrieval_index,
            memory_config=self._config,
            timestamp=time.time(),
        )
        
    async def restore_from_snapshot(self, snapshot: MemorySnapshot):
        """从快照恢复内存状态"""
        self._conversation_history = snapshot.conversation_history
        self._compressed_summaries = snapshot.compressed_summaries
        self._retrieval_index = snapshot.retrieval_index
        self._rebuild_internal_structures()
        
    async def serialize_state(self) -> bytes:
        """序列化内存状态用于持久化"""
        snapshot = await self.create_memory_snapshot()
        return pickle.dumps(snapshot)
```

#### **3. Tools扩展接口**
```python
# Tools影响最小，主要是增加状态保存能力
class BaseTool:
    # 现有工具逻辑完全不变...
    async def execute(self, **kwargs) -> ToolResult: ...
    
    # 新增：可选的状态管理
    async def save_state(self) -> Dict[str, Any]:
        """保存工具状态（大部分工具是无状态的）"""
        return {}
        
    async def restore_state(self, state: Dict[str, Any]):
        """恢复工具状态"""
        pass

# 有状态工具的扩展示例
class FileEditTool(BaseTool):
    async def save_state(self) -> Dict[str, Any]:
        """保存文件编辑状态"""
        return {
            "open_files": list(self._open_files.keys()),
            "pending_changes": self._pending_changes,
            "backup_info": self._backup_info,
        }
        
    async def restore_state(self, state: Dict[str, Any]):
        """恢复文件编辑状态"""
        for file_path in state.get("open_files", []):
            await self._reopen_file(file_path)
        self._pending_changes = state.get("pending_changes", {})
        self._backup_info = state.get("backup_info", {})
```

#### **4. Error Handler扩展**
```python
class ErrorHandlerRegistry:
    # 现有错误处理逻辑保持不变...
    async def handle_error(self, error: Exception) -> RecoveryAction: ...
    
    # 新增：session相关错误处理
    async def handle_session_interruption(self, error: InterruptionError):
        """处理会话中断错误"""
        return RecoveryAction(
            action_type="checkpoint_and_pause",
            recovery_hint="Session interrupted by user",
            can_resume=True,
        )
        
    async def handle_recovery_failure(self, error: RecoveryError):
        """处理恢复失败错误"""
        return RecoveryAction(
            action_type="rollback_to_stable",
            recovery_hint=f"Recovery failed: {error.reason}",
            fallback_checkpoint=error.last_stable_checkpoint,
        )
```

### Core固化保护策略

#### **1. 接口稳定性保证**
```python
# 定义稳定的核心接口，永不改变
class StableCoreInterfaces:
    """固化的核心接口，向后兼容保证"""
    
    @abstractmethod
    async def think(self, prompt: str) -> str:
        """核心思考接口 - 永不改变"""
        
    @abstractmethod  
    async def retrieve_memory(self, query: str) -> List[Message]:
        """内存检索接口 - 永不改变"""
        
    @abstractmethod
    async def execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """工具执行接口 - 永不改变"""
        
    @abstractmethod
    async def handle_error(self, error: Exception) -> RecoveryAction:
        """错误处理接口 - 永不改变"""
```

#### **2. 扩展点设计**
```python
# 为未来扩展预留钩子，但不影响现有逻辑
class CoreExtensionPoints:
    """核心组件扩展点"""
    
    # 执行前后钩子
    before_think: List[Callable] = []
    after_think: List[Callable] = []
    before_tool_execution: List[Callable] = []
    after_tool_execution: List[Callable] = []
    
    # 状态变更钩子
    on_state_change: List[Callable] = []
    on_memory_update: List[Callable] = []
    
    # 错误处理钩子
    on_error: List[Callable] = []
    on_recovery: List[Callable] = []
    
    # Session管理钩子
    on_checkpoint_create: List[Callable] = []
    on_session_restore: List[Callable] = []
    on_interruption: List[Callable] = []
```

#### **3. 版本兼容策略**
```python
# 确保Session增强功能是可选的
@dataclass
class CoreComponentConfig:
    # 现有配置保持不变
    max_memory: int = 1000
    timeout: float = 60.0
    max_iterations: int = 100
    
    # 新增：可选的session功能（默认关闭）
    enable_session_checkpoints: bool = False
    enable_state_persistence: bool = False
    enable_interruption_handling: bool = False
    checkpoint_interval: float = 300.0
    
    # 扩展配置（不影响现有功能）
    session_extensions: Dict[str, Any] = field(default_factory=dict)
```

### 具体固化计划

#### **Week 1: 固化Tools和Gate** ✅
```python
# 优先级1：最稳定组件
Tasks:
- [x] 确定tool接口稳定性
- [x] 固化gate系统调用接口  
- [x] 预留状态保存扩展点
- [x] 添加@stable_interface装饰器
```

#### **Week 2: 固化Error Handler** ✅  
```python
# 优先级2：错误处理核心
Tasks:
- [x] 确定错误处理核心逻辑
- [x] 预留session错误类型扩展
- [x] 固化恢复策略接口
- [x] 添加扩展点设计
```

#### **Week 3: 固化vCPU核心逻辑** ⏳
```python
# 优先级3：执行引擎核心
Tasks:
- [ ] 确定Think-Act-Observe循环不变
- [ ] 固化ActionIR解析逻辑
- [ ] 预留checkpoint扩展点
- [ ] 设计可选session管理接口
```

#### **Week 4: 固化MMU核心** ⏳
```python
# 优先级4：内存管理核心
Tasks:
- [ ] 确定内存检索/存储接口
- [ ] 固化压缩算法选择
- [ ] 预留序列化扩展点
- [ ] 设计snapshot机制接口
```

### 推荐固化方案

#### **Option 1: 立即固化（推荐）** ✅
```bash
# 1. 立即冻结核心接口
git branch feature/core-freeze
git checkout feature/core-freeze

# 2. 添加"不可变"标记
touch src/nimbus/core/stable_interfaces.py
touch src/nimbus/core/extension_points.py

# 3. 为session功能预留扩展点
# 但不改变现有核心逻辑
```

#### **实施步骤**：
1. **创建稳定接口定义文件**
2. **为核心组件添加@stable_interface装饰器**
3. **设计可选扩展机制**
4. **确保向后兼容性测试**
5. **文档化固化策略**

### 固化效果保证

#### **兼容性保证**
- ✅ 现有API永不破坏
- ✅ Session功能为可选扩展
- ✅ 核心逻辑行为不变
- ✅ 性能影响可控

#### **扩展性保证**  
- ✅ 预留充足扩展点
- ✅ 钩子机制完善
- ✅ 状态管理接口清晰
- ✅ 配置驱动的功能开关

---

**总结**: 通过精心设计的扩展接口和固化策略，我们可以**立即固化core代码**，同时为Session管理功能预留充足的扩展空间。核心原则是"现有不变，扩展可选"，确保系统稳定性和向后兼容性。这个roadmap将Nimbus的session管理提升到工业级水准，实现真正的多session隔离、可靠的持久化和完善的恢复机制。