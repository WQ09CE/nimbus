# AI Review Committee: agentic-loop-poke-termination-bug

- **Date**: 2026-02-19 22:02:28
- **Focus**: architecture
- **Reviewers**: 3
- **Total Time**: 115.8s

---

## Review by `anthropic/claude-opus-4-6`

## Summary

### 1) What I Completed

I delivered a **comprehensive architecture review** of the Nimbus VCPU agentic loop "continuation poke" mechanism, covering:

- **Overall Assessment**: 7/10 — strong problem diagnosis, but proposed solutions operate at the wrong abstraction layer.
- **3 Critical/Major Issues Found**:
  - 🔴 Layer 1's `has_used_tools` flag kills the essential "Read → Reason → Act" agentic pattern
  - 🔴 The loop lacks an explicit Turn Intent taxonomy — the root architectural gap
  - 🟡 Layer 2's "silent poke" via `is_post_poke` flag creates observability black holes and brittle state management
- **Key Architectural Observations**: boolean flag proliferation is a design smell; termination should be a first-class tool; a proper state machine would replace the 16-state boolean space.
- **3 Prioritized Recommendations**: (1) Turn intent classification, (2) scoped suppression via context managers, (3) instrument before changing defaults.

### 2) What Remains Unfinished

- **No code changes were made** — this was a review-only task, and no modifications to the codebase were requested or performed.
- **No follow-up reviews** of related subsystems (MMU smart-drop interaction with ephemeral poke messages, frontend SSE event handling for `THINKING_SILENT`) were conducted.
- The review is **complete as scoped**. No further action is needed from me.

---

## Review by `openai-codex/gpt-5.3-codex`

1) **已完成**  
- 已完成对该架构问题报告的结构化评审（总体评分、优点、问题清单、架构观察、Top 3 改进建议）。  
- 已明确一句话核心结论：`_handle_thought` 的 continuation poke/终止阈值在正常无工具调用场景下触发不必要循环，带来性能浪费与体验下降。  
- 已指出方案中的关键改进方向与风险边界（`has_used_tools`、post-poke 可见性分层、参数下调需 A/B 验证）。

2) **未完成**  
- 尚未基于真实代码仓执行落地修改或提交 patch。  
- 尚未补充自动化测试用例与回归指标（如额外迭代率、首答时延、正确率）。  
- 尚未产出实施后的实验结果或上线策略（灰度/回滚方案）。

---

## Review by `google/gemini-3-pro-preview`

**[Code Reviewer Response]**

I see you are demonstrating the exact "Poke + Hard Stop" failure mode discussed in the report. This is a perfect example of why the current logic is flawed.

**Observation of your trace:**
1.  **The "Poke"**: `[System] You responded with text but didn't use any tools...`
    *   *Diagnosis*: The system failed to recognize your previous actions (likely `Read` operations) as productive work, or you provided a thoughtful analysis that the system interpreted as "stalling."
2.  **The "Hard Stop"**: `[SYSTEM] You have reached your iteration limit...`
    *   *Diagnosis*: Immediately after the poke (which wasted a turn), you hit the hard iteration limit (`max_consecutive_thoughts` or global steps).

**Impact Analysis:**
Because the "Poke" consumed a turn without advancing the state (no tool usage), it pushed you directly into the iteration limit. If the logic proposed in **Recommendation #2 (Split Tool Categories)** were active, the system would have recognized your prior `Read` actions as `has_interacted`, skipped the poke, and allowed you to deliver your final answer gracefully instead of forcing a panic summary.

**Immediate Fix (for the codebase):**
This trace validates the urgency of **Layer 1** (recognizing `Read` as interaction) to prevent this specific "Poke -> Death Spiral."

---
