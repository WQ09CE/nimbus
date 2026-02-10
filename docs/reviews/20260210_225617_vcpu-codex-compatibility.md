# AI Review Committee: vcpu-codex-compatibility

- **Date**: 2026-02-10 22:56:17
- **Focus**: architecture
- **Reviewers**: 3
- **Total Time**: 120.0s

---

## Review by `anthropic/claude-opus-4-6`

# Architecture Review: vCPU Loop & Multi-Model Function Call Compatibility

**Reviewer:** anthropic/claude-opus-4-6
**Focus:** Architecture
**Date:** 2025-01-XX

---

## 1. Overall Assessment

**Score: 5/10** — The vCPU loop has a clean Think-Act-Observe structure, but it embeds a critical assumption: that all LLMs share identical function-calling semantics. This assumption breaks down with Codex-style models, revealing a missing abstraction layer between model output and action interpretation.

---

## 2. Strengths

- **Clean separation of concerns in the happy path**: The `step()` → `decode()` → `_execute_action()` pipeline is readable and well-structured for a single-model scenario.
- **`ActionIR` as intermediate representation**: Having a canonical action representation is architecturally sound — it's the right place to normalize model differences. The problem isn't the IR's existence but how narrowly it's populated.
- **`max_consecutive_thoughts` config**: The existence of this knob shows awareness that text-only responses need bounds. The issue is that `1` is hardcoded as the default with no model-aware override path.
- **Mixed response handling** (content + tool_calls split): The `split_response` logic in `step()` correctly handles the common case where models emit reasoning text alongside tool calls.

---

## 3. Issues Found

### 🔴 Critical: Binary THOUGHT/TOOL_CALL Dichotomy Is Architecturally Insufficient

**Location:** `decoder.py` — the `elif content and content.strip()` branch

**Description:**
The decoder has exactly two modes:
1. `tool_calls` present → map to `ActionIR` tool actions
2. No `tool_calls`, text present → `THOUGHT` → implicit `RETURN`

This binary classification collapses *all* text-only responses into a single semantic category. But model outputs carry vastly different **intent signals**:

| Text-Only Response Type | Correct Handling | Current Handling |
|---|---|---|
| Final answer to user | RETURN ✅ | RETURN ✅ |
| Confirmation request ("Shall I proceed?") | CONTINUE / PROMPT_USER | RETURN ❌ |
| Reasoning/chain-of-thought | CONTINUE | RETURN ❌ |
| Error explanation ("I can't do X because...") | RETURN ✅ | RETURN ✅ |
| Partial plan ("I'll do A then B then C") | CONTINUE | RETURN ❌ |

With `max_consecutive_thoughts = 1`, the framework terminates the agent loop on the very first text response, which is correct for decisive models (Claude) but catastrophically wrong for cautious models (Codex).

**Suggestion:** The `ActionIR.kind` enum needs at minimum a third value: `CLARIFICATION` or `INTERMEDIATE_THOUGHT`, which does not trigger return logic. The decoder — or a post-decode classifier — must distinguish terminal text from non-terminal text.

---

### 🔴 Critical: No Model Behavior Profile / Strategy Abstraction

**Location:** `vcpu.py` (entire step loop), `VCPUConfig`

**Description:**
The vCPU loop hardcodes a single behavioral contract: "the model will call tools when it should act." There is no abstraction for **model behavioral profiles**. This is the root architectural gap.

Different models exhibit fundamentally different function-calling personalities:

| Behavioral Axis | Claude/Sonnet | Codex | GPT-4o |
|---|---|---|---|
| Confirmation tendency | Low — acts directly | High — asks first | Medium |
| Tool call granularity | Batched calls | Single sequential | Varies |
| Text-alongside-tools | Frequent | Rare | Frequent |
| Implicit intent in text | Action-oriented | Dialogue-oriented | Mixed |

The framework treats all of these identically.

**Suggestion:** Introduce a `ModelBehaviorProfile` (or `ModelDialect`) that sits between the ALU (LLM interface) and the Decoder:

```python
@dataclass
class ModelBehaviorProfile:
    """Describes how a specific model family behaves with tool calling."""
    
    # How aggressively the model uses tools vs. text
    tool_call_tendency: Literal["eager", "moderate", "cautious"] = "eager"
    
    # Whether text-only responses should auto-return or continue
    text_response_strategy: Literal["auto_return", "classify", "always_continue"] = "auto_return"
    
    # Max consecutive text responses before forced return
    max_consecutive_thoughts: int = 1
    
    # System prompt amendments for this model type
    behavioral_prompt_suffix: Optional[str] = None
    
    # Whether to inject "execute immediately" instructions
    suppress_confirmation: bool = False
```

---

### 🟡 Major: `_handle_return` Conflates "I have something to say" with "I'm done"

**Location:** `vcpu.py` — implicit return logic triggered by THOUGHT actions

**Description:**
The current logic chain is:
```
text-only response → THOUGHT ActionIR → is_final=True → agent stops
```

This means the agent **cannot think out loud without terminating**. For agentic use cases, this is a significant limitation even beyond the Codex issue. An agent that wants to announce "Starting deployment to staging..." before making tool calls cannot do so across two `step()` iterations.

**Suggestion:** Decouple "has text output" from "is finished." The finality decision should be based on:
1. Explicit `RETURN` actions (model calls a designated "finish" tool)
2. Consecutive thought count exceeding threshold
3. Semantic classification of the text (is this an answer or a process update?)

---

### 🟡 Major: System Prompt Is Not a Reliable Fix for Model Behavioral Differences

**Location:** Architectural decision space (Scheme A in the problem statement)

**Description:**
While prompt engineering (Scheme A: "Do NOT ask for confirmation") will partially mitigate the symptom, it is **architecturally fragile** because:
1. Codex's confirmation behavior may be deeply embedded in its RLHF training; prompts cannot fully override it
2. It couples business logic (execution policy) with prompt text
3. Prompt effectiveness varies across model versions — a prompt that works on Codex v1 may not work on v2
4. It provides no fallback mechanism when the prompt fails

This should be a **complementary** measure, not the primary solution.

---

### 🟡 Major: No Feedback Loop from Execution Context to Thought Classification

**Location:** `decoder.py` — `decode()` has no access to conversation history or agent state

**Description:**
The decoder makes classification decisions (`THOUGHT` vs tool action) in isolation, without context. It cannot distinguish:
- "First response in a fresh task" (more likely to be a clarification → should continue)
- "Response after tool results returned" (more likely to be a conclusion → could be final)
- "Response after user said 'go ahead'" (should definitely execute, not ask again)

The decoder is stateless, but the classification problem is inherently stateful.

**Suggestion:** Pass minimal context signals to the decoder:

```python
@dataclass
class DecodeContext:
    turn_number: int
    last_action_kind: Optional[str]  # Was previous action a tool call? A thought?
    pending_tool_results: bool  # Are there unprocessed tool results?
    user_intent_signal: Optional[str]  # "confirmed", "exploring", etc.
```

---

### 🔵 Minor: `max_consecutive_thoughts = 1` Default Is Too Aggressive

**Location:** `VCPUConfig`

**Description:**
Even for well-behaved models like Claude, `max_consecutive_thoughts = 1` means *zero tolerance* for intermediate thinking. This works today because Claude rarely emits text-only responses when tools are available, but it's a brittle default that will break with any model that occasionally "thinks" before acting.

**Suggestion:** Default to `3`, and let eager-model profiles override to `1` if desired.

---

## 4. Architecture/Design Observations

### The Real Problem: A Missing "Intent Classification" Layer

The core architectural gap is not about any single configuration value — it's that the framework lacks an **intent classification layer** between raw LLM output and action dispatch.

Current pipeline:
```
LLM Output → Decoder (structural parsing) → ActionIR → Executor
```

Required pipeline:
```
LLM Output → Decoder (structural parsing) → Intent Classifier → ActionIR → Executor
                                                  ↑
                                          ModelBehaviorProfile
                                          ConversationContext
```

The Intent Classifier's job is narrow but crucial: given a text-only response, determine whether it is:
- **TERMINAL**: A final answer (→ RETURN)
- **INTERMEDIATE**: Reasoning, planning, status update (→ CONTINUE, don't show to user)
- **CLARIFICATION**: Asking the user something (→ YIELD to user, but don't end agent loop)
- **CONFIRMATION_REQUEST**: Asking permission to act (→ based on policy, either auto-confirm or yield)

This classifier can be implemented in tiers of sophistication:
1. **Rule-based** (regex/heuristic): Check for question marks, "shall I", "would you like", "confirm" patterns
2. **Config-driven**: Model profile says "this model's text is usually X"
3. **LLM-assisted**: Use a fast, cheap model to classify intent (likely overkill initially)

### The Adapter Pattern Is the Right Long-Term Direction

The problem statement mentions "Adapter 层" as an option. I strongly agree this is the right long-term architecture. Each model family should have an adapter that:
1. Adjusts system prompts for optimal tool-calling behavior
2. Post-processes responses to normalize behavioral quirks
3. Provides a `ModelBehaviorProfile` to the vCPU

This is analogous to how database ORMs have dialect-specific adapters (PostgreSQL vs MySQL) despite a common query interface.

### Tension: Agent Autonomy vs. User Safety

There's a legitimate design tension here. Codex's confirmation behavior, while annoying for autonomous agents, is arguably *safer* for high-stakes operations. The framework should support **execution policies**:
- `AUTONOMOUS`: Never confirm, always execute (current Claude behavior)
- `CAUTIOUS`: Confirm destructive/irreversible actions
- `SUPERVISED`: Always confirm before tool execution

This is orthogonal to the model compatibility issue but should be considered in the solution design to avoid accidentally hardcoding an autonomy level.

---

## 5. Actionable Recommendations (Prioritized)

### Priority 1: Introduce `ModelBehaviorProfile` + Prompt Augmentation (Short-term fix, 1-2 days)

This addresses the immediate Codex problem with minimal architectural change:

```python
# In config or model registry
CODEX_PROFILE = ModelBehaviorProfile(
    tool_call_tendency="cautious",
    max_consecutive_thoughts=5,
    suppress_confirmation=True,
    behavioral_prompt_suffix=(
        "IMPORTANT: When the user confirms or agrees, execute the required tool calls immediately. "
        "Do NOT ask for additional confirmation. Treat 'ok', 'yes', 'go ahead', '好', '1', 'a' "
        "as explicit confirmation to proceed with the previously discussed action."
    )
)

# In vcpu.py step(), apply profile
config = self._resolve_config(model_name)  # merges VCPUConfig with ModelBehaviorProfile
```

**Why first:** This is the 80/20 solution. Prompt augmentation + higher `max_consecutive_thoughts` will fix most Codex interactions without deep refactoring.

### Priority 2: Add Intent Classification to Decoder (Medium-term, 3-5 days)

Extend the decoder with a lightweight text classifier:

```python
class ResponseIntentClassifier:
    """Classifies text-only LLM responses into intent categories."""
    
    CONFIRMATION_PATTERNS = [
        r"shall I\b", r"would you like", r"do you want me to",
        r"please confirm", r"proceed\?", r"是否", r"确认",
    ]
    
    QUESTION_PATTERNS = [
        r"\?\s*$", r"which (one|option)", r"clarify",
    ]
    
    def classify(self, text: str, context: DecodeContext) -> ResponseIntent:
        if self._matches_confirmation_patterns(text):
            return ResponseIntent.CONFIRMATION_REQUEST
        if self._is_question(text) and context.turn_number < 3:
            return ResponseIntent.CLARIFICATION
        if context.pending_tool_results:
            return ResponseIntent.TERMINAL  # Summarizing tool results = likely final
        return ResponseIntent.TERMINAL  # Default: treat as answer
```

Then in the vCPU loop:
```python
if intent == ResponseIntent.CONFIRMATION_REQUEST and profile.suppress_confirmation:
    # Auto-inject "yes, proceed" and re-run step
    self.mmu.add_user_message("Yes, proceed with the action.")
    return await self.step()  # Re-enter loop
```

**Why second:** This makes the framework robust to model behavioral variation without per-model prompt hacking. The rule-based approach is maintainable and testable.

### Priority 3: Refactor Toward Model Adapter Architecture (Long-term, 1-2 weeks)

Introduce a formal `ModelAdapter` interface:

```python
class ModelAdapter(Protocol):
    """Adapts framework behavior to model-specific characteristics."""
    
    @property
    def behavior_profile(self) -> ModelBehaviorProfile: ...
    
    def augment_system_prompt(self, base_prompt: str) -> str: ...
    
    def post_process_response(self, response: LLMResponse, context: DecodeContext) -> LLMResponse:
        """Normalize model-specific quirks before decoding."""
        ...
    
    def should_auto_confirm(self, text: str) -> bool:
        """Whether a confirmation-request text should be auto-approved."""
        ...
```

```python
# Registry
MODEL_ADAPTERS = {
    "claude-*": ClaudeAdapter(),
    "gpt-5.3-codex*": CodexAdapter(),
    "gpt-4o*": GPT4oAdapter(),
}
```

**Why third:** This is the proper long-term solution but requires significant refactoring. Do it after validating the approach with Priorities 1-2.

---

## Summary Table

| Issue | Severity | Recommended Fix | Priority |
|---|---|---|---|
| Binary THOUGHT/TOOL_CALL classification | 🔴 Critical | Intent classifier | P2 |
| No model behavior profiles | 🔴 Critical | `ModelBehaviorProfile` | P1 |
| THOUGHT = implicit RETURN | 🟡 Major | Decouple text from finality | P2 |
| Prompt-only fix is fragile | 🟡 Major | Use as complement, not primary | P1 |
| Stateless decoder | 🟡 Major | Pass `DecodeContext` | P2 |
| `max_consecutive_thoughts` default | 🔵 Minor | Raise default to 3 | P1 |

---

**Bottom line:** The framework has a clean core design but was implicitly built around Claude's tool-calling behavior. The fix is not to patch Codex compatibility into the existing structure, but to introduce a thin adaptation layer (ModelBehaviorProfile → Intent Classifier → Model Adapter) that makes the framework model-agnostic by design. Start with the profile + prompt approach for immediate relief, then build the classifier, then formalize the adapter pattern.

---

## Review by `openai-codex/gpt-5.3-codex`

## 1) Overall Assessment

**Score: 7/10** — 问题定位准确，但当前边界（THOUGHT=final）把“对话行为”与“任务完成”耦合过紧，导致对 Codex 这类保守模型适配性不足。

---

## 2) Strengths

1. **问题复现路径清晰**  
   你给出了具体对话序列（“好”→确认循环→“同意”才调用工具），这比抽象描述更有助于定位状态机缺陷。

2. **关键执行路径暴露完整**  
   `step()` 的 THINK/DECODE/STORE/EXECUTE 顺序和 `decoder.decode()` 的分支非常关键，能看出是“文本回复被当作 implicit RETURN”触发提前终止。

3. **已有保护参数意识**  
   `max_consecutive_thoughts` 已体现你们知道“连续思考”是风险点，这给后续演进为更通用策略提供了钩子。

---

## 3) Issues Found

### Issue 1
- **Severity:** 🔴 Critical  
- **Location:** `decoder.py::decode()` + `vcpu.py` `_handle_return` / `result.is_final` 语义链路  
- **Description:**  
  目前“无 tool_calls 的文本”直接映射为 `THOUGHT`，随后在执行层被视作 implicit final return。  
  这将**LLM输出形态**（text vs tool_call）错误等价为**任务状态**（done vs not done），是架构层面的语义泄漏。
- **Suggestion:**  
  引入**显式终止动作**（如 `FINAL_ANSWER` / `RETURN`）与 `THOUGHT` 解耦：  
  - `THOUGHT` 默认非终止  
  - 仅当满足终止判据（显式标签、协议字段、或终止策略判定）才 `is_final=True`  
  - Decoder 只做语法映射，终止判定由 Policy/Controller 层处理

---

### Issue 2
- **Severity:** 🟡 Major  
- **Location:** `VCPUConfig.max_consecutive_thoughts = 1`  
- **Description:**  
  全局固定阈值对跨模型行为不鲁棒。对于 Codex 这类确认型模型，阈值 1 等同于“禁止澄清轮次”，会系统性截断任务。
- **Suggestion:**  
  将该配置下沉为**模型能力配置**（model profile）或**任务策略配置**：  
  - 例如 `thought_budget`, `clarification_budget`, `confirmation_policy`  
  - 默认值可保守，但允许 per-model / per-agent override  
  - 记录 telemetry 后再自动调参

---

### Issue 3
- **Severity:** 🟡 Major  
- **Location:** vCPU `step()` 中 mixed response / store / execute 顺序  
- **Description:**  
  当前“content + tool_calls”做 split 是对的，但“纯 content”直接进入 action 执行，缺少一个**Intent/Policy Gate**判断该文本是：  
  1) 最终回答、2) 请求用户澄清、3) 自我确认/犹豫、4) 工具调用前置说明。  
- **Suggestion:**  
  在 decode 后、execute 前增加 `DecisionPolicy.evaluate(actions, context)`：  
  - 输出 `CONTINUE_THINK | ASK_USER | FORCE_TOOL_ATTEMPT | FINALIZE`  
  - 将“是否结束循环”从 Action 执行器里抽离

---

### Issue 4
- **Severity:** 🔵 Minor  
- **Location:** 方案讨论 A（仅靠 prompt）  
- **Description:**  
  仅靠 “Do NOT ask for confirmation” 的 system prompt 对高安全/高不确定任务不稳定，且易被上层用户语义覆盖。  
- **Suggestion:**  
  Prompt 作为“软约束”，必须配合运行时 policy（硬约束）与配置层（可调），形成三层防线。

---

### Issue 5
- **Severity:** 🔵 Minor  
- **Location:** 状态可观测性（未展示但从问题可推断）  
- **Description:**  
  缺少“为何结束”的结构化 reason code，会让调参和跨模型比较困难。
- **Suggestion:**  
  增加 step-level metrics：  
  `termination_reason`, `thought_count`, `clarification_count`, `tool_attempt_count`, `model_profile_id`。

---

## 4) Architecture/Design Observations

1. **核心架构问题是“语义分层不清”**  
   Decoder 本应做协议解析，不应承担“这是不是 final answer”的业务判定。  
   终止应由独立策略层依据上下文和配置决定。

2. **建议采用“策略驱动循环”，而非“输出形态驱动循环”**  
   当前是 LLM 给文本就可能结束；更稳健的是：  
   - LLM 提供候选意图  
   - Policy 决定是否继续、是否要求工具、是否向用户澄清

3. **多模型支持需要 Profile + Capability Matrix**  
   不同模型在 function calling 上行为差异是常态，不是异常。  
   应显式建模：`supports_reliable_tool_call`, `clarification_tendency`, `instruction_adherence` 等。

4. **A/B/C 方案评估**  
   - **A (Prompt-only):** 快但脆弱，适合作为补丁，不是根治。  
   - **B (文本询问检测):** 可用但有语言依赖和误判风险，需作为启发式而非主逻辑。  
   - **C (model-specific config):** 必要，但若没有 policy 抽象会演变为散乱 if-else。  
   - **D (推荐): Policy Layer + Model Profile + Prompt协同**（长期最稳）。

---

## 5) Actionable Recommendations (Top 3)

### 1) 最高优先：解耦 `THOUGHT` 与 `FINAL`
- 新增 `FINAL_ANSWER`（或 `RETURN`）动作类型  
- `THOUGHT` 默认 `is_final=False`  
- 终止逻辑集中到 `TerminationPolicy`

### 2) 引入 `DecisionPolicy` + `ModelProfile`
- `DecisionPolicy` 负责：是否继续循环、是否要求工具优先、是否允许澄清  
- `ModelProfile` 提供模型差异参数：`max_consecutive_thoughts`, `clarification_budget`, `tool_call_bias`

### 3) Prompt 作为辅助手段并配 telemetry 闭环
- 针对 Codex 增强 system prompt（“有足够参数时直接调用工具”）  
- 同时埋点 termination/clarification/tool-call 指标，按模型评估效果  
- 用数据决定阈值，而不是硬编码全局默认

---

如果只做短期修复：先上 **C + 轻量A**。  
如果做中期正确演进：必须落 **D（Policy分层）**，这是你们 vCPU 作为通用执行引擎的关键。

---

## Review by `google-antigravity/gemini-3-pro-high`

(Review failed: Process proc-b7079d1d timed out after 120.0s)

---
