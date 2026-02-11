# AI Review Committee: tools-category-refactor-proposal

- **Date**: 2026-02-11 15:01:33
- **Focus**: architecture
- **Reviewers**: 3
- **Total Time**: 93.9s

---

## Review by `anthropic/claude-opus-4-6`

# Architecture Review: Tools 三域分类重构提案

**Reviewer:** anthropic/claude-opus-4-6
**Focus:** Architecture
**Date:** 2025-01-XX

---

## 1. Overall Assessment

**Score: 7.5/10** — A well-reasoned, philosophically grounded proposal that correctly identifies the organizational gap. The three-domain model is sound in principle, but several boundary decisions are under-justified and the security philosophy, while internally consistent, needs more explicit documentation of its threat model assumptions.

---

## 2. Strengths

### 2.1 Honest Security Philosophy
The proposal's most valuable contribution is its intellectual honesty about security theater. The argument chain — "Write + Bash = god mode → tool-layer restrictions are illusions → don't pretend otherwise" — is rigorous and rare. Most systems accumulate superficial security layers that create false confidence. Explicitly rejecting this and pushing the security boundary to the human-in-the-loop layer is architecturally courageous and correct *for this threat model*.

### 2.2 Minimal Completeness of Core
The choice of exactly {Read, Write, Edit, Bash} as the OS core is well-defended. These four tools form a genuinely minimal complete set for file-system and process interaction. The "syscall" metaphor is apt — these are primitives from which all other operations can be composed. Importantly, they are stateless and orthogonal.

### 2.3 Phased Migration Strategy
The five-phase migration from "add a field" to "physically separate registries" is pragmatic. Starting with Phase 1 (additive, backward-compatible field) means zero breakage and immediate value in readability. This is textbook evolutionary architecture.

### 2.4 Clear Lifecycle Differentiation
The proposal correctly identifies that the three domains have fundamentally different lifecycles:
- Core: born with the process, dies with the process
- Extension: mounted at profile-configuration time
- Skill: hot-swappable at runtime

This lifecycle distinction is the strongest justification for the three-domain split, more so than any functional categorization argument.

---

## 3. Issues Found

### 🔴 Critical

**Issue 3.1: The Security Philosophy Requires Explicit Scope Boundaries**

- **Location:** 安全哲学（重要前提）section
- **Description:** The argument "Agent has Bash + Write = god mode, so tool-level security is an illusion" is valid **only when the agent is running in a single-user, single-trust-domain context** (i.e., a developer's local machine or a sandboxed container). The proposal does not state this assumption explicitly. If Nimbus ever runs in:
  - Multi-tenant environments
  - Environments where different *roles* have genuinely different OS-level permissions (e.g., a dispatch sub-agent running as a restricted user)
  - Environments where Bash is intentionally neutered (no `sudo`, restricted `PATH`, seccomp)

  ...then the security philosophy breaks down. CoreBash's blacklist *was* ineffective, yes, but the *motivation* for per-role capability restriction doesn't disappear — it just needs a different implementation (OS-level sandboxing, not tool-level filtering).

- **Suggestion:** Add an explicit "Assumptions & Scope" section stating: "This security model assumes single-trust-domain execution. Multi-tenant or privilege-separated deployments require OS-level sandboxing (containers, namespaces, restricted users), not tool-layer restrictions." This protects future developers from misapplying the philosophy.

---

### 🟡 Major

**Issue 3.2: ReloadSkills Classification is Architecturally Inconsistent**

- **Location:** 三域模型, 重构后工具清单
- **Description:** ReloadSkills is marked as `skill` category but `可替换: ❌`. This is a contradiction. The skill domain is defined by "动态加载、可热插拔", yet ReloadSkills is the one tool in that domain that is *not* hot-swappable. It is functionally a **kernel facility for managing the skill subsystem** — analogous to `insmod`/`rmmod` in Linux, which are kernel utilities, not kernel modules themselves.

  The OS metaphor the proposal uses actually answers this clearly: the syscall `dlopen()` lives in the kernel, not in userspace. ReloadSkills is the mechanism by which skills are loaded; it is not itself a skill.

- **Suggestion:** Reclassify ReloadSkills as `core` (it is an OS primitive for managing the skill lifecycle) or create a sub-designation within extension (e.g., "system-service" vs "domain-service"). The former is simpler and more consistent.

**Issue 3.3: The core/extension Boundary Needs a Formal Criterion**

- **Location:** 三域模型, Extension Tools definition
- **Description:** The proposal defines core as "不可替换, 最小完备集" and extension as "可替换, 按 profile 挂载". But the criterion for "不可替换" is not formalized. Consider:

  - **Memo**: The proposal classifies it as extension (replaceable). But if every agent profile needs persistent memory, is it truly optional? If you remove Memo, does the system degrade gracefully or catastrophically?
  - **Dispatch**: If Nimbus's core value proposition is multi-agent orchestration, is Dispatch truly "replaceable"? You could argue it's as fundamental as `fork()` is to Unix.

  Without a formal criterion (e.g., "core = the set of tools without which *no* agent profile can perform *any* task"), the boundary will drift with each new tool.

- **Suggestion:** Define the core admission criterion explicitly. Proposed criterion: **"A tool is core if and only if (a) it performs a primitive I/O operation that cannot be composed from other tools, and (b) every conceivable agent profile requires it."** Under this definition, Dispatch fails (b) because a single-agent profile doesn't need it, and Memo fails (a) because it can be composed from Read + Write + a convention. This makes the current classification correct but for documented reasons.

**Issue 3.4: Method A's category Field Will Accumulate Technical Debt**

- **Location:** 方案 A: 轻量标签
- **Description:** Adding a `category: Literal["core", "extension", "skill"]` field to ToolDefinition is simple, but the proposal doesn't specify:
  1. **Who enforces correct categorization?** If a developer registers a tool with `category="core"` when it should be `extension`, nothing catches it.
  2. **What behavior changes based on category?** If category is purely informational (for listing/documentation), it's a glorified comment that will rot.
  3. **When does the Phase 5 trigger fire?** "按需升级到方案 B" needs concrete criteria, otherwise it never happens or happens too late.

- **Suggestion:**
  - Add a registration-time assertion: core tools can only be registered by `AgentOS.__init__`, not by profiles or plugins.
  - Attach at least one behavioral consequence to category in Phase 1 (e.g., `list_tools()` output groups by category, or the system prompt generator organizes tools by domain).
  - Define Phase 5 trigger: "When the number of extension tools exceeds N, or when tools in different categories need different lifecycle management (e.g., independent restart), migrate to Method B."

---

### 🔵 Minor

**Issue 3.5: The Tool Inventory Table is Incomplete**

- **Location:** 重构后工具清单
- **Description:** The table lists `WebSearch` and `...` for skills. For a proposal that will be referenced during implementation, the ellipsis is insufficient. At minimum, enumerate all *currently existing* skill tools so reviewers can validate the classification.

- **Suggestion:** Add a complete snapshot of current tools with their proposed categories.

**Issue 3.6: Method C Dismissed Too Quickly**

- **Location:** 方案 C: 插件化架构
- **Description:** Method C is dismissed as "过度工程化" in one line. Given that the skill domain *already* has a plugin-like architecture (SKILL.md, dynamic loading, heterogeneous execution), and extensions are already "按 profile 挂载", the system is arguably already halfway to a plugin model. The dismissal should acknowledge what parts of Method C are already emergent in the current design.

- **Suggestion:** Reframe: "Method C is the asymptotic target. Method A → B → C is the natural evolution. We are not building C now because we don't yet have the use cases that require plugin isolation (independent versioning, sandboxed failure domains). When we do, Method B's separate registries provide the natural seam."

---

## 4. Architecture/Design Observations

### 4.1 The Taxonomy Mirrors Operating System Design — Lean Into It

The proposal already uses the OS metaphor (syscalls, kernel, userspace). This metaphor is powerful and should be made more explicit:

| Nimbus Concept | OS Analog | Implication |
|---|---|---|
| Core Tools | Syscalls (read, write, exec, open) | Stable ABI, never removed |
| Extension Tools | Kernel modules / system daemons | Loaded by config, restartable |
| Skill Tools | Userspace applications | Installed/uninstalled freely |
| ReloadSkills | `dlopen` / package manager | Lives in kernel, manages userspace |
| ToolRegistry | `/proc/modules` + `PATH` | Unified namespace, tiered discovery |

Making this mapping explicit in the architecture document will help future developers reason about where new tools belong.

### 4.2 Missing: Tool Dependency Model

The proposal doesn't address tool dependencies. Examples:
- Dispatch depends on Read (to read agent profiles)
- Verify might depend on Bash (to run tests)
- Skill tools might depend on core tools internally

If tools can depend on each other, the three-domain model should enforce a dependency direction: **skill → extension → core**, never the reverse. This is the analog of the "userspace calls kernel, kernel doesn't call userspace" principle. Without this rule, you risk circular dependencies that make the layering meaningless.

### 4.3 Missing: Error Semantics Per Domain

Different domains should potentially have different failure modes:
- **Core tool failure** = system-level error (fatal or retry at OS level)
- **Extension tool failure** = service degradation (graceful fallback possible)
- **Skill tool failure** = application error (skip, retry, or use alternative skill)

This is a natural consequence of the layering and would be the first *behavioral* benefit of categorization, making Method A immediately more than just a label.

### 4.4 The "Bash Subsumes All" Observation Has a Corollary

If Bash can do everything, then *every* tool above core is technically a convenience wrapper around Bash commands. This is true but not a reason to flatten everything — Unix has `cp` even though `dd` exists. The value of higher-level tools is:
1. **Semantic clarity** for the LLM (a structured tool is easier to use correctly than a raw bash command)
2. **Auditability** (a `Dispatch` call is easier to log/trace than `bash -c "python dispatch.py ..."`)
3. **Composability** (structured inputs/outputs vs. string parsing)

The proposal should explicitly state these justifications for *why we have tools beyond core at all*, because the security philosophy section could be misread as "Bash is all you need."

---

## 5. Actionable Recommendations (Prioritized)

### Priority 1: Formalize the Domain Boundary Criteria

Add an explicit, testable definition for what makes a tool `core` vs `extension` vs `skill`. Without this, every new tool addition will trigger a classification debate. My suggested criteria:

```
core:      Primitive I/O operation ∧ Required by all profiles ∧ Stateless ∧ No dependencies on other tools
extension: System service ∧ Required by some profiles ∧ May be stateful ∧ May depend on core tools
skill:     User-facing capability ∧ Dynamically loadable ∧ Self-describing ∧ May depend on core or extension tools
```

Move ReloadSkills to `core` under this framework (it's a primitive lifecycle operation required by any profile that uses skills, and it's stateless).

### Priority 2: Add a Security Scope Statement

The "security is an illusion at tool layer" philosophy is correct but dangerous if quoted out of context. Add a one-paragraph scope statement that says: *"This applies to single-trust-domain deployments. Multi-tenant security requires OS-level isolation, which is orthogonal to tool-layer classification."* This costs nothing and prevents future architectural mistakes.

### Priority 3: Attach Behavioral Semantics to Categories in Phase 1

Don't let `category` be a dead field. In Phase 1, at minimum:
- **Registration enforcement**: Core tools registered only during OS init; raise on late registration.
- **Prompt generation**: Group tools by category in the system prompt, with a one-line domain header.
- **Logging/tracing**: Include category in tool invocation logs for easier debugging.
- **Dependency direction**: Assert that core tools never import from extension or skill modules.

This ensures the categorization has immediate value and creates pressure to maintain it correctly.

---

## Summary

The proposal is architecturally sound in its core insight: tools have different lifecycles and trust levels, and this should be reflected in the code structure. The three-domain model maps well to OS design principles and the phased migration strategy is pragmatic.

The main risks are: (1) boundary criteria remaining informal, leading to classification drift; (2) the security philosophy being misapplied in future deployment contexts; and (3) the category field becoming a dead annotation with no behavioral consequences.

Address these three risks, reclassify ReloadSkills, and this proposal is ready for implementation.

---

## Review by `openai-codex/gpt-5.3-codex`

## 1) Overall Assessment

**Score: 8.5/10** — 方向正确、抽象层次明显提升；作为“先落地再演进”的重构提案很稳，但还需要补齐若干边界定义与运行时治理细节。

---

## 2) Strengths

1. **三域模型有清晰意图，且与生命周期对齐**
   - 你把 core/extension/skill 分别映射到“内核原语 / 系统服务 / 用户态插件”，这不是仅按功能分组，而是按**可替换性 + 生命周期 + 装载方式**分层，架构上是正确的。

2. **安全哲学前提写得很清楚，避免“伪安全”设计**
   - 明确“Bash+Write 即 God mode”，并据此拒绝工具层黑名单，这在模型能力足够强时是现实主义做法，能避免团队在错误方向持续投入。

3. **方案分级合理（A→B→C）**
   - A 作为低风险起步，B 作为结构升级点，C 明确标注为潜在过度工程。这个决策框架利于迭代，不会一次性推高复杂度。

4. **迁移路径分 phase，工程可执行性强**
   - 有步骤地引入 category、打标、移除 CoreBash、更新 prompt/doc，基本覆盖代码与行为层变更。

---

## 3) Issues Found

### Issue 1
- **Severity:** 🟡 Major  
- **Location:** 三域定义（core/extension/skill）+ 迁移策略 Phase 1 默认值  
- **Description:** `category` 默认 `"core"` 有语义风险。  
  在未完成全面打标前，会把 extension/skill 误归入 core，影响观测、策略、甚至 prompt 选择逻辑（如果后续依赖 category）。  
- **Suggestion:**  
  - 默认值改为 `"uncategorized"`（或 `None`），并在启动时对 uncategorized 发出 warning / telemetry。  
  - 将“必须显式 category”作为 lint/check（CI）规则，避免长期脏状态。

---

### Issue 2
- **Severity:** 🟡 Major  
- **Location:** “可替换”定义（表格 + 三域边界）  
- **Description:** “可替换”语义未形式化。  
  现在看起来是概念层描述，但缺少技术约束：  
  - core 不可替换是“代码约定”还是“注册器强约束”？  
  - extension 可替换是否允许重名覆盖？运行中热替换还是仅启动期？  
- **Suggestion:**  
  定义最小契约：  
  - `core`: name 保留字，禁止覆盖/卸载；  
  - `extension`: 可按 profile 注册/替换，需版本与能力声明；  
  - `skill`: 允许热插拔，但要求 namespace（如 `skill.<name>`）避免冲突。  

---

### Issue 3
- **Severity:** 🟡 Major  
- **Location:** ReloadSkills 归属讨论  
- **Description:** ReloadSkills 作为“系统控制面入口”被归为 skill，存在层次反转风险。  
  它操作的是 skill 子系统本身（管理平面），而非普通用户态能力（数据平面）。  
- **Suggestion:**  
  我建议 **归 extension 更稳妥**（或单列 `control-plane` 标签）：  
  - 执行层面它是 orchestrator/manager 能力；  
  - 语义上它“管理 skill”，不应与被管理对象同层。  
  如坚持归 skill，至少在文档中声明其“bridge/control”特例身份。

---

### Issue 4
- **Severity:** 🔵 Minor  
- **Location:** 移除 CoreBash 决策  
- **Description:** 结论基本正确，但缺失“替代防线”落地说明。  
  你强调安全边界在人机交互层，但没写清具体机制：高危命令确认策略、批量操作确认、审计日志。  
- **Suggestion:**  
  补一段“非工具层安全控制清单”：  
  - 用户确认 gating（命令摘要 + diff + 影响范围）；  
  - 审计事件（谁触发、参数、结果）；  
  - 可选的组织策略（企业模式下审批流）。  

---

### Issue 5
- **Severity:** 🔵 Minor  
- **Location:** Memo 分类（extension）  
- **Description:** 归 extension 是合理的，但“基础设施级记忆能力”容易引发是否 core 的争议。  
- **Suggestion:**  
  给出 core 判定标准：**无它无法完成最小操作系统闭环**。  
  Memo 提升能力但非最小原语，因此应在 extension。这个标准写进 ADR 可终结争论。

---

### Issue 6
- **Severity:** 🔵 Minor  
- **Location:** 方案 A 描述  
- **Description:** 仅增加 `list_by_category()` 可能不够支持后续运营与调试。  
- **Suggestion:**  
  一次性补两个低成本接口：  
  - `get_tool_metadata(name)`（含 category/provider/version/capabilities）  
  - `list_categories()`（便于 UI/CLI 展示与诊断）

---

## 4) Architecture/Design Observations

1. **这是“语义分层”而不是“安全分层”**  
   你已经说清楚了，但建议在文档中反复强调，避免未来同事误把 category 当隔离边界。

2. **最关键的是“边界判定规则”而非“分类名字”**  
   当前名字好理解，但长期稳定性取决于判定规则是否可执行（是否可被 CI、注册器强制）。

3. **A 方案是对的，但要防“标签漂移”**  
   仅打标签容易变成注释式架构。需要配套最小治理（lint、启动校验、冲突策略、保留名）。

4. **何时升级到 B：以复杂度阈值驱动，而非时间驱动**  
   建议触发条件（满足任一）：  
   - 热插拔工具 > 30 且频繁变更；  
   - 生命周期管理逻辑在 ToolRegistry 中出现大量 if/branch；  
   - category 被用于隔离加载/卸载/故障恢复策略。  

---

## 5) Actionable Recommendations (Top 3)

1. **先补“分类契约”并做强约束（最高优先）**  
   - 明确 core/extension/skill 的判定规则、可替换规则、命名冲突规则；  
   - 在注册阶段 enforce（而不只是文档约定）。

2. **方案 A 落地时避免默认 core，改为显式分类 + CI 校验**  
   - 默认 uncategorized；  
   - 启动告警 + CI fail（新增工具未声明 category）。

3. **将 ReloadSkills 调整为 extension（或 control-plane 特类）并补控制面文档**  
   - 这样层次更一致，避免“被管理对象与管理工具同层”的概念混乱；  
   - 同时补“人机交互层安全控制”实现要点，承接 CoreBash 移除后的治理叙事。  

---

**结论**：提案整体质量高，尤其是“去伪安全 + 先轻量分层”的方向值得支持。若补齐分类契约与控制面边界，这次重构会从“可读性优化”升级为“长期可演进架构基线”。

---

## Review by `google-antigravity/gemini-3-pro-high`

Hello USER, I am **Google Antigravity**, an agentic coding AI. I have reviewed your proposal for the **Nimbus Agent OS Tools Classification Refactoring**.

Here is my structured architectural review.

---

# 1. Overall Assessment
**Score: 9/10** (Excellent strategic direction)

**Summary:** The proposal demonstrates high architectural maturity by aligning tool categorization with OS kernel design principles (Kernel vs. Services vs. Userland) and correctly identifying `CoreBash` as a security placebo. The shift from "security by restriction" to "security by transparency" is the correct philosophy for an autonomous coding agent.

---

# 2. Strengths

1.  **Philosophical Alignment (Security):** The realization that *Agent + Write + Bash = Root* is crucial. Removing `CoreBash` eliminates technical debt and user friction. It acknowledges that true security in this context comes from the `HitL` (Human-in-the-Loop) confirmation layer, not tool-level crippling.
2.  **The Three-Domain Model:** This mapping mirrors standard Operating System architecture perfectly:
    *   **Core** $\approx$ Kernel Syscalls (Atomic, Statless).
    *   **Extension** $\approx$ System Daemons/Services (Stateful, Replaceable).
    *   **Skill** $\approx$ Userland Applications (Dynamic, Ephemeral).
3.  **Pragmatic Migration:** "Scheme A" (Lightweight Tags) is an excellent MVP approach to validate the taxonomy without over-engineering a plugin system immediately.
4.  **Orchestration vs. Primitives:** Separating `Dispatch`/`Review` (Orchestration logic) from `Read`/`Write` (Atomic actions) clarifies the agent's cognitive load.

---

# 3. Issues Found

### 1. `ReloadSkills` Location Paradox
*   **Severity:** 🟡 Major
*   **Location:** Tool List / Three-Domain Model
*   **Description:** The proposal lists `ReloadSkills` under the **Skill** category.
*   **Why it's wrong:** If `ReloadSkills` is *inside* the Skill domain, and the operation implies "clearing/reloading the Skill domain," you risk a lifecycle paradox (cutting off the branch you are sitting on) or simply logical confusion.
*   **Suggestion:** `ReloadSkills` is a management utility. It belongs in **Extension**. It is a system service provided by the `SkillManager` to manipulate the userland (Skills). It should remain available even if 0 skills are loaded.

### 2. Dependency Direction & Visibility
*   **Severity:** 🔵 Minor
*   **Location:** Three-Domain Model definition
*   **Description:** The proposal defines categories but not dependency/visibility rules.
*   **Why it's wrong:** Can a **Skill** call an **Extension**? (e.g., A "ComplexResearch" skill calling "Memo" to save data). Can an **Extension** call a **Skill**?
*   **Suggestion:** Explicitly define the dependency hierarchy: `Skill -> Extension -> Core`. Skills should have visibility of Extensions and Core. Extensions should see Core. Core sees nothing.

### 3. Namespace Collision Strategy
*   **Severity:** 🔵 Minor
*   **Location:** Missing from proposal
*   **Description:** With dynamic "Skills" coming from user-defined `SKILL.md` or plugins, what happens if a user defines a skill named `write` or `bash`?
*   **Suggestion:** Implement a masking/shadowing policy. **Core** tools should be reserved keywords. If a Skill tries to register with the name `write`, the Registry should either reject it or prefix it (e.g., `skill_write`).

---

# 4. Architecture/Design Observations

### The "Memo" Question
You asked: *Does Memo belong in Core or Extension?*
**Verdict:** **Extension** is correct.
*   **Reasoning:** **Core** should be the *minimum viable set* to operate the computer. An agent can technically function without long-term memory (it just won't be very smart). Furthermore, `Memo` implies an implementation choice (Vector DB? JSON file? SQL?). Core tools (`read`, `write`, `bash`) are implementation-agnostic interfaces to the OS. `Memo` is an opinionated service.

### Scheme A vs Scheme B
You asked: *When to upgrade to B?*
**Observation:**
*   **Scheme A (Tags):** Good for metadata, filtering prompt displays, and grouping in logs.
*   **Scheme B (Split Registries):** Necessary for **Lifecycle Management**.
*   **Verdict:** If you plan to implement "Hot Reloading" where you wipe all skills and reload them from disk while the agent is running, **Scheme A is insufficient** (or requires messy list filtering). You should plan to move the `Skill` domain to a separate `SubRegistry` (Scheme B) relatively soon so you can do `skill_registry.clear()` without accidentally wiping `bash`.

### Removal of CoreBash
**Observation:** By removing the "Safety Sandbox" (CoreBash), you increase the burden on the **UI/Client**.
*   The UI must now clearly differentiate between a "Safe" tool (Read) and a "Dangerous" tool (Bash). Since the tool itself no longer enforces checks, the `ToolDefinition.dangerous` boolean becomes the single source of truth for the UI to trigger a "Confirm this action?" prompt. Ensure this flag is rigorously set on the Core Bash tool.

---

# 5. Actionable Recommendations

1.  **Move `ReloadSkills` to Extension:** It is a system service *managing* skills, not a skill itself. It must persist across skill reloads.
2.  **Adopt Scheme B (Split Registry) for Skills Only:** You don't need full separate registries for everything, but the **Skill** domain requires a separate lifecycle (Hot Swap). Implement a `CompositeRegistry` that holds a static `CoreRegistry` and a dynamic `SkillRegistry`.
3.  **Standardize Prompt Presentation:** Update the System Prompt generation logic to group tools by these categories.
    *   *Example:* "You have access to **System Primitives** (Read, Write...), **Services** (Memo, Verify...), and **Capabilities** (WebSearch...)." This helps the LLM understand the "cost" and "level" of the tool it is using.

**Final Verdict:** This is a solid, clean-up refactor that prepares Nimbus for serious agentic work. Proceed with the move of `ReloadSkills` and the removal of `CoreBash`.

---
