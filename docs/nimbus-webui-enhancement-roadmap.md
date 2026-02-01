# Nimbus Web-UI 增强施工计划
借鉴 Pi Coding Agent TUI 的优秀设计

## 项目背景

通过深入分析 Pi Coding Agent 的 TUI 实现，我们发现了几个核心优势：

1. **透明的 AI 工作过程** - 用户能清晰看到 AI 的思考和决策过程
2. **工具特定渲染** - 每种工具都有优化的显示方式
3. **流畅的流式更新** - 真正的实时字符级更新
4. **思考等级可视化** - 6级思考强度的直观展示

正如 Mario 在博客中提到的核心理念：**维护两份数据流** - 一份是 LLM 上下文，一份是专门给 UI 展示的人类可读版本。

## 核心设计原则

### 双流架构 (Dual Stream Architecture)
```
LLM Context Stream (Machine)     →     UI Display Stream (Human)
├─ Raw tool calls                      ├─ Formatted tool displays  
├─ Complete thinking traces            ├─ Progressive thinking reveal
├─ Full error context                  ├─ User-friendly error messages
└─ Technical metadata                  └─ Visual status indicators
```

这种分离确保：
- LLM 获得完整上下文用于推理
- 用户界面保持清晰和专注
- 可以独立优化两个流的性能

## 实施阶段

### 🎯 阶段一：工具特定渲染系统 (2-3周)

#### 1.1 工具渲染框架设计
```typescript
// 建立工具渲染器接口
interface ToolRenderer {
  renderCall(call: ToolCall): ReactNode;
  renderProgress(call: ToolCall, partial: any): ReactNode;
  renderResult(call: ToolCall, result: ToolResult): ReactNode;
  renderError(call: ToolCall, error: string): ReactNode;
}

// 工具特定渲染器
const toolRenderers: Record<string, ToolRenderer> = {
  'Read': new FileReadRenderer(),
  'Write': new FileWriteRenderer(), 
  'Edit': new FileDiffRenderer(),
  'Bash': new BashExecutionRenderer(),
  'Glob': new FileSearchRenderer(),
};
```

#### 1.2 核心工具渲染器实现

**FileReadRenderer** - 文件读取专用显示
- 📁 文件路径简化显示（~/project/file.py）
- 📊 文件大小和行数信息
- 🎨 语法高亮预览
- 📜 智能截断（基于视觉行数）

**FileDiffRenderer** - 代码差异专用显示
- ➕➖ 添加/删除行的颜色区分
- 📍 行号对齐显示
- 🔍 上下文行数控制
- 📁 文件路径和修改摘要

**BashExecutionRenderer** - 命令执行专用显示
- 💻 命令提示符样式
- ⚡ 实时输出流式显示
- 🚦 退出码状态指示器
- 📏 输出长度智能截断

#### 1.3 渲染器系统集成
```typescript
// 动态渲染器选择
function ToolDisplay({ tool }: { tool: ToolCall }) {
  const renderer = toolRenderers[tool.name] || new DefaultToolRenderer();
  
  return (
    <div className="tool-execution">
      {renderer.renderCall(tool)}
      {tool.isRunning && renderer.renderProgress(tool, tool.partialResult)}
      {tool.result && renderer.renderResult(tool, tool.result)}
      {tool.error && renderer.renderError(tool, tool.error)}
    </div>
  );
}
```

### 🚀 阶段二：增强流式更新机制 (2-3周)

#### 2.1 字符级流式更新
```typescript
// WebSocket 字符流处理
class CharacterStreamProcessor {
  private buffer: string = '';
  private updateCallback: (text: string) => void;
  
  processChunk(chunk: string) {
    this.buffer += chunk;
    // 批量更新：每50ms或积累50字符触发更新
    this.scheduleUpdate();
  }
  
  private scheduleUpdate = debounce(() => {
    this.updateCallback(this.buffer);
  }, 50);
}
```

#### 2.2 智能渲染优化
```typescript
// 使用 Web API 优化渲染性能
function StreamingTextDisplay({ stream }: { stream: string }) {
  const elementRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    if (elementRef.current) {
      // 使用 DocumentFragment 减少重排
      const fragment = document.createDocumentFragment();
      const textNode = document.createTextNode(stream);
      fragment.appendChild(textNode);
      
      // 批量更新 DOM
      requestAnimationFrame(() => {
        elementRef.current!.appendChild(fragment);
      });
    }
  }, [stream]);
  
  return <div ref={elementRef} className="streaming-text" />;
}
```

#### 2.3 工具执行状态流
```typescript
// 工具执行状态的细粒度更新
interface ToolExecutionState {
  phase: 'preparing' | 'executing' | 'processing' | 'complete';
  progress?: number;
  currentAction?: string;
  intermediateResult?: any;
}

// 状态驱动的 UI 更新
function ToolProgressDisplay({ state }: { state: ToolExecutionState }) {
  return (
    <div className="tool-progress">
      <div className="phase-indicator">
        {state.phase === 'preparing' && <Spinner />}
        {state.phase === 'executing' && <ProgressBar value={state.progress} />}
        {state.currentAction && <span>{state.currentAction}</span>}
      </div>
    </div>
  );
}
```

### 🧠 阶段三：思考过程可视化 (1-2周)

#### 3.1 思考等级系统
```typescript
// 思考强度等级定义
enum ThinkingLevel {
  OFF = 'off',           // 无思考显示
  MINIMAL = 'minimal',   // 仅显示 "思考中..."
  LOW = 'low',          // 简化思考要点
  MEDIUM = 'medium',    // 部分思考过程
  HIGH = 'high',        // 详细思考过程  
  XHIGH = 'xhigh'       // 完整思考trace
}

// 等级对应的颜色方案
const thinkingColors = {
  off: 'text-gray-600',
  minimal: 'text-gray-500',
  low: 'text-blue-400', 
  medium: 'text-blue-300',
  high: 'text-purple-400',
  xhigh: 'text-pink-400'
};
```

#### 3.2 思考内容渐进式显示
```typescript
// 思考内容的智能分级显示
function ThinkingDisplay({ 
  content, 
  level, 
  isStreaming 
}: { 
  content: string;
  level: ThinkingLevel;
  isStreaming?: boolean;
}) {
  const processedContent = useMemo(() => {
    switch (level) {
      case ThinkingLevel.MINIMAL:
        return "🤔 思考中...";
      case ThinkingLevel.LOW:
        return extractKeyPoints(content);
      case ThinkingLevel.MEDIUM:
        return summarizeThinking(content);
      default:
        return content;
    }
  }, [content, level]);

  return (
    <div className={`thinking-block ${thinkingColors[level]}`}>
      <div className="thinking-header">
        <span className="thinking-icon">💭</span>
        <span className="thinking-level">思考 ({level})</span>
        {isStreaming && <StreamingIndicator />}
      </div>
      
      <div className="thinking-content">
        <MarkdownRenderer content={processedContent} />
        {isStreaming && <TypingCursor />}
      </div>
    </div>
  );
}
```

#### 3.3 用户控制的思考可见性
```typescript
// 用户可以动态调节思考显示级别
function ThinkingLevelControl() {
  const [level, setLevel] = useThinkingLevel();
  
  return (
    <div className="thinking-controls">
      <label>思考显示级别:</label>
      <select value={level} onChange={e => setLevel(e.target.value)}>
        <option value="off">隐藏</option>
        <option value="minimal">最小</option>
        <option value="low">简化</option>
        <option value="medium">中等</option>
        <option value="high">详细</option>
        <option value="xhigh">完整</option>
      </select>
    </div>
  );
}
```

### 🎨 阶段四：专业主题系统 (1-2周)

#### 4.1 语义化主题架构
```typescript
// 基于语义的主题配置
interface NimbusTheme {
  // 思考相关
  thinking: {
    off: string;
    minimal: string;
    low: string;
    medium: string;
    high: string;
    xhigh: string;
  };
  
  // 工具状态
  tools: {
    pending: { bg: string; border: string; text: string };
    success: { bg: string; border: string; text: string };
    error: { bg: string; border: string; text: string };
  };
  
  // 代码相关
  code: {
    background: string;
    syntax: {
      keyword: string;
      string: string;
      comment: string;
      function: string;
      variable: string;
    };
    diff: {
      added: string;
      removed: string;
      context: string;
    };
  };
}
```

#### 4.2 动态主题切换
```typescript
// 支持运行时主题切换
function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useTheme();
  
  useEffect(() => {
    // 应用 CSS 变量到根元素
    const root = document.documentElement;
    Object.entries(theme.tools.pending).forEach(([key, value]) => {
      root.style.setProperty(`--tool-pending-${key}`, value);
    });
    // ... 应用其他主题变量
  }, [theme]);
  
  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
```

### 📊 阶段五：AI 工作透明度增强 (2周)

#### 5.1 AI 决策过程可视化
```typescript
// AI 决策链条的可视化
interface DecisionChain {
  thought: string;          // 当前思考
  consideration: string[];  // 考虑的选项
  chosen: string;          // 选择的行动
  reasoning: string;       // 选择理由
}

function DecisionFlow({ chain }: { chain: DecisionChain }) {
  return (
    <div className="decision-flow">
      <div className="thought">💭 {chain.thought}</div>
      
      <div className="considerations">
        <span className="label">考虑选项:</span>
        {chain.consideration.map(opt => (
          <span key={opt} className="option">{opt}</span>
        ))}
      </div>
      
      <div className="decision">
        <span className="label">决定:</span>
        <span className="chosen">{chain.chosen}</span>
      </div>
      
      <div className="reasoning">
        <span className="label">理由:</span>
        {chain.reasoning}
      </div>
    </div>
  );
}
```

#### 5.2 工具调用意图显示
```typescript
// 在工具执行前显示 AI 的意图
function ToolIntentDisplay({ intent }: { intent: ToolIntent }) {
  return (
    <div className="tool-intent">
      <div className="intent-header">
        <span className="icon">🎯</span>
        <span>准备执行: {intent.toolName}</span>
      </div>
      
      <div className="intent-purpose">
        <span className="label">目的:</span>
        {intent.purpose}
      </div>
      
      <div className="intent-expected">
        <span className="label">期望结果:</span>
        {intent.expectedOutcome}
      </div>
    </div>
  );
}
```

### 🔧 阶段六：性能优化和用户体验 (1周)

#### 6.1 虚拟化长列表
```typescript
// 对于长对话历史，使用虚拟化减少 DOM 节点
import { FixedSizeList as List } from 'react-window';

function MessageList({ messages }: { messages: Message[] }) {
  const Row = ({ index, style }: { index: number; style: any }) => (
    <div style={style}>
      <ChatMessage message={messages[index]} />
    </div>
  );
  
  return (
    <List
      height={600}
      itemCount={messages.length}
      itemSize={150}
      overscanCount={5} // 预渲染5个项目
    >
      {Row}
    </List>
  );
}
```

#### 6.2 智能滚动优化
```typescript
// 更智能的滚动策略
function useIntelligentScroll() {
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const [lastUserInteraction, setLastUserInteraction] = useState(0);
  
  useEffect(() => {
    // 如果用户最近有交互，暂停自动滚动
    const timeSinceInteraction = Date.now() - lastUserInteraction;
    if (timeSinceInteraction < 3000) { // 3秒内
      setShouldAutoScroll(false);
    } else {
      setShouldAutoScroll(true);
    }
  }, [lastUserInteraction]);
  
  return { shouldAutoScroll, markUserInteraction: () => setLastUserInteraction(Date.now()) };
}
```

## 技术实现要点

### 关键技术选择

#### 1. 流式处理
- **WebSocket + EventSource**: 实时数据流
- **Web Streams API**: 字符级流处理  
- **RequestAnimationFrame**: 平滑的 UI 更新

#### 2. 性能优化
- **React.memo**: 减少不必要的重渲染
- **useMemo/useCallback**: 缓存计算结果
- **React-window**: 虚拟化长列表
- **DocumentFragment**: 批量 DOM 更新

#### 3. 状态管理
- **Zustand**: 轻量级状态管理
- **Context API**: 主题和配置共享
- **Local Storage**: 用户偏好持久化

#### 4. 样式系统
- **CSS Variables**: 动态主题切换
- **Tailwind CSS**: 基础样式框架
- **CSS-in-JS**: 动态样式计算

### 数据流设计

```
WebSocket Stream → Character Buffer → Batch Processor → React State → Renderer
     ↓                    ↓              ↓             ↓           ↓
  Raw chunks         Accumulation    Smart batching   UI State   Visual Output
  (ms级更新)         (减少抖动)       (50ms/50字符)    (优化)     (流畅显示)
```

### 用户体验考虑

#### 1. 渐进式信息展示
- 先显示工具意图，再显示执行结果
- 思考内容从简到详的分级显示
- 长输出的智能折叠和展开

#### 2. 视觉层次设计
- 不同类型内容的视觉权重区分
- 颜色编码的状态指示系统
- 统一的间距和对齐规范

#### 3. 交互反馈
- 实时的加载状态指示
- 明确的错误状态和恢复提示
- 快捷键支持的高效操作

## 验收标准

### 功能验收
- [ ] 每种核心工具都有专用渲染器
- [ ] 流式更新延迟 < 100ms
- [ ] 思考内容支持 6 级显示模式
- [ ] 主题切换无需刷新页面
- [ ] 长对话性能保持流畅 (>1000条消息)

### 性能验收  
- [ ] 首次渲染 < 200ms
- [ ] 流式更新 CPU 占用 < 10%
- [ ] 内存占用稳定 (无明显泄漏)
- [ ] 大文件工具输出渲染 < 500ms

### 用户体验验收
- [ ] AI 工作过程清晰可见
- [ ] 信息密度合理，不感到拥挤
- [ ] 交互反馈及时准确
- [ ] 支持键盘导航
- [ ] 移动端适配良好

## 风险控制

### 技术风险
- **性能风险**: 大量 DOM 更新可能影响性能
  - 缓解: 虚拟化、批量更新、memo优化
  
- **兼容性风险**: 新的 Web API 可能不被旧浏览器支持
  - 缓解: Progressive Enhancement，优雅降级

### 用户体验风险  
- **信息过载**: 过多的技术细节可能困扰普通用户
  - 缓解: 分级显示，默认简化模式
  
- **学习成本**: 新的交互模式需要用户适应
  - 缓解: 渐进式引导，保留熟悉的操作方式

## 后续迭代方向

### 长期愿景
1. **AI 协作透明化**: 用户能完全理解 AI 的工作流程
2. **个性化界面**: 基于用户习惯的智能界面适配
3. **多模态支持**: 语音、手势等交互方式
4. **协作增强**: 多用户实时协作功能

### 技术演进
1. **WebAssembly**: 性能关键部分的原生化
2. **PWA**: 离线能力和原生应用体验
3. **AI 增强**: 界面布局的智能优化
4. **无障碍**: 更好的可访问性支持

通过这个渐进式的实施计划，我们将把 Pi Coding Agent TUI 的优秀设计理念融入到 Nimbus Web-UI 中，创造出既保持 Web 平台优势又具备专业工具特性的用户界面。