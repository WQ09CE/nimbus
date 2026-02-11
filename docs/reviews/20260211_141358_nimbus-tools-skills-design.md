# AI Review Committee: nimbus-tools-skills-design

- **Date**: 2026-02-11 14:13:58
- **Focus**: architecture
- **Reviewers**: 3
- **Total Time**: 92.5s

---

## Review by `anthropic/claude-opus-4-6`

# Architecture Review: Nimbus Agent OS — Tools & Skills System Design

**Reviewer**: anthropic/claude-opus-4-6
**Focus**: Architecture
**Date**: 2025-01-XX

---

## 1. Overall Assessment

**Score: 6.5/10** — A thoughtful three-layer architecture with a strong "File System as API" philosophy, but undermined by significant security gaps (YOLO mode, missing skill RBAC), inconsistent registration paths, and several design decisions that create privilege escalation vectors in production.

---

## 2. Strengths

### 2.1 Clean Layered Separation (Section 2)
The Kernel → Orchestration → Skill layering follows a well-established pattern (kernel/services/userspace). The philosophical grounding is sound: each layer has a clear abstraction level and responsibility boundary.

### 2.2 "4 Tools Are All You Need" Philosophy (Section 1, 3)
Reducing the kernel surface to Read/Write/Edit/Bash is an elegant minimalist design. This is reminiscent of Unix philosophy — compose complex behavior from simple primitives. It constrains the attack surface at the kernel layer and makes the system easier to reason about.

### 2.3 Dual-Agent Separation (Section 4)
The Core (read-only architect) vs Executor (full-permission engineer) split is a genuinely good security pattern. This is principle-of-least-privilege applied at the agent level, not just the tool level. The Dispatch flow (snapshot → spawn → diff → return) provides auditability.

### 2.4 Honest Known Issues Section (Section 8)
Documenting 8 known issues with severity markers shows engineering maturity. Too many design docs omit known weaknesses. This enables prioritized remediation.

### 2.5 Skill System Design (Section 5)
SKILL.md as interface definition is elegant — it's human-readable, version-controllable, and requires zero infrastructure beyond a filesystem. The automatic interpreter detection (.py→python3, .sh→bash) reduces friction for skill authors.

---

## 3. Issues Found

### 🔴 Critical

#### Issue C1: Sandbox YOLO Mode Completely Negates Security Architecture
- **Location**: Section 3 (Sandbox), Section 8 Issue #2
- **Description**: The document acknowledges Read has "switched to YOLO Mode" bypassing the sandbox. But Read is available to the **Core** agent — the one explicitly designed to be "read-only" and safe. If Read can escape the sandbox, Core can read arbitrary system files (SSH keys, environment variables, credentials, `/etc/shadow`). This isn't just a Read concern — it means the entire Dual-Agent security model's foundation is compromised. A "read-only" agent with unrestricted filesystem read is not safe.
- **Suggestion**: 
  1. Immediately re-enable sandbox for Read with a properly scoped allowlist
  2. If YOLO mode is needed for development, gate it behind an explicit `NIMBUS_DEV_MODE=true` environment variable that cannot be set by any agent tool
  3. Add filesystem read audit logging regardless of mode

#### Issue C2: Skill Tools Have No RBAC Enforcement
- **Location**: Section 6 (RBAC), Section 8 Issue #3
- **Description**: Skills are listed as available to both Core and Standard roles. But skills execute arbitrary scripts via `asyncio.create_subprocess_exec`. A skill could trivially perform write operations, network access, or system modifications. This means **Core agent bypasses its read-only restriction entirely by invoking any skill**. The RBAC table says Core can't use Write/Edit/Bash, but a skill can do everything those tools do.
- **Suggestion**:
  1. Add a `permissions` field to SKILL.md manifest (e.g., `permissions: [fs:read, network:outbound]`)
  2. SkillManager must validate skill permissions against the calling agent's role
  3. Consider running skills in a sandboxed subprocess with restricted capabilities (e.g., `seccomp`, `landlock`, or at minimum chroot)

#### Issue C3: CoreBash Blacklist is Bypassable
- **Location**: Section 4 (CoreBash)
- **Description**: Blacklist-based security is fundamentally flawed. The document mentions "redirect detection, curl security checks, pipe segment checking" but blacklists are always incomplete. Examples of bypasses:
  - `python3 -c "import os; os.system('rm -rf /')"` — code execution via interpreter
  - `$(cat /etc/shadow)` — command substitution in arguments
  - `busybox rm file` — alternate binaries
  - Base64 encoded commands: `echo 'cm0gLXJmIC8=' | base64 -d | sh`
  - Symbolic link manipulation to bypass path checks
- **Suggestion**: Switch to a **whitelist** approach. Define exactly which commands Core can run (e.g., `grep`, `find`, `cat`, `wc`, `head`, `tail`, `ls`, `tree`, `git log`, `git diff`). Anything not on the whitelist is denied. This is a fundamental security architecture decision.

### 🟡 Major

#### Issue M1: Dual Registration Paths Create Inconsistency
- **Location**: Section 7, Section 8 Issues #4 and #5
- **Description**: Having `@tool` decorator global registration AND `AgentOS.__init__` direct registration means tools can exist in one registry but not the other. Memo's "special registration path" is a code smell — if the registration system can't handle a core tool, the system is incomplete. This will cause debugging nightmares and potential security gaps (a tool might bypass RBAC if registered through the wrong path).
- **Suggestion**: Unify to a single registration mechanism. The `@tool` decorator should be the **only** way to define tools. `AgentOS.__init__` should discover decorated tools, not register them independently. Memo should use the standard path.

#### Issue M2: ToolRegistry Silent Override
- **Location**: Section 8 Issue #6
- **Description**: Allowing silent tool override means a malicious or buggy skill could replace a kernel tool (e.g., replace `Read` with a version that exfiltrates data). Combined with hot-reload (ReloadSkills), this is exploitable at runtime.
- **Suggestion**: 
  1. Layer 1 tools should be immutable after initial registration — reject any override attempt
  2. Layer 2 tools should require explicit `force=True` with logging
  3. Layer 3 tools can be reloaded but should be namespaced (e.g., `web-search:WebFetch`) to prevent collision with kernel tools

#### Issue M3: Dispatch Resource Limits Are Generous
- **Location**: Section 4 (Dispatch)
- **Description**: Max 8 dispatches × 120s each = potentially 960s of executor time, with executor_max_iterations=15 per dispatch. That's 120 total LLM iterations possible in a single session. Combined with Bash access in Executor, this is a significant cost and safety exposure. There's no mention of:
  - Token budget limits
  - Cost circuit breakers
  - Recursive dispatch prevention (can Executor dispatch?)
  - Rate limiting across sessions
- **Suggestion**: Add a cost accounting system. Prevent Executor from calling Dispatch (no recursive spawning). Add a per-session token/cost budget.

#### Issue M4: Skill Hot-Reload Prompt Injection Risk
- **Location**: Section 8 Issue #7, Section 5
- **Description**: If skills inject instructions into the system prompt and hot-reload can't clear them, then: (1) the prompt grows unboundedly over reloads, (2) old/conflicting instructions persist, (3) a skill that's been "unloaded" still influences agent behavior. This is especially dangerous because SKILL.md is user-authored content being injected into system prompts — a classic prompt injection vector.
- **Suggestion**: 
  1. System prompt should be **regenerated from scratch** on each reload, not appended to
  2. Skill instructions should be sandboxed in a clearly delimited section of the prompt
  3. Consider separating skill instructions from system prompt entirely — use tool descriptions instead

#### Issue M5: No Authentication/Provenance for Skills
- **Location**: Section 5
- **Description**: Any directory with a SKILL.md placed in `skills/` is auto-discovered and loaded. There's no signing, no checksum, no trust model. In a multi-user or networked environment, this is a supply-chain attack vector. Even in single-user mode, a compromised skill persists silently.
- **Suggestion**: Add at minimum:
  1. A `skills.lock` manifest listing expected skills with checksums
  2. Log warnings for new/modified skills
  3. Consider a `trusted: bool` field that gates whether skills can access network or filesystem

### 🔵 Minor

#### Issue m1: Verify Tool Scope Is Limited
- **Location**: Section 4 (Verify)
- **Description**: 8 check types are reasonable for MVP but missing common verification needs: JSON schema validation, HTTP endpoint health checks, file permission checks, directory structure validation, git state checks (clean/dirty, branch).
- **Suggestion**: Design Verify to be extensible — allow skills to register custom verification checks.

#### Issue m2: Model Alias Mapping in Dispatch
- **Location**: Section 4 (Dispatch)
- **Description**: Hard-coded model aliases (claude/gpt/gemini) in Dispatch couples the orchestration layer to specific providers. This will require code changes for every new model.
- **Suggestion**: Move model configuration to a config file or environment. Dispatch should accept model identifiers without knowing about specific providers.

#### Issue m3: Document Doesn't Specify Error Handling Strategy
- **Location**: Throughout
- **Description**: No mention of how tool failures propagate. Does a failed Edit retry? Does a Bash timeout return partial output? Does a skill crash get caught? Error handling strategy is essential architecture.
- **Suggestion**: Add a section on error taxonomy and handling: retriable vs fatal, timeout behavior, partial result semantics, error propagation across the Dispatch boundary.

---

## 4. Architecture/Design Observations

### 4.1 The Fundamental Tension: Convenience vs Security
The architecture shows a pattern I'd call **"security erosion under development pressure"**. The original design has sound security principles (sandbox, RBAC, dual-agent separation), but each has been partially bypassed:
- Sandbox → YOLO mode
- RBAC → Skills bypass role restrictions  
- CoreBash → Blacklist instead of whitelist
- ToolRegistry → Silent overrides allowed

This suggests the security model was designed top-down but implemented bottom-up, with pragmatic shortcuts accumulating. **The architecture needs a security hardening pass before any production use.**

### 4.2 Namespace and Identity
Tools across all three layers share a flat namespace. There's no concept of tool identity, versioning, or provenance. As the skill ecosystem grows, name collisions become inevitable. Consider: `{layer}:{category}:{tool}` naming (e.g., `skill:web-search:WebFetch`, `kernel:fs:Read`).

### 4.3 Missing Observability Layer
No mention of logging, tracing, metrics, or audit trails. For an "Agent OS," observability is not optional — it's how you debug agent behavior, detect misuse, and understand performance. Every tool invocation should produce a structured log entry with: timestamp, caller role, tool name, parameters (sanitized), result status, duration.

### 4.4 Concurrency Model Unclear
ReviewCommittee does parallel execution. Dispatch spawns subprocesses. Skills use asyncio. But the document doesn't describe the concurrency model: Is there a shared event loop? Can tools conflict (two Bash commands writing the same file)? Is there filesystem locking? This matters for correctness.

### 4.5 The Skill System Has Platform Potential
Despite the security concerns, the "directory = skill, SKILL.md = interface" design is genuinely elegant and has platform potential. With proper sandboxing, signing, and a registry, this could become a powerful ecosystem. The `skill-creator` skill (a skill that creates skills) is particularly interesting — it's the system bootstrapping itself.

---

## 5. Actionable Recommendations (Prioritized)

### Priority 1: Fix the Security Foundation 🔴
**Why**: Three critical issues (C1, C2, C3) each independently allow privilege escalation. Together, they make the RBAC system essentially decorative.

**Actions**:
1. Re-enable Read sandbox with explicit path allowlists
2. Switch CoreBash from blacklist to whitelist
3. Add permission declarations to SKILL.md and enforce them in SkillManager
4. Make Layer 1 tool registrations immutable (prevent override)
5. Prevent recursive Dispatch (Executor → Dispatch → Executor)

**Estimated effort**: 2-3 days for a security-focused sprint

### Priority 2: Unify Tool Registration and Add Observability 🟡
**Why**: Dual registration paths (Issue M1) create confusion and potential security gaps. Lack of observability makes debugging and security auditing impossible.

**Actions**:
1. Consolidate to single `@tool` decorator registration path
2. Add structured logging to ToolRegistry (every register, invoke, override)
3. Add invocation audit trail (who called what, when, with what params)
4. Namespace tools by layer to prevent collisions

**Estimated effort**: 3-4 days

### Priority 3: Harden Skill System for Growth 🟡
**Why**: The skill system is the extensibility story. Getting it right now prevents an exponentially harder migration later.

**Actions**:
1. Add `skills.lock` with checksums for integrity verification
2. Regenerate (don't append) system prompts on skill reload
3. Add skill-level permission model (`permissions: [fs:read, net:out]`)
4. Namespace skill tools (`skill-name:tool-name`)
5. Add versioning to SKILL.md manifest

**Estimated effort**: 4-5 days

---

## Summary

The architecture's **conceptual design** is strong — three-layer separation, Unix-inspired minimalism, file-system-as-API, dual-agent security model. These are good ideas grounded in proven patterns.

The architecture's **implementation fidelity** to those concepts is where it breaks down. Nearly every security boundary has a documented bypass, the registration system has two competing mechanisms, and the skill system's power (arbitrary script execution) is completely ungated by the RBAC system that's supposed to constrain it.

The honest self-assessment in Section 8 is valuable, but the 8 listed issues undercount the real problems. Issues #2 and #3 alone are sufficient for a Core agent to perform arbitrary file writes and system modifications, which means the Dual-Agent architecture's fundamental security guarantee ("Core is read-only") does not hold.

**Bottom line**: Fix the security foundation before adding features. The conceptual architecture deserves an implementation that lives up to it.

---

## Review by `openai-codex/gpt-5.3-codex`

## 1) Overall Assessment

**Score: 7.5/10** — 架构方向正确、分层思路有价值，但当前实现层面的“边界可穿透性”和注册/权限一致性问题会削弱体系可靠性与安全性。

---

## 2) Strengths

1. **三层模型叙事清晰，符合演进式系统设计**
   - Layer 1（稳定 primitive）→ Layer 2（编排/治理）→ Layer 3（可扩展能力）这一分层在概念上合理，利于长期演化。
   - “File System as API”将 Skill 交付标准化为目录 + `SKILL.md`，降低扩展门槛。

2. **Dual-Agent 架构有明确的职责分离意图**
   - Core（只读 + 决策）/Executor（执行）是良好的 least-privilege 思路，尤其与 `Dispatch` 配合时，便于把“计划”和“落地”分开审计。

3. **工具基础设施具备跨模型抽象能力**
   - `ToolDefinition` 兼容 Claude/OpenAI 调用格式、`ToolParameter -> JSON Schema`、sync/async 透明等，说明平台层抽象做得较扎实。

4. **运行时可观测性与可用性考虑较实用**
   - `Read` 分页/截断、`Bash` 流式输出与大输出落盘、`Edit` diff 输出、`ReloadSkills` 热重载，都是工程上高频痛点的正向处理。

5. **文档已主动暴露已知问题**
   - 能明确列出8个风险点本身是成熟信号，便于治理排期。

---

## 3) Issues Found

### Issue A — Read 沙箱 YOLO Mode 导致边界失效
- **Severity**: 🔴 Critical  
- **Location**: §3 Sandbox（“Read已切换YOLO Mode”）+ §8(2)  
- **Description**: Core/Reviewer 等“只读角色”仍可借 Read 越界读取敏感文件，直接破坏 RBAC 与工作区边界。只要可读，即可泄露密钥、系统配置、SSH材料。  
- **Suggestion**:  
  1) 立即恢复 `Read` 到严格沙箱（workspace allowlist + denylist + symlink 解析）。  
  2) 增加路径 canonicalization（`realpath`）与 TOCTOU 防护。  
  3) 将“YOLO”仅作为显式 debug feature flag，默认关闭且仅本地开发可用。

---

### Issue B — Skill Tools 无角色限制，形成权限旁路
- **Severity**: 🔴 Critical  
- **Location**: §6 RBAC + §8(3)  
- **Description**: Skill 工具可执行脚本（`.py/.sh/.js`），若不受 RBAC 限制，相当于向低权限角色开放了“任意能力注入”。可绕开 Layer 1/2 安全策略（例如通过脚本进行文件写入、网络调用、命令执行）。  
- **Suggestion**:  
  1) Skill 工具注册必须走同一 RBAC 策略引擎，按 role 显式 allow。  
  2) 每个 Skill 在 manifest 声明 capability（fs_read/fs_write/net/exec等），由策略层审批。  
  3) 默认 deny：未声明 capability 或未授权 role 的 Skill 不可加载。

---

### Issue C — 工具注册双路径 + 静默覆盖，破坏一致性与可审计性
- **Severity**: 🟡 Major  
- **Location**: §7 两阶段注册 + §8(5)(6)  
- **Description**: `@tool` 全局注册与 `AgentOS` 直接注册并存，且 `ToolRegistry.register` 允许静默覆盖。结果是同名工具来源不确定、行为可被悄悄替换，审计困难。  
- **Suggestion**:  
  1) 统一单一路径（建议集中到 ToolRegistry + 明确生命周期 hook）。  
  2) 同名注册默认抛错；若允许 override，必须 `explicit=True` 并记录审计日志。  
  3) 启动时输出最终工具清单（name/version/source/role visibility）。

---

### Issue D — Prompt 注入不可回收，热重载后状态污染
- **Severity**: 🟡 Major  
- **Location**: §8(7)  
- **Description**: Skill 热重载无法清除已注入 system instructions，会导致“幽灵能力/过期规则”持续影响后续会话，违反配置即代码的一致性预期。  
- **Suggestion**:  
  1) Prompt 采用分层片段（base + role + skill set hash）重建，而非增量拼接。  
  2) Reload 时触发 prompt recompilation，并基于 skill manifest hash 做幂等更新。  
  3) 在会话元数据中记录 prompt provenance 便于排障。

---

### Issue E — ScriptTool 执行模型安全边界过宽
- **Severity**: 🟡 Major  
- **Location**: §5 ScriptTool执行  
- **Description**: 自动解释器识别 + kwargs→CLI 参数转换，若无严格参数编码、超时/资源限制、环境隔离，容易引入命令注入、资源滥用、数据外泄。  
- **Suggestion**:  
  1) 参数传递必须使用 argv 列表，不经 shell。  
  2) 统一执行沙箱（cwd 限制、env allowlist、CPU/内存/时长限制、网络策略）。  
  3) 为 Skill Tool 增加 seccomp/container 级隔离（至少对不受信技能）。

---

### Issue F — CoreBash 黑名单策略可绕过（策略模型问题）
- **Severity**: 🟡 Major  
- **Location**: §4 CoreBash  
- **Description**: 以黑名单为主的命令过滤天然脆弱，易被编码/管道/子进程/解释器链绕过。  
- **Suggestion**:  
  1) 从黑名单转向能力白名单（允许命令集合 + 参数模式）。  
  2) 对“命令执行”做语义级策略（例如仅允许只读诊断命令）。  
  3) 高风险命令要求二次确认或策略审批。

---

### Issue G — Dispatch 资源预算与隔离策略未形成“硬约束”定义
- **Severity**: 🔵 Minor  
- **Location**: §4 Dispatch  
- **Description**: 有 max dispatch/time budget 配置是好事，但未说明超限后的回收机制、子进程清理、并发上限与背压策略。  
- **Suggestion**:  
  1) 明确超时后 kill tree、临时文件清理、token/成本预算。  
  2) 加入并发队列与拒绝策略，避免资源雪崩。  
  3) 产出统一 telemetry（dispatch_id、cost、duration、result）。

---

### Issue H — RBAC 角色语义与工具能力映射仍偏粗粒度
- **Severity**: 🟡 Major  
- **Location**: §6 RBAC  
- **Description**: 当前按“工具名”授权，不按“能力”授权；同一工具内部能力可能过宽（如 Bash/Skill）。这会导致一授全授。  
- **Suggestion**:  
  1) RBAC 升级为 RBAC + Capability/Policy（ABAC）混合模型。  
  2) 对工具参数级别做策略（路径前缀、命令类别、网络域名）。  
  3) 引入 policy test suite，防止策略回归。

---

## 4) Architecture/Design Observations

1. **分层是对的，但“执行平面”与“策略平面”耦合不够**
   - 现在看起来工具很多，但统一策略内核不足（权限、沙箱、审计在多处散落）。建议建立一个**Policy Enforcement Point (PEP)**，所有工具调用统一过闸。

2. **Layer 3 的扩展性强，但信任模型尚未闭环**
   - “目录即技能”很高效，但需要配套“技能供应链安全”：签名、来源、版本锁定、权限声明、审核状态。不然扩展性越强，攻击面越大。

3. **Dual-Agent 的安全价值取决于不可绕过性**
   - 只要 Core 仍可通过 Skill/Read 侧向拿到强能力，Dual-Agent 就退化为“名义分离”。需要从“角色定义”升级为“技术强制隔离”。

4. **注册流程应从“方便开发”转向“可审计生产”**
   - 双路径注册在原型期可接受，进入平台期应严格 single source of truth。

---

## 5) Actionable Recommendations (Top 3, prioritized)

1. **先做安全封口（P0）**  
   - 关闭 Read YOLO、Skill 默认 deny、统一 Skill RBAC + capability gating。  
   - 这是当前最关键的权限逃逸修复。

2. **统一策略与注册内核（P1）**  
   - 建立统一 `PolicyEngine + ToolRegistry` 调用链：注册唯一入口、禁止静默覆盖、全量审计。  
   - 解决“看似分层，实则多入口可绕过”的架构债。

3. **重构 Skill 生命周期与 Prompt 生命周期（P1）**  
   - Skill reload 改为声明式重建（含 prompt 重编译、旧状态清理、版本/hash 管理）。  
   - 把 Layer 3 从“能跑”提升到“可控、可回滚、可追踪”。

---

### 对“已知8个问题是否完整”的结论
不完整。除已列8项外，至少还应补充：
- ScriptTool 参数/执行隔离风险（命令注入与资源滥用）
- CoreBash 黑名单绕过风险（策略模型问题）
- 注册覆盖审计缺失（供应链/完整性）
- Dispatch 超时/并发回收机制缺失（稳定性风险）
- 技能来源信任与签名/版本锁定缺失（供应链安全）

总体而言，这份文档**方向优秀、工程意识强**，但要成为“可在复杂环境长期运行”的 Agent OS，需要把“权限与策略一致性”提升为一等公民。

---

## Review by `google-antigravity/gemini-3-pro-high`

# Review: Nimbus Agent OS — Tools & Skills Architecture

**Reviewer:** google-antigravity/gemini-3-pro-high
**Date:** 2023-10-27
**Scope:** Architecture, Security, Scalability of the 3-Layer Tool System

## 1. Overall Assessment
**Score:** 8/10
**Summary:** A robust, UNIX-philosophy-inspired architecture with a sophisticated "File System as API" skill pattern and strong Dual-Agent separation, though currently compromised by security implementation details (Blacklisting, RBAC leaks).

## 2. Strengths

*   **"File System as API" (Layer 3):** The decision to treat a directory as a skill and `SKILL.md` as the interface definition is excellent. It decouples the implementation (Python/Bash/Node) from the definition, making the system highly extensible without code changes to the core.
*   **Dual-Agent Separation of Concerns (Layer 2):** The `Core` (Architect/ReadOnly) vs. `Executor` (Engineer/Write) model is the correct abstraction for complex coding tasks. It mimics real-world engineering workflows.
*   **Pragmatic Kernel Tools (Layer 1):**
    *   **Edit:** Integrating "Fuzzy Matching" as a fallback is a crucial feature for LLMs, which often struggle with exact line/context reproduction.
    *   **Read:** Intelligent truncation (2000 lines/50KB) and "Smart Limit" handling prevent context window overflows, a common failure mode in agentic systems.
*   **Self-Awareness:** The "Known Issues" section is surprisingly honest and identifies key technical debts (e.g., the registration inconsistency), which suggests a healthy engineering culture.

## 3. Issues Found

### 🔴 Critical

*   **RBAC Leakage via Skill System (Layer 3)**
    *   **Location:** Section 6 (RBAC) & Section 8 (Issue #3)
    *   **Description:** The document notes "Skill Tools have no role restrictions," yet the RBAC section grants the `Core` role access to "Skill Tools." This breaks the security model. If a Skill defines a `delete_database` script, the `Core` agent (supposedly Read-Only/Architect) can execute it because it wraps a script execution.
    *   **Suggestion:** Implement mandatory permission declarations in `SKILL.md` frontmatter (e.g., `requires_role: executor`). The `ToolRegistry` must enforce this during the `Core` agent's dispatch loop.

*   **Fragile Security in `CoreBash`**
    *   **Location:** Section 4 (CoreBash)
    *   **Description:** Using a **Blacklist** mechanism (checking for `git push`, file ops, etc.) to secure `CoreBash` is fundamentally insecure. It is trivial to bypass blacklists using encoding (Base64), string concatenation, or obfuscation.
    *   **Suggestion:** Switch `CoreBash` to a **Whitelist** approach (only allow `ls`, `grep`, `find`) OR, preferably, remove `CoreBash` entirely and force the Core agent to use structured tools (`Read`, `code-scout`) for exploration.

### 🟡 Major

*   **Registration Race Conditions & Inconsistency**
    *   **Location:** Section 8 (Issue #5 & #6)
    *   **Description:** The conflict between global `@tool` decorators and manual `AgentOS` registration, combined with "Silent Overwrite" in `ToolRegistry`, creates a scenario where the system state is non-deterministic depending on import order.
    *   **Suggestion:** Deprecate one method. Preferably, keep `@tool` for metadata definition but require explicit registration passing the function object to the Registry. Make `ToolRegistry.register` raise an error on duplicate keys by default, requiring an explicit `force=True` flag.

### 🔵 Minor

*   **Sandboxing "YOLO Mode"**
    *   **Location:** Section 8 (Issue #2)
    *   **Description:** While useful for an MVP/Dev mode, having `Read` bypass the sandbox creates a habit of path traversal that will be hard to patch out later without breaking agent behavior prompts.
    *   **Suggestion:** Formalize the concept of "Workspace Root". If `Read` needs to access system libs, mount those read-only into the sandbox configuration rather than disabling the sandbox entirely.

## 4. Architecture/Design Observations

*   **Layering Strategy:** The three layers map well to the cognitive load of the agent:
    *   L1 (Kernel) = Motor functions.
    *   L2 (Orchestration) = Executive function / Planning.
    *   L3 (Skills) = Learned behaviors.
    This is a biologically plausible and architecturally sound hierarchy.
*   **The "Dispatch" Primitive:** The `Dispatch` tool is effectively a recursive RPC call to a sub-agent. This is a powerful pattern. However, the configuration (`max 8 dispatches`) implies a depth/breadth limit that needs to be carefully tuned to prevent infinite loops or cost explosions.
*   **Prompt Coupling:** The `PromptManager` relying on `role` x `model_id` implies a tight coupling between tools and prompts. As tools evolve (especially L3 Skills), the System Prompt needs to dynamically reflect available skills without bloating the context.

## 5. Actionable Recommendations

1.  **Implement RBAC in `SKILL.md`:** Immediately modify the Skill Manifest schema to include a `security_context` or `allowed_roles` field. Default to `['executor', 'standard']` to protect the `Core` agent from accidental destructive acts via scripts.
2.  **Harden the Tool Registry:** Refactor `ToolRegistry` to block silent overwrites. Unify the registration path: use `@tool` strictly for metadata (docstrings, schema generation) and a centralized `register_tools()` bootstrap function for loading.
3.  **Replace `CoreBash` Blacklist:** Abandon the regex/string matching blacklist for `CoreBash`. Either restrict `Core` to `Read` + `code-scout` (structured analysis) or implement a containerized, read-only filesystem mount for the Core agent's bash sessions.

---
