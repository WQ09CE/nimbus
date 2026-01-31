# 用户打断功能实现

## 已完成的功能

### 1. Store层改动
- 在 `ChatState` 接口中添加了：
  - `isInterrupting: boolean` - 标识是否正在处理打断请求
  - `streamAbortController: AbortController | null` - 用于中止流式请求
- 添加了 `interruptMessage()` 方法，用于中断正在进行的流式处理

### 2. ChatInput 组件改动
- 添加了新的 props：
  - `onInterrupt?: () => void` - 打断回调
  - `isStreaming?: boolean` - 是否正在流式处理
  - `isInterrupting?: boolean` - 是否正在中断
- 在流式处理期间，Send按钮会变成红色的"停止"按钮
- 点击停止按钮会调用 `onInterrupt` 回调

### 3. 主页面改动
- 从store中获取 `isInterrupting` 状态和 `interruptMessage` 方法
- 将这些状态和方法传递给 ChatInput 组件
- 更新了 disabled 逻辑，只有在非中断状态的流式处理时才禁用输入

### 4. 错误处理改动
- 在catch块中检测 AbortError，显示"用户已取消对话"消息
- 在流式处理结束和错误处理时都重置 abort controller 和中断状态

## 使用流程

1. 用户发送消息，开始流式处理
2. 在流式处理期间，Send按钮变成红色的"停止"按钮
3. 用户点击"停止"按钮
4. 调用 `interruptMessage()` 方法，中止当前请求
5. 显示"用户已取消对话"错误消息
6. 重置所有流式处理状态

## 技术细节

- 使用 `AbortController` 来实现请求中断
- 通过 `signal` 参数传递给 `streamChat` 函数
- 在catch块中通过检查错误名称来区分用户中断和其他错误
- 状态管理确保UI正确反映当前的中断状态