# Nimbus Agent Terminal-Bench 测试报告

**测试日期**: 2026-02-05  
**Agent 版本**: nimbus 0.2.0  
**底层模型**: anthropic/claude-sonnet-4-5  
**测试平台**: terminal-bench (Docker 容器内执行)

---

## 一、测试概况

共 11 次运行，排除 2 次基础设施故障（Docker 未启动/build 失败），有效任务 9 个。

| 任务 | 测试项 | 通过项 | 结果 |
|------|--------|--------|------|
| fibonacci-server (run1) | 6 | 0 | ❌ agent 启动失败 |
| fibonacci-server (run2) | 6 | 6 | ✅ |
| kv-store-grpc | 7 | 5 | ❌ |
| pypi-server | 1 | 1 | ✅ |
| polyglot-c-py | 1 | 0 | ❌ |
| pcap-to-netflow | 4 | 1 | ❌ |
| add-benchmark-lm-eval-harness | 3 | 2 | ❌ |
| swe-bench-langcodes | 1 | 0 | ❌ agent 未启动 |
| build-cython-ext | 11 | 10 | ❌ |

**通过率: 2/9 (22%)**

值得注意的是，多数失败任务的通过率都很高（5/7, 2/3, 10/11），说明 agent 的基本能力没有问题，失败集中在"最后一公里"。

---

## 二、逐任务失败分析

### 2.1 kv-store-grpc — proto 字段命名错误

**通过 5/7，失败 2 项：** `test_grpc_protocol_handshake`, `test_grpc_server_functionality`

任务要求：

> SetVal takes a message named SetValRequest that includes a **key (string)** and a **value (int)** as parameters and returns a SetValResponse with a **val (int)** field

Agent 实际生成的 proto：`SetValRequest` 的字段命名为 `val` 而非 `value`。测试用 `SetValRequest(key="handshake", value=999)` 调用时报错：

```
ValueError: Protocol message SetValRequest has no "value" field.
```

Agent 自己写了测试客户端验证，但客户端用的是自己生成的 proto（字段名为 `val`），所以自测通过，实际测试失败。

**本质问题：** 需求中同时出现了 `value`（请求参数名）和 `val`（响应字段名），agent 没有区分两者，统一用了 `val`。

---

### 2.2 polyglot-c-py — 编译产物未清理

**通过 0/1，失败项：** `test_fibonacci_polyglot`

Agent 正确创建了 polyglot 文件 `/app/polyglot/main.py.c`，并为验证功能执行了编译：

```bash
gcc /app/polyglot/main.py.c -o /app/polyglot/cmain
```

测试检查目录内容：

```python
polyglot_files = os.listdir("/app/polyglot")
assert polyglot_files == ["main.py.c"]
# AssertionError: Expected only main.py.c, found: ['cmain', 'main.py.c']
```

**本质问题：** Agent 做了正确的事（验证编译能否通过），但没有意识到验证行为本身对环境产生了副作用。

---

### 2.3 pcap-to-netflow — 时间戳语义理解错误

**通过 1/4，失败项：** `test_flow_0_data`, `test_flow_171_data`, `test_last_flow_data`

所有失败都是时间戳不匹配：

```
Expected timestamp=1295981576046  →  实际 1767881434251
Expected timestamp=1295981831981  →  实际 1767881497329
Expected timestamp=1295982726000  →  实际 1767882415103
```

期望值对应 2011 年 1 月的 Unix 时间戳（pcap 文件的实际捕获时间），实际值对应 2026 年 2 月（当前系统时间）。差值恒定约 15 年。

Agent 在生成 NetFlow v5 header 的 `SysUpTime` 和 `unix_secs` 字段时，使用了当前系统时间而非 pcap 数据包中记录的原始时间戳。流量计数（1,209 条流、41 个文件）完全正确，仅时间戳基准出错。

**本质问题：** 对 NetFlow v5 协议规范理解不充分。时间戳应从 pcap packet header 提取，而非取系统时钟。

---

### 2.4 add-benchmark-lm-eval-harness — metric 配置不匹配

**通过 2/3，失败项：** `test_metrics`

```python
return results["results"]["esci"]["exact_match,none"]
# KeyError: 'exact_match,none'
```

Agent 创建了 esci.jsonl（93,347 条数据，格式正确）和 task YAML，但 lm-eval-harness 的评估结果中不包含 `exact_match,none` 这个 metric key。说明 task YAML 中的 metric 定义方式与 lm-eval-harness 框架的期望不一致。

**本质问题：** 对 lm-eval-harness 框架的 task YAML metric 配置规范不熟悉，未能正确配置 `exact_match` metric type。

---

### 2.5 swe-bench-langcodes — Agent 安装失败

**通过 0/1，failure_mode: agent_timeout (20min)**

容器日志显示：

```
/installed-agent/nimbus-run: line 13: exec: nimbus: not found
```

Nimbus wheel 安装过程看起来完成了（pip install 输出正常），但 `nimbus` 命令不在 PATH 中。该容器使用 Python 3.9，可能是 pip 安装 console_scripts entry point 的路径（如 `/usr/local/bin` vs `~/.local/bin`）与 nimbus-run 脚本中的 PATH 不一致。

**本质问题：** Agent 的 Docker 安装脚本对 Python 3.9 环境的兼容性问题。这不是 agent 智能层面的问题，而是工程/部署问题。

---

### 2.6 build-cython-ext — numpy 兼容修复不完整

**通过 10/11，失败项：** `test_ccomplexity`

```
AttributeError: module 'numpy' has no attribute 'int'.
```

任务要求修复 pyknotid 的 Cython 扩展以兼容 NumPy 2.3.0。`np.int` 在 NumPy 2.0 中被移除。Agent 成功修复了 `chelpers.pyx` 和 `cinvariants.pyx` 中的兼容性问题，但遗漏了 `ccomplexity.pyx` 中的 `np.int` 用法。

三个 Cython 模块中有相同的兼容性问题，agent 修了两个漏了一个。

**本质问题：** 发现并修复了同类问题中的部分实例，但没有做全局扫描确认是否还有遗漏。

---

## 三、问题分类

从 7 个失败任务中（排除 2 个基础设施问题），可以归纳出以下几类问题：

### A. 自验证盲区（3/7 任务受影响）

| 任务 | 表现 |
|------|------|
| kv-store-grpc | 用自己的 proto 自测通过，但字段名与要求不符 |
| polyglot-c-py | 验证了功能正确，没检查环境副作用 |
| pcap-to-netflow | 验证了文件数量，没验证数据内容 |

Agent 最终输出都是"✅ 任务完成"，并给出了详细的成功总结。但验证维度不足——只验证了 happy path 的功能性，没有覆盖：
- 字段精确命名
- 文件系统清洁度
- 数据值的正确性

这不是一个 prompt 问题。Agent 有验证意识（它确实写了自测），但**验证策略过于乐观**——倾向于用自己理解的方式验证自己的实现，而不是站在评测者角度做对抗性验证。

### B. 同类问题全局扫描缺失（1/7）

| 任务 | 表现 |
|------|------|
| build-cython-ext | 修了 chelpers + cinvariants 的 np.int，漏了 ccomplexity |

修复发现的问题后，没有执行 `grep -rn "np\.int[^0-9]" *.pyx` 之类的全局扫描。这是一个工作流程问题——agent 的行为模式是"发现 → 修复 → 下一步"，缺少"发现 → 修复 → 全局确认"这个闭环。

### C. 需求精度降级（2/7）

| 任务 | 表现 |
|------|------|
| kv-store-grpc | `value` → `val` |
| lm-eval-harness | metric 配置与框架规范不符 |

Agent 对自然语言需求的理解存在"模糊化"倾向——把精确的 API 名称当作含义近似的描述来处理。

### D. 工具层限制（间接影响）

| 问题 | 影响 |
|------|------|
| Bash 工具 60s 超时 | kv-store-grpc 中启动后台服务超时，浪费了 agent 多个迭代 |
| Docker 容器中 ps/pgrep 缺失 | agent 无法确认进程状态，反复尝试诊断 |

kv-store-grpc 任务中，agent 花了 17 轮迭代（约 3 分钟）处理"后台服务是否在运行"的问题，真正的 bug（字段命名）反而没有被发现。工具层的摩擦消耗了 agent 本可用于更仔细检查代码的注意力预算。

---

## 四、待讨论的改进方向

以下列出可能的改进方向，不包含具体实施方案，供评审委员会讨论优先级。

### 方向 1：验证机制增强

当前 agent 的验证是"自由发挥"式的——由 LLM 自行决定验证什么、怎么验证。可以考虑在框架层面引入结构化的验证阶段，比如：

- 任务完成后的 checklist 回审阶段
- 面向"副作用"的环境状态检查
- 输出值抽样验证（而不仅仅验证结构）

### 方向 2：全局扫描能力

当 agent 发现并修复了一个代码问题时，缺少"在整个项目中搜索同类问题"的行为模式。可以考虑：

- 在工具链中提供更便捷的全局搜索工具
- 在修复类任务中引入自动化的同类问题扫描步骤

### 方向 3：Bash 工具对长时运行进程的支持

当前 Bash 工具的 60s 超时对"启动后台服务"场景不友好。`command &` 在 subprocess pipe 中不能真正后台化（因为 stdout pipe 仍被持有）。可以考虑：

- 专门的"启动后台进程"工具或参数
- 更智能的超时处理（检测到后台进程时自动 detach）

### 方向 4：Docker 安装兼容性

swe-bench-langcodes 的失败完全是安装问题（nimbus binary 不在 PATH 中）。需要：

- 测试覆盖 Python 3.9/3.10/3.11/3.12/3.13 各版本的安装路径
- nimbus-run 脚本中的 PATH 设置更健壮

### 方向 5：领域知识补充机制

pcap-to-netflow 的失败源于对 NetFlow v5 协议规范的理解错误。lm-eval-harness 的失败源于对该框架的配置规范不熟悉。当任务涉及特定协议/框架时，agent 需要：

- 在实现前先查阅相关文档/规范
- 或者具备在线搜索能力

---

## 五、原始数据

所有测试日志位于：

```
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-03-33/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-03-52/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-04-15/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-10-44/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-14-57/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-21-24/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-30-33/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-37-08/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-52-36/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-52-37/
/Users/wangqing/sourcecode/agent/terminal-bench/runs/2026-02-05__22-52-39/
```

每个 run 目录下：
- `results.json` — 结构化测试结果
- `sessions/agent.log` — agent 完整执行日志
- `sessions/tests.log` — 测试执行输出
- `panes/post-agent.txt` — agent 执行后的容器状态
