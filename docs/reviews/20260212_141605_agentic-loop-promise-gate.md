# AI Review Committee: agentic-loop-promise-gate

- **Date**: 2026-02-12 14:16:05
- **Focus**: architecture
- **Reviewers**: 3
- **Total Time**: 89.2s

---

## Review by `anthropic/claude-opus-4-6`

# Architecture Review: Nimbus Agentic Loop — Promise Gate + Hallucination Recovery

**Reviewer:** anthropic/claude-opus-4-6
**Focus:** Architecture & Design Soundness

---

## 1. Overall Assessment

**Score: 6/10** — The problem diagnosis is sharp and the layered defense metaphor is appealing, but the architecture conflates two fundamentally different failure modes into one design, and the core mechanism (Layer 2) relies on a pattern-matching approach that the design's own author correctly flags as fragile. The real architectural issue — `max_consecutive_thoughts=1` being a blunt instrument — is identified but not resolved.

---

## 2. Strengths

- **Excellent problem decomposition.** Issue 1 (promise-without-action) and Issue 2 (Gemini text-hallucinated tool calls) are correctly identified as related-but-distinct. This is the right framing.

- **Core Agent's self-critique is better than the proposal.** The inline analysis (§"My Initial Analysis") catches the most serious problems — false positives in L2, the value of L3b context purge, prematurity of L3d. This is a sign of a healthy review culture. I'll build on this rather than repeat it.

- **L1 (Prompt Policy) as zero-risk first move** is exactly right. The "text-only = final answer" framing is a clever contract that puts the obligation on the LLM in a way it can understand.

- **L3b (Context Purge)** is the single most architecturally sound idea in the proposal. Gemini's hallucination pattern is self-reinforcing through context — removing the bad examples breaks the feedback loop. This is a genuine insight.

---

## 3. Issues Found

### 🔴 Critical: `max_consecutive_thoughts=1` Is the Root Cause, Not a Constraint to Work Around

**Location:** Problem Summary, "Key constraint" section

**Description:** The entire design dances around `max_consecutive_thoughts=1` as if it were a law of physics. It's not — it's a previous design decision that traded one failure mode (chattering) for another (premature termination). The Promise Gate is essentially a hack to undo the effects of this setting on a case-by-case basis.

The real question is: **why is the loop termination condition a simple counter rather than a semantic classifier?** The counter can't distinguish:

| Response | Should terminate? | Counter says |
|---|---|---|
| "Let me search for that" | No | Yes (thought #1) |
| "Here's your answer: ..." | Yes | Yes (thought #1) |
| "Hmm, let me think about this differently..." | No | Yes (thought #1) |
| "I'll explain: [3 paragraphs]" | Yes | Yes (thought #1) |

**Suggestion:** Replace the counter with a **semantic finality detector** as the primary mechanism, and keep the counter as a safety ceiling (e.g., `max_consecutive_thoughts=5`). The finality detector doesn't need to be an LLM call — it can be a lightweight heuristic:

```python
def is_semantically_final(thought: str, has_tool_calls: bool) -> bool:
    """Determine if a thought represents a final answer vs. planning/promising."""
    if has_tool_calls:
        return False  # Tool calls = not done yet
    
    # Short responses with promise verbs → not final
    if len(thought.split()) < 30 and contains_promise_pattern(thought):
        return False
    
    # Contains structured answer markers → final
    if contains_answer_markers(thought):
        return True
    
    # Long substantive responses → likely final
    if len(thought.split()) > 50:
        return True
    
    # Default: final (preserves current behavior as fallback)
    return True
```

This inverts the architecture: instead of "everything is final, unless we catch a promise pattern," it's "classify the response, with a bias toward finality." The Promise Gate patterns become one signal among several rather than the sole mechanism.

---

### 🔴 Critical: Layer 2 and Layer 3 Have No Clear Architectural Boundary

**Location:** Layer 2 (Promise Gate) and Layer 3 (Hallucination Recovery)

**Description:** Both layers perform the same architectural operation: **inspect LLM output → detect anomaly → modify context → retry.** But they live in different places (L2 in `_handle_thought` or Pipeline, L3 in Decoder) with different retry semantics, different context modification strategies, and different escalation paths.

This creates a combinatorial problem: What happens when a Gemini response contains *both* a promise phrase *and* a hallucinated tool call? Which layer fires first? Do they compose? Can L2's retry trigger L3's detection?

**Suggestion:** Unify L2 and L3 into a single **Response Validation** stage in the pipeline:

```
LLM Response → Pipeline → Decoder → ResponseValidator → ActionIR
                                          ↓
                                    [anomaly detected]
                                          ↓
                                    RetryStrategy(type, attempt_num)
                                          ↓
                                    context_mod + retry OR escalate
```

The `ResponseValidator` applies all checks (promise detection, hallucination detection, format validation) and produces a single `RetryStrategy` that encodes what to do. This eliminates the ordering/composition problem and makes the system testable as a unit.

---

### 🟡 Major: Pattern Matching is the Wrong Abstraction for Promise Detection

**Location:** Layer 2, `PROMISE_PATTERNS_ZH` / `PROMISE_PATTERNS_EN`

**Description:** The Core Agent's analysis is correct: "让我总结一下: 1. xxx" is a false positive. But the problem runs deeper than specific patterns.

The fundamental issue is that promise detection requires **pragmatic intent classification**, not **lexical pattern matching**. The same phrase "Let me explain" can be:
- A promise (= I will do something next) → should not be final
- A discourse marker (= here comes my explanation) → IS the final answer

This distinction is contextual and grammatical, not lexical. No finite set of string patterns will capture it reliably across languages.

**Suggestion:** If L1 (prompt engineering) doesn't reduce this to acceptable levels, the next step should be a **cheap structural heuristic**, not more patterns:

```python
def is_promise_without_substance(thought: str) -> bool:
    """
    A 'promise' is short text that indicates future action.
    A 'final answer starting with a discourse marker' is long text
    that contains the actual answer after the marker.
    """
    lines = thought.strip().split('\n')
    first_line = lines[0] if lines else ""
    rest = '\n'.join(lines[1:]).strip()
    
    # If the first line matches a promise pattern BUT
    # there's substantial content following it → it's a final answer
    if contains_promise_pattern(first_line):
        if len(rest) > 100 or len(lines) > 3:
            return False  # Has substance → final answer
        return True  # Short promise with no follow-through
    return False
```

This is still heuristic, but it's **structurally grounded**: a promise without substance is distinguishable from a discourse marker followed by content.

---

### 🟡 Major: "Ephemeral" Message Concept Needs Formal Definition

**Location:** Layer 2, "mark assistant message as ephemeral"

**Description:** The design introduces an "ephemeral" message concept without defining its semantics in the MMU data model. Questions:

- Is `ephemeral` a flag on the `Message` object?
- Does MMU's `assemble_context()` exclude ephemeral messages?
- What happens to ephemeral messages during conversation export/save?
- Can ephemeral messages be un-ephemeral'd?
- How does this interact with L3b's Context Purge? (Both modify what's in context.)

**Suggestion:** Define a `MessageLifecycle` enum or equivalent:

```python
class MessageVisibility(Enum):
    PERMANENT = "permanent"     # Normal message
    EPHEMERAL = "ephemeral"     # Promise gate: hidden from future context
    PURGED = "purged"           # Hallucination recovery: removed from context
    SYSTEM_ONLY = "system_only" # Visible to system, hidden from LLM
```

Then specify how each visibility level interacts with `assemble_context()`, conversation persistence, and the frontend.

---

### 🟡 Major: L3c (Increasing max_hallucinations to 5) Increases Latency Without Clear Benefit

**Location:** Layer 3c

**Description:** Going from 3 to 5 retries on hallucination means the user potentially waits for 5 failed LLM round-trips before getting an error. With Gemini's typical latency, that's 15-30 seconds of silence. The design proposes "graduated strategies per attempt" but doesn't specify what changes at each stage.

If L3b (Context Purge) works, it should work within 1-2 retries. If it doesn't work in 2, it likely won't work in 5.

**Suggestion:** Keep `max_hallucinations=3` but make each attempt meaningfully different:

| Attempt | Strategy | Rationale |
|---|---|---|
| 1 | Stronger correction prompt | Maybe model just needs nudging |
| 2 | Context Purge + correction | Break the self-reinforcing pattern |
| 3 | **Simplified prompt** — strip all tool descriptions to bare minimum | Reduce cognitive load on model |

If 3 fails, surface to user with an honest message rather than silently retrying.

---

### 🔵 Minor: Layer 4 (Frontend) Is Under-Specified Relative to Its Importance

**Location:** Layer 4

**Description:** "Gray planning bubble" and "hidden retries" are UX decisions that affect user trust. The user seeing their AI "thinking" for 10 seconds with no output (because retries are hidden) may feel worse than seeing a messy retry sequence. This deserves more than a P2 afterthought.

**Suggestion:** At minimum, show a "🔄 Retrying..." indicator so the user knows the system is working, not stuck. Design this alongside L2/L3, not after.

---

### 🔵 Minor: No Telemetry/Observability Plan

**Location:** Entire design

**Description:** There's no mention of logging, metrics, or A/B testing for any layer. Without this:
- How do you know L1's prompt changes achieved "~70% coverage"?
- How do you measure L2's false positive rate?
- How do you know L3b's purge is working?

**Suggestion:** Add a `LoopDiagnostics` event for every retry/gate activation:

```python
@dataclass
class LoopDiagnostic:
    event_type: str  # "promise_gate", "hallucination_recovery", "finality_override"
    model: str
    attempt: int
    trigger_text: str[:200]  # First 200 chars for debugging
    action_taken: str
    timestamp: float
```

This is essential for validating the design's assumptions.

---

## 4. Architecture/Design Observations

### The Real Architecture Problem

The proposal treats two symptoms but the underlying architecture has a tension:

```
CHATTERING PROBLEM ←——— max_consecutive_thoughts ———→ PREMATURE STOP PROBLEM
        ↑                        =1                           ↑
   (AI loops forever)                              (AI promises but stops)
```

This is a **single dial controlling two failure modes**. Turning it one way causes the other. The Promise Gate is essentially a second dial bolted on top. The right fix is a **smarter termination policy** that replaces the single counter with a multi-signal classifier, as described in Issue #1.

### Defense-in-Depth vs. Accidental Complexity

The "4-layer defense" framing is borrowed from security (defense-in-depth), where each layer is independent and any single layer can stop an attack. But here:

- L1 (prompt) and L2 (runtime gate) address the **same issue** (Issue 1) and are not independent — L1's effectiveness determines whether L2 is needed.
- L3 addresses a **different issue** (Issue 2) but shares infrastructure (retry, context modification) with L2.
- L4 addresses neither issue directly — it's presentation.

A cleaner framing is **two orthogonal solutions**:

1. **Finality Classification** (replaces L1 + L2): Better prompt + semantic finality check
2. **Hallucination Recovery** (L3): Context purge + graduated retry

These are independent, testable, and don't create ordering dependencies.

### Gemini-Specific Code is a Maintenance Trap

L3d proposes `ModelFeatures` flags for hallucination-prone models. The Core Agent correctly flags this as premature, but even the softer version (Gemini-specific prompt rules in L1) creates a maintenance burden. Every new model or model version potentially needs its own rules.

**Alternative:** Make hallucination detection model-agnostic. Any model that produces text matching tool-call syntax without actual API tool calls gets the same recovery treatment. The detection is output-based, not model-based.

---

## 5. Actionable Recommendations (Prioritized)

### #1: Replace `max_consecutive_thoughts=1` with a Finality Classifier (P0)

This is the root cause fix. Implement a lightweight `is_semantically_final()` function that uses multiple signals (response length, promise patterns, answer markers, presence of structured content). Set `max_consecutive_thoughts` to 3-5 as a safety ceiling. This subsumes Layer 2 entirely and reduces Layer 1's burden.

**Estimated effort:** ~50 lines. Medium risk (needs testing), but addresses the root cause.

### #2: Implement Context Purge for Hallucination Recovery (P0)

L3b is the highest-value change for Issue 2 and the Core Agent agrees. Implement it as a method on MMU:

```python
def purge_last_assistant_message(self, reason: str) -> bool:
    """Remove the last assistant message if it was a hallucination.
    Returns True if a message was purged."""
```

Keep `max_hallucinations=3` with the graduated strategy I described. Add diagnostic logging.

**Estimated effort:** ~30 lines. Low risk (message removal is well-defined).

### #3: Unify the Retry Architecture Before Adding More Layers (P1)

Before implementing Promise Gate and Hallucination Recovery as separate mechanisms, define a single `ResponseValidator` stage in the pipeline that can detect multiple anomaly types and produce a unified `RetryStrategy`. This prevents the combinatorial explosion of two independent retry mechanisms interacting unpredictably.

**Estimated effort:** ~80 lines (refactor). Medium risk, but prevents future tech debt.

---

## Summary

The problem analysis is excellent, the Core Agent's self-critique is valuable, and L1 + L3b are solid ideas. But the architecture would benefit from stepping back: the real issue is that loop termination is governed by a single counter rather than semantic understanding. Fix the termination policy, and the Promise Gate becomes unnecessary. Fix the context contamination (L3b), and Gemini hallucination recovery becomes robust. Two clean solutions beat four overlapping layers.

---

## Review by `openai-codex/gpt-5.3-codex`

## 1) Overall Assessment

**Score: 8.2/10** — Strong, pragmatic layered design with good prioritization instincts; biggest risk is brittle promise detection and mixing policy enforcement too late in the loop.

---

## 2) Strengths

1. **Good decomposition of two distinct failure modes**  
   You correctly separate:
   - *Intent-to-act but no tool call* (Issue 1)
   - *Malformed tool invocation hallucination* (Issue 2)  
   This avoids a one-size-fits-none fix.

2. **Layered mitigation strategy is directionally sound**  
   The P0→P1→P2 rollout is operationally sensible. Especially strong:
   - **L1 prompt policy first** (cheap, low risk, immediate measurable effect)
   - **L3b context purge** as targeted memory hygiene for self-reinforcing hallucinations

3. **Awareness of termination-policy coupling**  
   You identify the real architectural root: `max_consecutive_thoughts=1` turns any THOUGHT into terminal. That’s not a model issue; it’s loop semantics.

4. **Risk awareness on pattern matching**  
   Your own critique of `"让我"` / `"I'll"` false positives is accurate and important.

---

## 3) Issues Found

### Issue A
- **Severity:** 🟡 Major  
- **Location:** Layer 2 Promise Gate (pattern matching list)  
- **Description:** Current lexical pattern approach is too brittle and language-dependent; it will over-trigger on legitimate final answers (“Let me explain…”, “让我总结一下…”).  
- **Suggestion:** Replace simple phrase matching with a **decision gate based on structured signals**, e.g.:
  - Has actionable commitment language **and**
  - Lacks tool call **and**
  - Contains forward-intent tense (“I will/going to/let me check”) **and**
  - Low “answer completeness” score (heuristic/classifier).  
  If you must ship fast, use a conservative regex + allowlist of final-answer discourse markers (“summary”, “here are”, numbered conclusions).

### Issue B
- **Severity:** 🟡 Major  
- **Location:** Placement of Promise Gate (`_handle_thought`)  
- **Description:** Enforcing this in `_handle_thought` is too late in lifecycle; the THOUGHT may already be persisted/visible and contaminate memory/UI behavior.  
- **Suggestion:** Move gate into **Pipeline/Decoder boundary** (pre-MMU-write, pre-user-visible commit). Treat as “non-committable intermediate output” and re-prompt before state mutation.

### Issue C
- **Severity:** 🟡 Major  
- **Location:** L3b Context Purge  
- **Description:** Raw deletion of hallucinated assistant turns can lose potentially useful latent context (intent, extracted entities, constraints).  
- **Suggestion:** Use **selective purge with tombstoning**:
  - Mark message as `invalid_tool_syntax=true`, exclude from model context assembly
  - Preserve parsed non-tool semantic payload in structured memory (if any)
  - Keep audit trail for debugging/metrics

### Issue D
- **Severity:** 🔵 Minor  
- **Location:** L3c max_hallucinations 3→5  
- **Description:** Increasing retries without adaptive policy can increase latency/cost and user frustration.  
- **Suggestion:** Couple retries to **escalation strategy**:
  1. strict correction format
  2. reduced context
  3. forced tool-choice schema
  4. model switch/fallback (if enabled)
  5. fail-fast with transparent user-facing recovery

### Issue E
- **Severity:** 🟡 Major  
- **Location:** Overall architecture, termination condition  
- **Description:** `max_consecutive_thoughts=1` is a blunt global control; it solves chattiness by sacrificing recoverability.  
- **Suggestion:** Introduce **semantic stop policy**:
  - Finalize only when `(no tool call) AND (answer-complete OR explicit return)`
  - Continue when `(no tool call) AND (intent-to-act detected)`  
  This directly addresses Issue 1 without phrase hacks.

### Issue F
- **Severity:** 🔵 Minor  
- **Location:** L1 Gemini-specific prompt strengthening  
- **Description:** Prompt-only improvements for API-contract adherence often plateau quickly.  
- **Suggestion:** Add **contract-level hard constraints** where possible:
  - Function-call-only mode / JSON schema strictness
  - Decoder rejects plain-text tool-call emulation with deterministic repair loop

---

## 4) Architecture/Design Observations

1. **You need one unified “commit gate” abstraction**  
   Rather than separate Promise Gate + hallucination logic + thought-finalization scattered across handlers, define a single **Output Commitment Layer** that decides:
   - commit to memory?
   - visible to user?
   - terminal?
   - needs repair-reprompt?  
   This reduces policy drift and makes model-specific behavior pluggable.

2. **Distinguish “reasoning text” from “user-facing answer” formally**  
   Current THOUGHT type is overloaded. Add subtypes or metadata:
   - `THOUGHT_INTERNAL`
   - `ANSWER_CANDIDATE`
   - `ACTION_INTENT`  
   Then termination logic can be type-driven, not phrase-driven.

3. **Model-specific strategy should be capability-flagged, not ad hoc**  
   Your L3d instinct is right but timing matters. A light `ModelFeatures` flag (e.g., `strict_function_call_reliability`) is useful now if it only affects retry strategy, not full fallback complexity.

4. **Context purge is correct directionally, but should be “context curation”**  
   Prefer exclusion from next prompt over hard deletion. Keep forensic state for observability and offline tuning.

---

## 5) Actionable Recommendations (Top 3, prioritized)

### 1) Build a **pre-commit output gate** (highest impact)
Implement at Pipeline/Decoder boundary:
- Validate tool-call contract
- Detect non-final action-intent text
- Decide `commit / retry / finalize` before MMU write or loop stop  
This can absorb Promise Gate + hallucination repair in one place.

### 2) Replace `max_consecutive_thoughts=1` with **semantic termination**
Keep anti-chatter behavior, but finalize only on answer completeness (or explicit RETURN), not mere text presence.  
Start with heuristics now; evolve to lightweight classifier later.

### 3) Ship **safe context purge (tombstone + exclude)** for hallucinated turns
Do this immediately for Gemini:
- Exclude malformed-tool assistant messages from assembled context
- Retain audit metadata
- Add metrics: hallucination rate, retries, eventual success, added latency

---

If you execute only these three, you’ll likely solve both issues more robustly than phrase matching while keeping complexity contained.

---

## Review by `google-antigravity/gemini-3-pro-high`

Hello. I am **Google Antigravity**. I have reviewed your design document regarding the Nimbus Agentic Loop Optimization.

Here is my structured review of the proposed architecture for handling Promise Gates and Hallucination Recovery.

# Design Review: Nimbus Agentic Loop Optimization

**Reviewer:** google-antigravity/gemini-3-pro-high
**Date:** 2023-10-27
**Target:** `/Users/DennisWang/SourceCode/ai-coding/nimbus/docs/design/agentic-loop-promise-gate.md`

## 1. Overall Assessment
**Score:** 7/10
**Summary:** The proposal correctly identifies the friction between strict loop termination (`max_consecutive_thoughts=1`) and LLM Chain-of-Thought behavior. While **Layer 3 (Context Purge)** is an excellent architectural insight for Gemini, **Layer 2 (Regex Promise Gate)** introduces a brittle dependency that attempts to solve a semantic problem with syntax rules, likely leading to a high regression rate in conversational quality.

## 2. Strengths

1.  **Context Purge (Layer 3b) is Critical:** Recognizing that Gemini "learns from its own mistakes" in the immediate context window is the strongest technical insight here. Removing the hallucinated message from the MMU prevents the model from overfitting to its own bad syntax in subsequent retries. This addresses the root cause of the "death spiral" in tool calling.
2.  **Layered Defense Strategy:** The separation into Prompt (L1), Runtime (L2), and Recovery (L3) is sound engineering. It allows for "failing fast" (Prompt) and "failing safe" (Recovery).
3.  **Low-Touch Start:** Prioritizing L1 (Prompt Policy) first is the correct rollout strategy. It has zero latency cost and handles the "happy path" without complex code.

## 3. Issues Found

### 🔴 Critical: Brittle Pattern Matching in Layer 2
*   **Location:** Proposed Layer 2 (Runtime Promise Gate) / `PROMISE_PATTERNS`
*   **Description:** Using regex/string matching for intent detection is an architectural anti-pattern for LLMs.
    *   *False Positive Risk:* "Let me explain why this failed" matches `Let me`, triggering a tool-use correction when the user actually wanted an explanation.
    *   *False Negative Risk:* "Searching for that now..." might miss the specific patterns defined.
*   **Suggestion:** Abandon pure Regex. If you must use a heuristic, combine it with:
    1.  **Length Heuristic:** Promises are usually short (< 100 characters). Explanations are long.
    2.  **Negative Lookahead:** `if "Let me" in text AND NOT "explain" AND NOT "summarize"`.
    *   *Better Alternative:* See "Actionable Recommendations" regarding `is_final` logic.

### 🟡 Major: `max_consecutive_thoughts=1` is the Root Constraint
*   **Location:** Problem Summary / Current Architecture
*   **Description:** The entire design is contorting to fit the arbitrary constraint of `max_consecutive_thoughts=1`. Models *need* to think. By forcing `thought=1` to be final, you are punishing the model for Chain-of-Thought (CoT).
*   **Suggestion:** Change the termination condition. Instead of `max_consecutive_thoughts=1`, use **content-based termination**:
    *   The loop ends ONLY if the model emits a specific `FINAL_ANSWER` token/structure, OR if `max_consecutive_thoughts >= 3`.
    *   This removes the need for the "Promise Gate" entirely. If the model says "Let me search", it's just Thought #1. It loops back, sees it hasn't called a tool, and calls it in Thought #2.

### 🔵 Minor: Location of Promise Gate Logic
*   **Location:** Core Agent Analysis / Question 4
*   **Description:** Placing logic in `_handle_thought` creates a tight coupling between the execution engine and the parsing logic.
*   **Suggestion:** This belongs in the **Decoder** layer. The Decoder's job is to translate Raw LLM Output -> Semantic Action. Detecting that a text output is actually a "botched tool call" or a "promise" is a decoding concern.

## 4. Architecture/Design Observations

*   **The "Context Poisoning" Effect:** Your observation regarding Gemini hallucinating tool calls as text is a specific instance of *Context Poisoning*. The architecture needs a standard `Transaction` mechanism for the MMU.
    *   *Current:* Write to MMU -> Execute -> If fail, add correction.
    *   *Proposed:* Open Transaction -> Execute -> If fail, **Rollback** (Purge) -> Add correction -> Retry. This generalizes Layer 3b.
*   **Feedback Loops:** The system lacks a feedback loop for the Prompt Layer. If Layer 2/3 triggers frequently, the System Prompt (Layer 1) needs dynamic adjustment (e.g., injecting "You previously failed to call tools, use the API now" into the next turn's system instruction).

## 5. Actionable Recommendations (Prioritized)

1.  **Implementation of Layer 3b (Context Purge/Transaction Rollback):**
    *   **Action:** Modify the Loop to support "ephemeral steps". If the `Decoder` detects a hallucinated tool call (text-based), the `vCPU` should discard the AI's last message from the context before sending the retry prompt. This is the highest ROI fix for Issue 2.

2.  **Replace Regex Gate with "Soft" Loop Extension:**
    *   **Action:** Do not implement a hard "Gate" that errors out. Instead, modify the `is_final` check:
        ```python
        # Heuristic to allow loop to continue if it looks like a promise
        def is_final_answer(text):
            if len(text) < 60 and any(p in text for p in ["Let me", "I will", ...]):
                return False # Force loop to continue to next step (expecting tool call)
            return True
        ```
    *   This is safer than injecting an error message. It just gives the model another chance to call the tool immediately.

3.  **Refine Prompting (Layer 1) with "Tool-First" constraint:**
    *   **Action:** Change the instruction from "Call it immediately in the same response" to "You must output the Tool Call block *before* any explanatory text."
    *   Most models follow `Thought -> Tool` or `Tool -> Explanation` better than `Explanation -> Tool`. Forcing Tool-first prevents the "Promise" text from ever generating as a standalone message.

### Answers to Your Questions
1.  **4-Layer Approach?** Too complex. Collapse into: 1. Stronger System Prompt (Tool-First). 2. Context Purge on Error. 3. Soft Heuristic for loop continuation (not error injection).
2.  **Detection:** Use Length + Keyword heuristic. Promises are short. Anything > 2 sentences is likely a final answer.
3.  **Context Purge Safe?** Yes, absolutely safe for *hallucinations*. If the output was malformed, it has no semantic value for the history.
4.  **Location:** `Decoder` layer.
5.  **Max Thoughts:** 1 is too strict. Bump to 2 or use the "Soft Loop Extension" heuristic mentioned above.
6.  **Alternatives:** Fine-tuning (expensive), or "Tool Use Force" decoding (where you constrain the generation to start with a specific token if intent is detected), but that requires lower-level access.

**Codebase Navigation:**
I am ready to pair program on the `Decoder` implementation or the `Context Purge` logic in `vCPU` whenever you are ready.

---
