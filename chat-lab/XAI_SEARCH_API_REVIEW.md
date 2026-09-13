# xAI 社区搜索：公开接口与热度排序边界

2026-09-12 官方文档／OpenAPI／官方 Python SDK／官方 protobuf 源码交叉核对。此文最初仅做文档研究。

**后续用户授权的[真实接口探针](XAI_SEARCH_API_TEST.md)更新**：旧式 search_parameters 返回410退役；当前 Responses SSE 实际暴露了 `x_keyword_search` 的 `mode=Top/Latest` 和包含 `min_faves:100` 的 query。它们是内部调用参数，不是外层 x_search 的公开排序字段；没有证明纯浏览量排序或取得可校验的指标表。共4个HTTP请求，未改生产／发送Telegram。

## 结论

**公开接口中有互动量门槛，但没有确认可直接调用的“实时浏览量降序榜单”接口。** 不能混淆当前 agentic `tools: [{type: "x_search"}]`、另一套 `search_parameters.sources` 配置，以及 Grok 内部工具。

### 1. 当前使用的 agentic X Search

入口 `POST https://api.x.ai/v1/responses`，工具配置 `type: "x_search"`。

官方 SDK `tools.x_search()`、REST OpenAPI 的 `ModelTool` XSearch 分支及 protobuf `XSearch` 相互印证的字段为：
- `from_date` / `to_date`
- `allowed_x_handles` / `excluded_x_handles`
- `enable_image_understanding` / `enable_video_understanding`

未在此工具公开 schema 中找到 `sort_by`、`sort_order`、`Top` / `Latest` 枚举、`min_faves`、`post_view_count`、逐帖互动指标 field selection、热点游标或热度增长率。

文档说 Grok 能使用关键词／语义／用户搜索及线程读取，**不等于开发者可将每个内部工具及其参数作为独立公开 endpoint 调用**。自然语言要求“Top”“最热”不是已经设置了可验证的排序字段。后续虽已在内部 trace 观察到 Top/Latest，也需区别于稳定的外部 API 合同，更不等价于浏览量降序。

附带不一致：指南称账号名单最多20，当前 OpenAPI 写 maxItems=10；本轮没有实测10/20边界。不要用公开文档的一处描述替代实际兼容性验证。

### 2. 另一套 search_parameters 的 XSource

当前公开 OpenAPI `SearchSource`、官方 Python SDK `search.x_source()`、protobuf `XSource` 中确实存在：

| 字段 | 定义 |
|---|---|
| `post_favorite_count` | 最低点赞数，greater than or equal |
| `post_view_count` | 最低浏览量，greater than or equal |
| `included_x_handles` / `excluded_x_handles` | 包含／排除账号 |

配置形状例如：

```json
{
  "search_parameters": {
    "mode": "on",
    "sources": [
      {"type": "x", "post_favorite_count": 100, "post_view_count": 10000}
    ]
  }
}
```

这是旧式／另一套搜索配置的 **schema 示意，不是已验证可用的生产请求**。这些字段仍在当前发布的 schema 和 SDK 中；文档研究阶段尚未验证运行支持；后续在当前 Grok 4.6、Responses 路径、本账户 Pi OAuth 上的实测返回410，确认该 Live Search 路径退役，不能采用。不得把这些字段直接加进当前 `tools[].x_search` 对象。

`mode=on/auto/off` 控制是否搜索，不是 Top/Latest 排序。两个 count 字段是过滤门槛，不是要求返回对应指标，也不是按数值降序。浏览量门槛属于累计量，不是某分钟／小时的流量增速。

### 3. 搜索过程与原始输出：一个重要限制

官方 streaming 文档：
- `include=["verbose_streaming"]`，可观察工具调用；示例可输出函数名和 arguments。
- SDK `include=["x_search_call_output"]` 可请求 X 搜索工具输出。
- Responses API 的工具输出 include 对照表没有列出 X 对应项；不能直接推定 SDK 配置与 Responses 完全等价。

**进一步核对官方 protobuf，`INCLUDE_OPTION_X_SEARCH_CALL_OUTPUT` 的注释明确为 encrypted output。** Web search 输出亦标为加密，与 code execution/collections 等标明 plaintext 的输出不同。

因此，“工具输出可返回”不能直接推导为“可读的逐帖原始 JSON 或点赞／浏览量表”。研究中最初提出它或可提供原始指标；源码核对后必须收紧为：是否有可读指标尚未证实，不能作为已具备的方案。

当前生产 Nimbus 搜索桥接为同步请求并主要投影模型文字／引用／候选／usage；尚无服务端工具调用参数观测。后续隔离探针已验证 Responses SSE 的 `custom_tool_call.input` 可观察内部关键词查询和 Top/Latest，生产尚未接入。若要验证 Grok 是否真的采用了某种搜索模式，宜先做隔离、最小的过程观测，而不是相信最终回答自述。只保留白名单参数／可读来源字段，不保存认证内容或无界 reasoning/raw HTTP。

## 对日报的实际意义

1. 当前不能实现“给现有 x_search 加一个按浏览量排序参数”这样的小改动。
2. 后续已排除退役的旧式 count 路径，并取得实际关键词搜索参数。下一步应验证这种引导的稳定性、查询过滤的实际效果及指标可读性；HTTP 200 本身不证明过滤条件被执行。
3. 若只能得到 Grok 转述的互动数字，不能包装成数据库原始指标；若能取得真实指标，也只能对已取得候选作“样本内”排序。
4. 真正的热度增长率还需同一帖至少两个有时间标记的观测；单次累计 views 不能代表“正在爆发”。现有接口没有确认此类时序数据供应。
5. 高互动门槛会漏掉刚发布、较小作者及小众但重要事件。即使验证可用，也只能作为一条高热度发现支路，与开放发现互补，不能过滤全部新闻。
6. X 自身 REST API 属于另一套接口，不在这份 xAI 能力结论内；按用户要求不以它替换当前社区搜索。

## 官方依据

- [X Search 指南](https://docs.x.ai/developers/tools/x-search)
- [Responses 接口](https://docs.x.ai/developers/rest-api-reference/inference/responses)
- [OpenAPI schema](https://docs.x.ai/openapi.json)：`ModelTool`、`SearchParameters`、`SearchSource`。
- [官方 SDK tools.py](https://github.com/xai-org/xai-sdk-python/blob/main/src/xai_sdk/tools.py)：`x_search()`。
- [官方 SDK search.py](https://github.com/xai-org/xai-sdk-python/blob/main/src/xai_sdk/search.py)：`x_source()`。
- [Streaming & Sync](https://docs.x.ai/developers/tools/streaming-and-sync)：工具 trace、include 字段及 SDK/Responses 对照表。
- [官方协议 chat.proto](https://github.com/xai-org/xai-proto/blob/main/proto/xai/api/v1/chat.proto)：`IncludeOption`、`XSearch`、`XSource`。

公开抓取快照与 hash 留在忽略目录 `.artifacts/xai-ranking-study/`。文档和 main 分支会变化；以上是本轮读取结论，而非未经实测的账户兼容性或实时排序保证。
