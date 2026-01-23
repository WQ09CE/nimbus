# Skill 开发指南

## Skill 概念

**Skill** 是 OpenNotebook 中的基本能力单元。每个 Skill 是一个异步函数,接受特定参数并返回结果。

```python
async def my_skill(**kwargs) -> Any:
    # 实现 Skill 逻辑
    return result
```

## 内置 Skills

### 1. chat

基本对话 Skill,使用 LLM 生成响应。

**实现**: `skills/chat.py`

```python
async def chat(message: str, context: str = "") -> str:
    """基本对话 Skill"""
    prompt = f"Context: {context}\nUser: {message}\nAssistant:"
    return await llm_client.complete(prompt)
```

### 2. search

Web 搜索 Skill (需实现具体搜索逻辑)。

**实现**: `skills/search.py`

```python
async def web_search(query: str) -> str:
    """Web 搜索 Skill"""
    # 实现搜索逻辑 (如调用 Google API, Bing API 等)
    return search_results
```

### 3. summarize

文本摘要 Skill。

**实现**: `skills/summarize.py`

```python
async def summarize_text(text: str, max_length: int = 200) -> str:
    """文本摘要 Skill"""
    # 实现摘要逻辑
    return summary
```

### 4. keywords

关键词提取 Skill。

**实现**: `skills/summarize.py`

```python
async def extract_keywords(text: str, count: int = 5) -> List[str]:
    """关键词提取 Skill"""
    # 实现关键词提取逻辑
    return keywords
```

---

## 创建自定义 Skill

### 基本步骤

1. **定义 Skill 函数**
2. **注册到 Agent**
3. **Agent 自动路由**

### 示例 1: 简单计算 Skill

```python
async def calculator(expression: str) -> float:
    """简单计算器 Skill"""
    try:
        # 注意: 生产环境请使用安全的表达式求值
        result = eval(expression)
        return float(result)
    except Exception as e:
        raise ValueError(f"计算错误: {e}")

# 注册
agent.register_skill("calculator", calculator)

# 使用
response = await agent.run("计算 123 + 456")
# Agent 会自动调用 calculator Skill
```

### 示例 2: 数据库查询 Skill

```python
import sqlite3

async def query_db(sql: str, params: tuple = ()) -> List[dict]:
    """数据库查询 Skill"""
    conn = sqlite3.connect("data.db")
    cursor = conn.cursor()
    cursor.execute(sql, params)
    columns = [col[0] for col in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return results

# 注册
agent.register_skill("query_db", query_db)

# 使用
response = await agent.run("查询销售额最高的 10 个产品")
# Agent 会生成 SQL 并调用 query_db
```

### 示例 3: 文件处理 Skill

```python
import pandas as pd

async def analyze_csv(file_path: str, operation: str) -> dict:
    """CSV 分析 Skill"""
    df = pd.read_csv(file_path)

    if operation == "summary":
        return {
            "rows": len(df),
            "columns": list(df.columns),
            "stats": df.describe().to_dict()
        }
    elif operation == "head":
        return df.head().to_dict()
    else:
        raise ValueError(f"不支持的操作: {operation}")

# 注册
agent.register_skill("analyze_csv", analyze_csv)
```

### 示例 4: API 调用 Skill

```python
import httpx

async def weather(city: str) -> str:
    """天气查询 Skill"""
    async with httpx.AsyncClient() as client:
        url = f"https://api.weather.com/v1/current?city={city}"
        response = await client.get(url)
        data = response.json()
        return f"{city} 当前天气: {data['temp']}°C, {data['condition']}"

# 注册
agent.register_skill("weather", weather)
```

---

## Skill 最佳实践

### 1. 参数验证

```python
async def my_skill(param1: str, param2: int = 10) -> str:
    # 验证参数
    if not param1:
        raise ValueError("param1 不能为空")
    if param2 <= 0:
        raise ValueError("param2 必须大于 0")

    # 执行逻辑
    return result
```

### 2. 错误处理

```python
async def robust_skill(url: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10)
            return response.json()
    except httpx.TimeoutException:
        return {"error": "请求超时"}
    except httpx.HTTPError as e:
        return {"error": f"HTTP 错误: {e}"}
    except Exception as e:
        return {"error": f"未知错误: {e}"}
```

### 3. 返回结构化数据

```python
async def structured_skill(query: str) -> dict:
    """返回结构化数据,便于 Agent 处理"""
    return {
        "status": "success",
        "data": {
            "query": query,
            "result": "...",
            "metadata": {"timestamp": "..."}
        }
    }
```

### 4. 支持流式输出 (可选)

```python
async def streaming_skill(prompt: str) -> AsyncIterator[str]:
    """流式输出 Skill"""
    async for chunk in llm_client.stream(prompt):
        yield chunk

# 注意: 当前 Runtime 不直接支持流式 Skill,
# 但可以在 Skill 内部累积后返回
```

### 5. 使用依赖注入

```python
class SkillFactory:
    def __init__(self, db_conn, api_key):
        self.db_conn = db_conn
        self.api_key = api_key

    def create_query_skill(self):
        async def query_skill(sql: str) -> List[dict]:
            # 使用 self.db_conn
            return results
        return query_skill

# 使用
factory = SkillFactory(db_conn, api_key)
agent.register_skill("query", factory.create_query_skill())
```

---

## Skill 注册方式

### 方式 1: 代码注册

```python
agent.register_skill("skill_name", skill_func)
```

### 方式 2: 配置文件注册 (YAML)

```yaml
skills:
  - name: "my_skill"
    type: "builtin"  # 内置 Skill

  - name: "custom_skill"
    type: "markdown"  # Markdown 定义的 Skill
    path: "~/.skills/custom.md"

  - name: "wukong_skill"
    type: "wukong"    # Wukong 框架 Skill
    path: "~/.wukong/skills/skill.py"
```

### 方式 3: 自定义 Skill Loader

```python
def load_my_skill_type(config: SkillConfig) -> SkillFunc:
    # 从配置加载 Skill
    path = Path(config.path)
    # ... 加载逻辑
    return skill_func

# 注册 Loader
AgentFactory.register_skill_loader("my_type", load_my_skill_type)

# 在配置中使用
# skills:
#   - name: "my_skill"
#     type: "my_type"
#     path: "/path/to/skill"
```

---

## Markdown Skill

Markdown Skill 允许通过 Markdown 文件定义 Skill 的提示词模板。

### 创建 Markdown Skill

**文件**: `~/.skills/summarizer.md`

```markdown
# 文本摘要 Skill

你是一个专业的文本摘要工具。请将以下文本压缩为 {{max_length}} 字以内的摘要。

## 文本
{{text}}

## 摘要
```

### 在配置中使用

```yaml
skills:
  - name: "summarizer"
    type: "markdown"
    path: "~/.skills/summarizer.md"
```

### 调用

```python
# Agent 会自动替换 {{text}} 和 {{max_length}}
response = await agent.run("总结这篇文章: ...")
```

---

## Skill 产物 (Artifacts)

Skill 可以返回结构化产物,供前端展示或后续任务使用。

### 返回产物

```python
async def chart_skill(data: List[dict]) -> dict:
    """生成图表配置 Skill"""
    return {
        "artifact_type": "chart",  # 标记为产物
        "id": "chart_1",
        "title": "销售趋势图",
        "data": {
            "type": "line",
            "options": {...},
            "data": data
        },
        "metadata": {"source": "sales_db"}
    }
```

### 产物类型

- `file`: 文件 (PPT, Word, PDF 等)
- `chart`: 图表配置 (ECharts, Plotly)
- `code`: 代码块
- `table`: 表格数据
- `image`: 图像
- `markdown`: Markdown 文档

### Agent 自动收集

```python
response = await agent.run("生成销售趋势图")

for artifact in response.artifacts:
    print(f"产物类型: {artifact.type}")
    print(f"标题: {artifact.title}")
    print(f"数据: {artifact.data}")
```

---

## Skill 测试

### 单元测试

```python
import pytest

@pytest.mark.asyncio
async def test_calculator_skill():
    result = await calculator("2 + 2")
    assert result == 4.0

@pytest.mark.asyncio
async def test_calculator_error():
    with pytest.raises(ValueError):
        await calculator("invalid")
```

### 集成测试

```python
@pytest.mark.asyncio
async def test_skill_in_agent():
    agent = NotebookAgent(llm_client=MockLLM())
    agent.register_skill("calculator", calculator)

    response = await agent.run("计算 10 * 5")
    assert "50" in response.text
```

---

## Skill 性能优化

### 1. 缓存

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def expensive_computation(param: str) -> str:
    # 昂贵计算
    return result

async def cached_skill(param: str) -> str:
    return expensive_computation(param)
```

### 2. 并发控制

```python
import asyncio

semaphore = asyncio.Semaphore(5)  # 最多 5 个并发

async def rate_limited_skill(url: str) -> str:
    async with semaphore:
        # 限流访问外部 API
        return await fetch(url)
```

### 3. 超时控制

```python
async def timeout_skill(query: str) -> str:
    try:
        return await asyncio.wait_for(
            long_running_task(query),
            timeout=10.0  # 10 秒超时
        )
    except asyncio.TimeoutError:
        return "任务超时"
```

---

## Skill 生命周期

```
1. 定义 Skill 函数
       ↓
2. 注册到 Agent (register_skill)
       ↓
3. Planner 识别需要的 Skill
       ↓
4. Runtime 调用 Skill (传入参数)
       ↓
5. Skill 执行并返回结果
       ↓
6. Agent 处理结果并返回给用户
```

---

## 常见问题

### Q: Skill 如何访问上下文?

通过参数传递:

```python
async def context_aware_skill(message: str, context: str = "") -> str:
    # 使用 context
    return response
```

### Q: Skill 如何调用其他 Skill?

不建议 Skill 之间直接调用。应该让 Planner 规划任务依赖:

```yaml
# DAG 规划
tasks:
  - id: "t1"
    skill: "search"
    params: {query: "..."}
  - id: "t2"
    skill: "summarize"
    params: {source: "t1"}  # 依赖 t1 的结果
    depends_on: ["t1"]
```

### Q: 如何处理大文件?

使用流式处理或分块:

```python
async def process_large_file(file_path: str) -> dict:
    total_lines = 0
    async with aiofiles.open(file_path) as f:
        async for line in f:
            total_lines += 1
            # 处理每一行
    return {"lines": total_lines}
```

### Q: Skill 如何返回多个结果?

返回列表或字典:

```python
async def multi_result_skill(query: str) -> List[dict]:
    return [
        {"type": "result", "data": "..."},
        {"type": "metadata", "data": "..."}
    ]
```

---

## 完整示例: 数据分析 Skill

```python
import pandas as pd
import asyncio

class DataAnalysisSkill:
    """数据分析 Skill 集合"""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir

    async def load_data(self, filename: str) -> pd.DataFrame:
        """加载数据"""
        path = f"{self.data_dir}/{filename}"
        return pd.read_csv(path)

    async def analyze(self, filename: str, operation: str) -> dict:
        """分析数据"""
        df = await self.load_data(filename)

        if operation == "summary":
            return {
                "artifact_type": "table",
                "title": f"{filename} 统计摘要",
                "data": df.describe().to_dict()
            }

        elif operation == "plot":
            return {
                "artifact_type": "chart",
                "title": f"{filename} 趋势图",
                "data": {
                    "type": "line",
                    "x": df.index.tolist(),
                    "y": df["value"].tolist()
                }
            }

        else:
            raise ValueError(f"不支持的操作: {operation}")

# 使用
skill = DataAnalysisSkill("/data")
agent.register_skill("analyze_data", skill.analyze)

response = await agent.run("分析 sales.csv 的趋势")
```

---

## 相关文档

- [快速入门](./getting-started.md)
- [架构说明](./architecture.md)
- [API 参考](./api-reference.md)
- [高级用法](./advanced-usage.md)
