# 🔭 Observability Demo

## 目的
演示 Nimbus 框架中 sub-tool call 的可观测性功能。

## 文件说明
| 文件 | 说明 |
|------|------|
| `observability_demo.py` | 核心演示代码，模拟 agent 工作流的 trace |
| `observability_config.yaml` | 可观测性配置 |

## 运行
```bash
python observability_demo.py
```

## 架构
```
Core Agent (Dispatch)
  └── Executor Agent
       ├── Tool Call 1 (Read)
       ├── Tool Call 2 (Write)
       ├── Tool Call 3 (Bash)
       └── Tool Call 4 (Edit)
```

每一个 sub-tool call 都会在 web-ui 中被追踪和展示。
