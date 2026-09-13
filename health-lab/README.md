# Garmin Readiness Lab — 独立本地研究

适用于提供夜间 HRV / Training Readiness 等数据的 Garmin 设备。
已完成大陆接口读取验证；API 标识用于分段但不等于已核实物理型号，代码不凭型号字符串推定字段完整。

**当前实现：大陆只读采集、私有归档、数据质量检查、过去数据基线、长期趋势、主观标签，以及同设备个人模式/探索性测量预测。**
**真实验收：用户本机登录成功，90天历史回填完成，核心字段与跨接口数值自洽。**
**已上线：私有摘要gateway、Nimbus原生garmin工具、独立09:00身体快报及其有界增量采集；2026-09-13新增后台证据引用解读，当前为有限试用。**
**尚未完成：人工对照App/特殊睡眠时段核实、跨设备传感器可比性验证、自定义总分拟合、Bot体感写入、未来自动slot实际送达验收。**

采集器仍是独立uv项目；通过私有摘要connector接入chat-lab，保留数据scope与服务隔离。健康与公共研究任务独立；最近更新均未改变08:00/09:00或五分钟提前量。原始历史只在本地计算，主控获得最小模式/预测摘要；历史回放和新设备前瞻有效性不能混同。
完整前期研究：`~/Documents/research/garmin-readiness-2026-09-11/README.md`。

设计基准见 [NIMBUS_INTEGRATION.md](NIMBUS_INTEGRATION.md)；**实际实现、隐私边界、验收和限制以 [INTEGRATION_STATUS.md](INTEGRATION_STATUS.md) 为准**。

## 开始：只需在本机终端登录

```bash
cd ~/Projects/nimbus-telegram/health-lab
uv sync --frozen --python 3.12
uv run --frozen garmin-readiness onboard
```

`onboard` 在本机终端交互输入大陆账号邮箱、隐藏输入密码/MFA；不要发到聊天、不要放进 argv、环境变量或配置文件。拒绝无 TTY 的密码管道。成功后只读采集最近 **3 天**，另取首日前一天心率以覆盖跨午夜的第一晚，共 22 次业务读取（此外 SDK 会认证、读取 profile/settings）。不会创建任何定时任务。

固定 `garminconnect==0.3.13`，完整依赖版本和分发包哈希在 `uv.lock`。使用 `is_cn=True`；不依赖停维的 Garth。SDK 本身可能尝试多种正常认证路径，本项目不额外循环登录；业务请求关闭 SDK 重试，每次间隔至少 1.5 秒，遇认证/限流/网络异常停止，404 记录为不可用并继续。人工重新同步是只读的；原始内容按哈希去重，观测版本保留。

缓存失效时在本机重新认证：

```bash
uv run --frozen garmin-readiness login --reauth
```

重新认证先在临时私有目录验证账号，绑定不一致则拒绝替换原有 token。账号切换不允许混入已有历史。Token 由 SDK 原子保存和刷新，具有持久账户权限；只读是我们固定方法白名单提供的限制，**不是 Garmin 颁发了只读 token**。

## 本地路径与权限

默认：`~/.local/share/garmin-readiness-cn/`

- `tokens/garmin_tokens.json`：仅 token，不存密码。
- `raw/<sha256>.json`：原始健康响应，8 MiB/响应上限；临时写入、fsync、原子发布、再登记 SQLite。相同内容不重复存文件。
- `health.sqlite3`：账号哈希、带时间的请求状态与原始哈希、版本化分析结果、主观记录。
- `.lock`：同一数据目录一次只允许一个命令，避免并行刷新 token / 写 DB。

目录 0700、文件 0600；拒绝符号链接、不安全权限、文件硬链接和 Git 工作树内的数据目录。SQLite 默认 DELETE journal；不接触现有 Telegram PostgreSQL。

目录不是加密保险箱；本机同用户/root 及其备份仍可接触数据。没有自动清理/异机备份/磁盘告警。硬断电可能留下未引用的完整原始对象或 `.pending-*` 文件，但不会把部分写入对象登记为已完成观测；被引用对象读取时校验哈希。

不把完整健康数据、GPS、token 发给云端模型；当前也未采集GPS。已获授权的最小摘要会经主控模型和Nimbus相关存储发送到Telegram，不再是完全本机处理。没有HTTP/TCP服务或MCP；已部署私有Unix socket摘要服务和Bot原生工具，健康内容与公开搜索上下文隔离。这里也**不沿用 chat-lab 的明文认证错误采集**：认证异常可能带 SSO ticket，CLI 只报告安全错误类别，不输出原始认证错误、HTTP header 或 traceback locals。Garmin read endpoint 的原始健康响应则按用户目的私有保存。

## 核对与历史采集

```bash
# 离线覆盖清点，不显示健康数值或账号
uv run --frozen garmin-readiness status

# 本机显示健康摘要并保存分析版本；不调用 Garmin/模型
uv run --frozen garmin-readiness report --day 2026-09-11

# 确认前三天与 App 一致后，显式回填（每批最多180天）
uv run --frozen garmin-readiness sync --days 90

# 后续增量通常重看最近3天，以接收 Garmin 睡眠修订
uv run --frozen garmin-readiness sync --days 3

# 本地长期描述性分析，不调用 Garmin 或模型
uv run --frozen garmin-readiness trends --days 90
```

90 天约 631 次业务读取，仅请求间隔约 16 分钟，另有网络耗时；不建议一开始就拉全部账号历史。取消会保留已完成观测，下次可人工重跑同一范围。历史可取范围以 Garmin 实际结果为准。

白名单仅七项：HRV、睡眠、日内心率、日静息心率、Training Readiness、压力、Body Battery。没有活动上传、修改、删除、课程、训练安排、通用 connectapi 或 GPS 路线读取。未新增详细运动活动/GPS端点。长期分析可使用已返回的睡眠呼吸/血氧和醒后准备度中的 acuteLoad 作描述性参考，不能据此认定完整训练量或诊断疾病。

## 当前基线的精确定义

- 时区固定 `Asia/Shanghai`，睡眠按醒来的日期；仅用 `*GMT` 转时区，不直接将 Garmin `*Local` 当 Unix epoch。
- HRV 用 `hrvSummary.lastNightAvg`，不用 weeklyAvg 代替昨夜。
- 夜间 HR 从前一天和当天的 `heartRateValues` 按真实睡眠窗口截取。先对 10 分钟时间桶取中位数，再对桶中位数取中位数，降低突发高频采样的影响。
- 夜间 HR 需要至少 30 个有效时间点、覆盖至少 70% 的 10 分钟桶，最大间断不超过 1 小时；这是质量启发式，不是医学阈值或精确佩戴率。
- 睡眠需字段日期/时间窗一致、已经结束、明确 confirmed；手动非设备睡眠、明显 HRV/睡眠窗口冲突、关键字段缺失不会进入完整基线。
- 基线仅使用目标日前 **42 天中的至少 28 个有效夜晚**，不使用目标日或未来日期。已知 deviceId / sleepVersion 不同的历史分开；sleep DTO 没有 deviceId 时可使用同日 sleepNeed 的 preferred tracker 标识作为明确标注的代理，不声称这是已验证的物理睡眠传感器。
- 主睡窗口中点落在北京时间12:00–22:00时，标记为非典型主睡时段待核实，保留原始值但不直接计入常规夜间基线或长期夜间统计。可能是夜班、旅行或主睡分类问题，不预先认定为无效睡眠或当日睡眠不足。
- HRV 取 log；HR 和睡眠时长保留原尺度，用历史中位数和 MAD 作稳健标准化，噪声下限分别是 0.05 log、1 bpm、0.5 小时。
- 日汇总静息 HR 只作对照，不混同夜间 HR，也不假定它在早晨已是最终值。
- Garmin 准备度只接受日期、时间点和 `AFTER_WAKEUP_RESET` 明确、且未显式标记 validSleep=false 的醒后项。兼容 timestampGMT，以及实测大陆返回的 timestamp/timestampLocal 配对；无时区的 timestamp 必须通过本地对应时刻交叉校验，不无条件猜UTC。不会沿用上游 morning helper 的“找不到就取第一项”fallback。
- sleep score、stress、Body Battery、Garmin readiness 不作为独立加分项叠加；避免共享 HRV 信号重复计数。
- `report` 输出分项与质量、`score: null` 和 `model_status: not_fitted_no_validated_target_labels`。**未见真实数据前不硬编一个总分。**

### 数据修订与时间泄漏

每次读取追加 fetched_at/status/hash。后续失败不会静默套用之前成功的数据冒充最新状态；原来的成功版本仍可按时间查询。

```bash
uv run --frozen garmin-readiness report --day 2026-09-11 --as-of '2026-09-11T09:00:00+08:00'
```

`--as-of` 仅选择当时已经被本采集器拿到的版本。历史回填不能伪造之前的晨间可用性。默认报告明确标记回顾性分析，而非前瞻预测。这是数据版本追踪，不是严格 runtime checkpoint/recovery。

## 给后续拟合积累目标

尽量在看任何设备/算法分数前，记录晨起精力（1 很低，5 很好）和肌肉酸痛（1 无，5 很重）：

```bash
uv run --frozen garmin-readiness label --energy 3 --soreness 2 --before-viewing-score
```

可指定 `--day`，回忆填写会保留实际 recorded_at，不假装当天填写。缺少 `--before-viewing-score` 就记录为否，避免无根据地认定盲填。

下一阶段先核对真实字段、历史覆盖、设备变化，再确定要拟合的是运动恢复还是日常精力。有足够标签后做时间顺序留出验证，与单指标及 Garmin 分数比较。没有配对 WHOOP 标签就不宣称监督拟合了 WHOOP。HRV 异常升高、RHR 异常偏低只标记复核，不自动视为恢复更好；分数不用于疾病诊断。

## 测试

```bash
uv run --frozen pytest -q
uv run --frozen ruff check src tests
```

使用合成生理数据、临时 SQLite、假 provider/认证、实际已安装客户端的无网络构造检查。测试期间不访问任何 Garmin 账号。覆盖跨午夜/UTC+8、非法值、采样覆盖、无明确晨间项、过去基线、设备变更、as-of/失败修订、权限/符号链接/硬链接、单写者、哈希完整性、原子写失败、账号隔离、认证不泄漏和限流停止。

目前 **72 项合成/离线测试通过**；chat-lab另有116项、core有551项通过/3项跳过。已单独完成真实大陆账号采集与字段自洽检查，原始记录、具体健康统计和身体分析仅保存在本机私有数据目录；仓库不保存这些健康数值。

**真实采集、跨接口自洽、人工 App 对照、设备准确性以及模型有效性是不同验收项，不互相替代。**
