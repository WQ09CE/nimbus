# xAI 搜索热度控制：真实接口探针

2026-09-12。用户授权的隔离探针，**4个 HTTP 请求，其中3次真实 X 搜索**。现有 Pi xAI OAuth、Grok 4.6，未读写生产 PG、修改生产源码／配置／日程、调用 Telegram。

## 1. 旧式浏览／点赞门槛：当前路线已退役

`POST /v1/responses`，使用 `search_parameters.mode=on`、X source、两个 count 门槛均为0。

实际返回 **HTTP 410**。本机安全分类确认错误正文同时包含 Live Search 和退役说明，并指向 tools 路径。原始错误仅私有有界保留，未投影认证／HTTP headers。

因此，不继续尝试高门槛：当前模型／认证／endpoint 上，这条配置路径已不可用。schema 和 SDK 中残留字段不能当可用能力。没有验证过滤效果，更没有接入生产。

## 2. 当前内置搜索：观察到了真正的 Top / Latest 调用参数

当前 `tools: [{type: "x_search", ...}]` + Responses SSE。只允许 X 工具，max_turns=1，max_output_tokens=1600，HTTP≤175s、bridge≤190s、store=false；无自动重试。

### 请求 Top + 最低点赞查询条件

服务端 `response.output_item.done` 返回：

```json
{
  "type": "custom_tool_call",
  "name": "x_keyword_search",
  "status": "completed",
  "input": "{\"query\":\"\\\"AI agents\\\" min_faves:100 since:2026-09-10 until:2026-09-13 -filter:replies\",\"limit\":\"5\",\"mode\":\"Top\"}"
}
```

该次 HTTP 200、completed、真实内部 x_search_calls=1，约44.6秒。

### 不要求 Top / 点赞门槛的对照

服务端同类事件返回 `x_keyword_search`，input 解码后：

```json
{
  "query": "(\"AI agent\" OR \"AI agents\") since:2026-09-10 until:2026-09-13 -filter:replies",
  "limit": "5",
  "mode": "Latest"
}
```

该次 HTTP 200、completed、真实内部 x_search_calls=1，约40.1秒。

**这是服务端工具调用回执，不是 Grok 最终回答声称“我按热度搜了”。** 同时，query 由模型生成且两次不逐字相同，Top 请求还加入了 min_faves，因此不是只改变一个变量的排序效果 A/B。

## 3. 能证明／不能证明

已证明本轮：
- 当前 Grok 4.6 内置关键词搜索实际调用过 `mode=Top` 与 `mode=Latest`。
- `min_faves:100` 被放进实际发送给关键词工具的 query；不是只留在外层请求说明。
- Responses SSE 可暴露 `custom_tool_call.input`，不必接入另一个搜索 provider 或 X REST API 才能观察这些参数。

尚未证明：
- `min_faves` 是否被后台作为过滤运算符严格执行。没有取得可校验的逐帖点赞表，不能用 HTTP 200 或 query 回显替代过滤验收。
- Top 的排序公式、是否纯按浏览／点赞数、实时刷新频率或全站覆盖。Top 不等价于已知的 views-desc，更不是热度增速。
- 是否能稳定指令控制、所有请求都会服从、内部工具 schema 永久兼容。外层 `x_search` 公开配置仍没有 mode/sort 字段；目前是自然语言引导 + 服务端观测。
- 逐帖指标的明文接口。本轮选定的工具事件未返回数字型点赞／浏览指标表；模型文本和引用也不等价于这种表。
- 全部返回帖子的存在、作者、内容和新闻价值。本轮是接口测试，不是新闻核验；两个帖子摘要也不是全 X 排名。

## 4. 观测器修正及请求记账

最初现代路径请求约26.3秒成功，已观察到 `custom_tool_call` / `x_keyword_search`，但探针白名单只保留 `arguments/action`，漏掉实际承载参数的 **`input`**。没有将此误判为上游不提供参数。

修正仅在忽略目录的探针副本中完成，再使用原预算剩余两次请求取得上述 Top 和对照记录。旧请求未伪造补回参数；本轮总计仍只有4个 HTTP 请求、3次实际搜索，没有不断重试到通过。

保存的是有界、白名单的工具调用输入、状态、usage、最终公共来源文本／引用。不保存 streaming reasoning 内容、认证信息或完整成功 HTTP body；encrypted content 只记录存在／长度，不解密。

## 5. 建议

先把原生搜索观测补到能够记录 `requested intent` 与 `observed mode/query`，再单独测试“Top 发现高互动候选 + Latest 发现新帖”的互补策略。高点赞可能偏向推广、大账号和较旧帖子，不把它作为全部新闻的资格门槛。

这提供了比继续叠长 prompt 更可测的控制点，但不是已经解决重大事件召回，也不是可以按实时浏览量自己排序的 API。生产变更和质量对照另行处理，本探针不自动部署。

证据：忽略目录 `.artifacts/xai-ranking-study/live-probe/` 的 intent、各请求 summary、legacy-error-classification、trace-continuation-*。公共搜索调用细节和原始错误仅保存在私有 investigation 的 `ranking-api-*` 下。探针源码位于 `.artifacts/xai-ranking-study/probe/`，使用现有 PiBridge 的隔离副本。
