# 多 Agent 架构能力测试实验报告

> 日期: 2025-01-25
> 作者: Nimbus Team
> 版本: v1.1

## 摘要

本实验设计并实现了一个高区分度的代码重构能力测试（Cross-File Refactoring Benchmark），用于评估不同 LLM 模型在复杂代码分析任务中的表现。实验发现：

1. **区分度达标**: 云端顶级模型 (96%) vs 本地小模型 (68%) 差距达 28%
2. **多 Agent 架构突破 90%**: qwen3-coder:30b 多 Agent 达到 91%，与云端仅差 5%
3. **同模型多 Agent 效果最佳**: 同模型 Brain + Coder 优于混合模型配置
4. **模型大小 ≠ 能力**: 指令遵循能力比参数量更重要

---

## 1. 实验背景

### 1.1 问题提出

原有 Nimbus 能力测试存在区分度不足的问题：

| 模型 | 原测试得分 |
|------|-----------|
| Gemini 2.5 Flash | 91.4% |
| Ollama qwen3:8b | 91.4% |

两个能力差距明显的模型得分相同，说明测试任务过于简单。

### 1.2 实验目标

1. 设计高区分度的能力测试，能区分强弱模型
2. 验证多 Agent 架构是否能提升本地模型表现
3. 探索最优的模型配置策略

---

## 2. 测试设计

### 2.1 Cross-File Refactoring 任务

**任务描述**: 将 `APIClient.old_api()` 方法重命名为 `new_api()`

**测试项目结构**:
```
sample_project/
├── core/
│   ├── client.py      # APIClient 类定义 (需修改)
│   └── utils.py       # 4 处调用 (需修改)
├── services/
│   ├── auth.py        # 4 处调用 (需修改)
│   └── data.py        # 独立 old_api() 函数 (陷阱，不应修改!)
├── tests/
│   └── test_client.py # 10+ 处调用 (需修改)
└── README.md          # 文档引用 (需修改)
```

**陷阱设计**: `services/data.py` 包含一个独立的 `old_api()` 函数，与 `APIClient.old_api()` 无关。弱模型容易误改此文件。

### 2.2 评分标准

| 指标 | 权重 | 说明 |
|------|------|------|
| Method Definition | 15% | 是否识别到方法定义需要修改 |
| Utils Calls | 15% | 识别 utils.py 中的调用点 (4处) |
| Auth Calls | 15% | 识别 auth.py 中的调用点 (4处) |
| Test Calls | 15% | 识别测试文件中的调用点 (10+处) |
| README Refs | 10% | 识别文档中的引用 (3+处) |
| No False Positive | 30% | 未误改 data.py 中的独立函数 |

**总分计算**: 加权平均

### 2.3 测试架构

#### 单 Agent 架构
```
用户任务 → LLM → 分析结果
```

#### 多 Agent 架构
```
用户任务 → Brain Agent (架构师) → 策略指导
                ↓
         Coder Agent (开发者) → 分析结果
```

**Brain Agent 职责**:
- 分析任务需求
- 识别关键约束（如陷阱文件）
- 制定步骤化指导

**Coder Agent 职责**:
- 按照 Brain 的指导执行
- 输出结构化的修改列表

---

## 3. 实验结果

### 3.1 单 Agent 模型对比

| 模型 | 参数量 | 总分 | 响应时间 | 识别数 |
|------|--------|------|----------|--------|
| Claude Opus 4.5 | - | **96.2%** | 36.6s | 44 |
| Gemini 2.5 Flash | - | **96.2%** | 25.3s | 45 |
| qwen3-coder:30b | 30B | 74.5-82.0% | 53-57s | 30-31 |
| qwen3:8b | 8B | 68.2-71.2% | 48.7s | 16-18 |
| qwen2.5-coder:32b | 32B | 66.0-73.5% | 141.8s | 16-19 |

*注：本地模型存在一定波动性*

#### 详细指标对比

| 指标 | Claude Opus 4.5 | Gemini 2.5 Flash | qwen3:8b | qwen2.5-coder:32b |
|------|-----------------|------------------|----------|-------------------|
| Method Definition | 100% | 100% | **0%** | **0%** |
| Utils Calls | 100% | 100% | 100% | 100% |
| Auth Calls | 75% | 75% | 75% | 50% |
| Test Calls | 100% | 100% | 80% | 90% |
| README Refs | 100% | 100% | **0%** | **0%** |
| No False Positive | 100% | 100% | 100% | 100% |

**关键发现**:
1. 本地模型普遍漏掉方法定义和文档更新
2. 所有模型都正确避开了陷阱
3. 32B 代码模型反而比 8B 通用模型略差

### 3.2 多 Agent 架构效果

#### 同模型 Brain + Coder（推荐）

| 模型配置 | 单 Agent | 多 Agent | 提升 |
|----------|----------|----------|------|
| **qwen3-coder:30b** | 74.5% | **91.0%** | **+16.5%** |
| qwen2.5-coder:32b | 73.5% | **87.2%** | **+13.7%** |
| qwen3:8b | 71.2% | **82.8%** | **+11.6%** |

#### 混合模型 Brain + Coder（效果不稳定）

| Brain | Coder | 得分 | 说明 |
|-------|-------|------|------|
| qwen3:14b | qwen3:8b | 77.5% | 有提升 |
| qwen3:14b | qwen3-coder:30b | 57.3% | **反而更差** |

**重要发现**: 混合模型配置效果不稳定，同模型多 Agent 更可靠。

### 3.3 多 Agent 改进分析

以 qwen3-coder:30b 为例：

| 指标 | 单 Agent | 多 Agent | 变化 |
|------|----------|----------|------|
| Method Definition | 100% | 100% | = |
| Utils Calls | 50% | 50% | = |
| Auth Calls | **0%** | **100%** | **+100%** |
| Test Calls | 80% | 90% | +10% |
| README Refs | 100% | 100% | = |

**多 Agent 解决了关键遗漏**:
- Brain Agent 明确指出需要修改的所有文件
- Brain Agent 强调 data.py 的陷阱
- Coder Agent 严格按照指导执行

---

## 4. 架构对比总览

### 4.1 最终排名

| 排名 | 架构 | 模型配置 | 得分 | 与云端差距 |
|------|------|----------|------|-----------|
| 1 | 云端单 Agent | Claude Opus 4.5 | **96.2%** | - |
| 1 | 云端单 Agent | Gemini 2.5 Flash | **96.2%** | - |
| **3** | **本地多 Agent** | **qwen3-coder:30b** | **91.0%** | **-5.2%** |
| 4 | 本地多 Agent | qwen2.5-coder:32b | 87.2% | -9.0% |
| 5 | 本地多 Agent | qwen3:8b | 82.8% | -13.4% |
| 6 | 本地混合 Agent | qwen3:14b + qwen3:8b | 77.5% | -18.7% |
| 7 | 本地单 Agent | qwen3-coder:30b | 74.5% | -21.7% |
| 8 | 本地单 Agent | qwen3:8b | 68-71% | -25~28% |
| 9 | 本地单 Agent | qwen2.5-coder:32b | 66-73% | -23~30% |

### 4.2 多 Agent 提升幅度

| 模型 | 单 Agent | 多 Agent | 提升 |
|------|----------|----------|------|
| qwen3-coder:30b | 74.5% | 91.0% | **+16.5%** |
| qwen2.5-coder:32b | 73.5% | 87.2% | +13.7% |
| qwen3:8b | 71.2% | 82.8% | +11.6% |

---

## 5. 结论与建议

### 5.1 主要结论

1. **高区分度测试设计成功**
   - 云端 vs 本地单 Agent 差距 25-30%
   - 能有效区分模型能力

2. **多 Agent 架构突破性有效**
   - qwen3-coder:30b 多 Agent 达到 91%，与云端仅差 5%
   - 平均提升 10-16%
   - 解决了本地模型的关键遗漏问题

3. **同模型多 Agent 效果最佳**
   - 同模型 Brain + Coder 稳定有效
   - 混合模型配置效果不稳定，可能反而更差

4. **模型选择建议**

   | 场景 | 推荐方案 | 预期得分 |
   |------|----------|----------|
   | 追求最高质量 | Claude Opus 4.5 / Gemini | 96% |
   | 本地部署首选 | qwen3-coder:30b 多 Agent | 91% |
   | 资源受限 | qwen3:8b 多 Agent | 83% |
   | 快速原型 | qwen3:8b 单 Agent | 68-71% |

5. **意外发现**
   - 代码专用模型不一定比通用模型好
   - 大模型不一定比小模型好（指令遵循能力更重要）
   - 混合模型配置可能相互干扰

### 5.2 多 Agent 架构最佳实践

```
推荐架构:

用户任务 → qwen3-coder:30b (Brain) → 策略指导
                    ↓
            qwen3-coder:30b (Coder) → 分析结果

关键点:
1. Brain 和 Coder 使用同一模型
2. Brain 负责理解任务、识别约束、制定策略
3. Coder 严格按照 Brain 的指导执行
4. 输出格式要明确（JSON）
```

### 5.3 后续工作

1. **扩展测试场景**
   - Stateful Bug Diagnosis（有状态 Bug 诊断）
   - Long-Horizon Planning（长周期任务规划）
   - Context-Aware Code Review（代码审查）

2. **优化多 Agent 架构**
   - 研究为何混合模型配置效果不稳定
   - 添加验证 Agent（三 Agent 架构）
   - 自动重试机制

3. **集成到 Nimbus**
   - 自动检测任务复杂度
   - 根据复杂度选择单/多 Agent
   - 支持自定义 Agent 角色

---

## 附录

### A. 测试文件位置

```
tests/capabilities/
├── benchmark_llm_refactoring.py      # 单 Agent 测试
├── benchmark_multi_agent.py          # 多 Agent 测试
└── test_cross_file_refactoring.py    # 评估框架

tests/data/refactoring/
├── sample_project/                   # 测试项目
└── golden/                           # 标准答案

tests/evaluation/
└── refactoring_metrics.py            # 评估指标
```

### B. 运行测试

```bash
# 单 Agent 测试
python tests/capabilities/benchmark_llm_refactoring.py \
  --provider ollama \
  --model qwen3-coder:30b

# 多 Agent 测试（推荐）
python tests/capabilities/benchmark_multi_agent.py \
  --provider ollama \
  --model qwen3-coder:30b \
  --compare

# 混合模型多 Agent（不推荐）
python tests/capabilities/benchmark_multi_agent.py \
  --provider ollama \
  --model qwen3:8b \
  --brain-model qwen3:14b
```

### C. 完整测试数据

#### 单 Agent 结果

| 模型 | Definition | Utils | Auth | Tests | README | No Trap | Total |
|------|------------|-------|------|-------|--------|---------|-------|
| Claude Opus 4.5 | 100% | 100% | 75% | 100% | 100% | 100% | 96.2% |
| Gemini 2.5 Flash | 100% | 100% | 75% | 100% | 100% | 100% | 96.2% |
| qwen3-coder:30b | 100% | 50% | 0% | 80% | 100% | 100% | 74.5% |
| qwen3:8b | 0% | 100% | 75% | 80% | 0% | 100% | 68.2% |
| qwen2.5-coder:32b | 0% | 100% | 50% | 90% | 0% | 100% | 66.0% |

#### 多 Agent 结果（同模型 Brain + Coder）

| 模型 | Definition | Utils | Auth | Tests | README | No Trap | Total |
|------|------------|-------|------|-------|--------|---------|-------|
| qwen3-coder:30b | 100% | 50% | 100% | 90% | 100% | 100% | **91.0%** |
| qwen2.5-coder:32b | 100% | 50% | 75% | 90% | 100% | 100% | 87.2% |
| qwen3:8b | 100% | 50% | 75% | 60% | 100% | 100% | 82.8% |

#### 混合模型结果

| Brain | Coder | Definition | Utils | Auth | Tests | README | No Trap | Total |
|-------|-------|------------|-------|------|-------|--------|---------|-------|
| qwen3:14b | qwen3:8b | 100% | 50% | 50% | 50% | 100% | 100% | 77.5% |
| qwen3:14b | qwen3-coder:30b | 100% | 25% | 25% | 10% | 33% | 100% | 57.3% |

### D. 关键洞察

1. **为什么多 Agent 有效？**
   - Brain 将复杂任务分解为明确步骤
   - Brain 显式指出约束条件（如陷阱文件）
   - Coder 只需执行，降低认知负担

2. **为什么混合模型效果差？**
   - 不同模型的"思维方式"不兼容
   - Brain 的表达方式可能不适合 Coder 理解
   - 同模型之间隐含知识共享更好

3. **为什么大模型不一定更好？**
   - 代码专用模型可能过度优化代码生成，忽略指令遵循
   - 通用模型的指令遵循能力可能更强
   - 输出格式的稳定性比"智能"更重要

---

## 版本历史

| 版本 | 日期 | 更新内容 |
|------|------|----------|
| v1.0 | 2025-01-25 | 初版，包含基本测试结果 |
| v1.1 | 2025-01-25 | 添加 qwen3-coder:30b 多 Agent 突破性结果，更新结论 |

---

*本报告由 Nimbus Agent Framework 生成*
