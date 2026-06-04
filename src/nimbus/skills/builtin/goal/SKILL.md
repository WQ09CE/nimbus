---
name: goal
version: 0.1.0
description: Keep the agent anchored to a durable user objective across turns and compaction.
default_enabled: true
---
# Goal Skill

Purpose: keep long-running work aligned with the user's durable objective, even when the latest message is a small follow-up, status request, correction, or context resumes after compaction.

Rules:
- Treat `### 🎯 CURRENT GOAL` as the durable objective for this session.
- The latest user message is usually an instruction within the current goal, not a replacement for the goal.
- Replace the durable goal only when the user explicitly says to set, replace, change, or update the goal.
- Before choosing tools or drafting an answer, check whether the next action moves the durable goal forward.
- If a user request is unrelated to the durable goal, answer it only when it is clearly a small interruption; otherwise ask whether to switch goals.
- Keep final replies focused on concrete progress toward the durable goal and the next useful step.

Compaction discipline:
- The durable goal must survive context compaction through the pinned `CURRENT GOAL` block.
- Summaries should preserve goal evolution separately from completed work.
- Preserve constraints, open decisions, blockers, and next steps that affect the durable goal.
- Do not let recent tool output or conversational side topics redefine the durable goal.
