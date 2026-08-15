# Nimbus Micro-Eval — 核心改造的 fitness function

> 2026-08-15 · 背景：dsh 取经结论"变异有了，选择还没有"——在动核心（事件日志
> Phase 2 反转权威）之前，先建选择函数。本文档记录设计、纪律与首批发现。

## 定位

**Harness eval，不是 model eval**：固定模型、固定任务，度量 harness 行为。
用途 = 核心手术前后的回归检测。与 Harbor/terminal-bench（外部大评测、容器）
互补：micro-eval 进程内、零容器、分钟级。

## 结构

```
evals/
  runner.py            # 进程内 AgentOS，per task：夹具→运行→verify→抽日志指标
  tasks/<name>/
    task.toml          # goal / mode / timeout / followups / agent 配置(小窗等)
    workspace/         # 夹具，拷到 tmp 运行（sandbox 可写根内）
    verify.py          # verify(workspace, output, log) -> (passed, detail)
  baselines/<model>.json         # --baseline 落盘
  baselines/<model>.latest.json  # 普通运行落盘
```

## 双 rail 纪律（防误读，重要）

| Rail | 模型 | 成本 | pass/fail | harness 指标 |
|---|---|---|---|---|
| **smoke** | ollama gemma4:12b-it-qat（已验证）/ qwen3.8 | 免费 | **仅参考**（弱模型天然抖） | **主信号** |
| **judgment** | claude-sonnet（OAuth 订阅） | 订阅内 | 主信号 | 辅助 |

smoke rail 每次核心改动后跑；judgment rail 只在大手术（Phase 2 级别）前后跑。
弱模型的价值恰恰是**压出 harness 的坏路径**（stall/nudge/压缩/恢复），
强模型一遍过反而测不到这些分支。

## 指标语义（全部来自 session_log，eval 是日志的第一个只读消费者）

- `turns/steps/tool_results`：效率与形状
- `turn_end_reasons`：completed/aborted/error/max-iterations 分布（+stalled 标记）
- `compactions`：压缩触发次数（long-context 任务应 >0）
- `invariant_violations`：**任何非空 = 核心 bug**，与 pass/fail 无关
- `usage`：token 消耗（回归阈值建议：steps/tokens 涨幅 >30% 视为回归）

## 任务集 v0（5 个，各守一个核心子系统）

| 任务 | 守什么 | 判据 |
|---|---|---|
| hello-tool | 工具调用基本回路 | 真值 token 进最终输出 + 有 tool/result |
| write-verify | Write+Bash 终结链 | add.py 可导入且正确 |
| long-context | **压缩（Phase 2 主守卫）**：4K 小窗强制 compaction | 压缩≥1 且 SECRET-CODE 存活 |
| followup | FollowUpQueue 跨 turn 记忆 | 2 turns + codename 召回 |
| crash-resume | **中断→graded recovery→续跑**（Phase 1 产物验证） | summary.txt 存在 + DONE |

扩容候选（v1）：steering / spawn-agent / stall-bait / sandbox-denial /
grep-navigate / multi-file（从 nimbus_harbor/tasks 搬）。

## 首跑发现（2026-08-15，gemma4 smoke rail，baseline 2/5）

**机械全绿**：所有任务（含中断路径）括号平衡、invariants 零违规；
interrupt→aborted→快照重载→resume 链路工作；followup 两轮召回通过；
write-verify 13 步真实写码+跑测通过。

**三个失败同属一族——"无证据终止"（termination-on-claim）**：

1. **hello-tool**：stall 终结的 `_final_summary` 兜底只在
   `len(summary) < 80` 时追加工具真值；gemma4 啰嗦总结超长 →
   `NIMBUS-EVAL-7431` 丢失。修复方向：兜底以"真值是否已包含"为门，
   不以长度为门。
2. **crash-resume**：resume 后模型纯文本宣称"我已创建 summary.txt"
   （从未调 Write），harness 接受了这句话并 completed 收尾 ——
   教科书级"过早宣告完成"。
3. **long-context**：Glob 同参数三连 → stall 终结，零文件读取，
   压缩路径未被行使；nudge 未能解锁"列表→读取"转换。

**系统性结论（带数据）**：nimbus 的终止语义信模型的嘴。dsh 的
`concludesTurn`（工具侧声明终止 + 真值随工具走）就是对症方案——
这应是 Phase 2 之前/之中的第一刀核心改动，且本 eval 即其回归守卫。

**附带**：VCPU doom loop 警告在命令连续**成功**时说
"Same command keeps failing"——heuristic 与文案不符，待修。

## 使用

```bash
python evals/runner.py                          # 全任务，默认 gemma4
python evals/runner.py --tasks hello-tool       # 单任务调试
python evals/runner.py --baseline               # 存为该模型 baseline
python evals/runner.py --model anthropic/claude-sonnet-4-5   # judgment rail
```

## 边界（刻意不做）

- 不做并行执行（本地模型串行即可；sonnet rail 需要时再加）
- 不做 LLM-judge 打分（verifier 全确定性）
- 不做与 baseline 的自动 diff 报警（先人读 JSON，形成直觉后再自动化）
- 不碰 Harbor（外部基准继续走 Harbor，职责分离）
