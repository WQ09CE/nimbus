# AI Review Committee: mmu-image-token-optimization-review

- **Date**: 2026-02-12 09:44:34
- **Focus**: code-quality
- **Reviewers**: 3
- **Total Time**: 71.8s

---

## Review by `anthropic/claude-opus-4-6`

# Code Quality Review: MMU Image Token Optimization

**Reviewer:** anthropic/claude-opus-4-6
**Focus:** Code Quality
**Date:** 2025-01-XX

---

## 1. Overall Assessment

**Score: 5/10** — Functional Phase 1–2 implementation with meaningful design deviations, missing Phase 3, zero test coverage for new logic, and several correctness risks in the fingerprinting and copy semantics.

---

## 2. Strengths

- **`token_estimate()` is clean and well-structured.** The Chinese character handling is a thoughtful detail, and the 1500-token estimate is documented with rationale. The function handles `str`, `list`, and fallback cases cleanly.

- **Budget approach in `_downgrade_seen_images` is arguably better than the design's "keep last occurrence" strategy.** It gives explicit control over total image token expenditure, which is the actual constraint that matters for context window management. The two-pass algorithm (scan backwards, then rebuild) is a reasonable pattern that avoids mutation of the input.

- **Placeholder text is informative.** `[📷 Image ({mime}) — Omitted to save tokens (duplicate or budget limit)]` gives the model (and debugging humans) useful context about what was removed and why.

- **Calling `_downgrade_seen_images` at the end of `assemble_context()`** is the right insertion point — it operates on the final message list before it leaves the MMU, ensuring all upstream logic sees original data.

---

## 3. Issues Found

### 🔴 Critical

#### C1: `_image_key` fingerprint is unreliable

**Location:** `MMU._image_key()`

```python
def _image_key(self, block: Dict[str, Any]) -> str:
    data = block.get("data", "")
    prefix = data[:64] if isinstance(data, str) else ""
    mime = block.get("mimeType", "")
    return f"{mime}:{prefix}"
```

**Problem:** 64 characters of base64 encodes only **48 bytes** of image data. This is typically within the file header region. Two different PNG images of the same dimensions, or two JPEG files from the same camera, can easily share the first 48 bytes (magic bytes + IHDR/SOI/EXIF headers). This produces **false positive duplicates**, silently dropping unique images.

Conversely, the *same* image re-encoded (e.g., different JPEG quality, or base64 with/without line breaks) would produce different prefixes — a **false negative**, defeating deduplication entirely.

**Suggestion:** Use a hash of the full data string:

```python
import hashlib

def _image_key(self, block: Dict[str, Any]) -> str:
    data = block.get("data", "")
    if isinstance(data, str) and data:
        digest = hashlib.sha256(data.encode("ascii", errors="replace")).hexdigest()[:16]
    else:
        digest = ""
    mime = block.get("mimeType", "")
    return f"{mime}:{digest}"
```

This is a one-time cost per image during context assembly — acceptable for correctness. If performance over very large base64 strings is a concern, hash the first **4096** characters instead (3072 bytes — well past any header region).

---

#### C2: No test coverage for any new logic

**Location:** Missing test file(s)

**Problem:** `token_estimate()` with image blocks, `_image_key()`, `_downgrade_seen_images()`, and the budget exhaustion path have **zero unit tests**. The existing 24 tests in `test_image_support.py` cover data pipeline only. The 10 failing tests in `test_v2_memory.py` suggest the test suite is already in a degraded state.

For logic that silently removes content from the context window, this is critical — regressions here are invisible (the model just gets worse answers with no error).

**Suggestion:** At minimum, add tests for:
1. `token_estimate` returns ~1500 per image block in list content
2. `_image_key` produces same key for identical images, different keys for different images
3. `_downgrade_seen_images` keeps newest N images within budget
4. `_downgrade_seen_images` deduplicates identical images (keeps latest only)
5. `_downgrade_seen_images` preserves non-image content blocks untouched
6. Budget boundary: exactly `max_image_tokens` edge case (6 images × 1500 = 9000 < 10000, 7 images × 1500 = 10500 > 10000)

---

### 🟡 Major

#### M1: `dict(msg)` shallow copy allows mutation of nested content

**Location:** `_downgrade_seen_images`, second pass

```python
new_msg = dict(msg)
new_msg["content"] = new_content
```

**Problem:** `dict(msg)` is a shallow copy. While `new_msg["content"]` is reassigned to a new list, any *non-replaced* blocks in `new_content` are still references to the original block dicts. If any downstream code mutates these blocks, it corrupts the original messages. More critically, if `msg` contains nested mutable values in keys *other* than `"content"` (e.g., `tool_calls`, metadata dicts), these are shared between old and new.

In the `changed=False` path, the original `msg` is appended directly — so the return list is a mix of original references and shallow copies. This inconsistency is a maintenance trap.

**Suggestion:** Use `copy.deepcopy(msg)` for changed messages, or document the mutation contract explicitly. Given this runs once per `assemble_context()` call and message lists are typically <100 items, deepcopy cost is negligible.

---

#### M2: Phase 3 is not implemented — `_format_messages()` will corrupt image data during compaction

**Location:** `src/nimbus/core/compaction.py`, `_format_messages()`

**Problem:** When compaction runs, `_format_messages()` calls `str()` on list-type content, producing Python repr output like `[{'type': 'image', 'data': 'base64...'}]`. This is:
1. **Not useful to the summarization model** — it sees raw dict repr
2. **Token-expensive** — the base64 data gets serialized as text
3. **Lossy** — the compacted summary loses semantic meaning of images

The code comment `# ⚠️ If content is a list (multimodal), Python str() produces garbage` shows the author *knows* this is broken but didn't implement the fix.

**Suggestion:** Implement Phase 3 as designed. At minimum, add a guard:

```python
if isinstance(content, list):
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "image":
                mime = block.get("mimeType", "image/unknown")
                parts.append(f"[📷 Image ({mime})]")
            elif "text" in block:
                parts.append(block["text"])
        elif isinstance(block, str):
            parts.append(block)
    content = "\n".join(parts)
```

---

#### M3: Budget logic silently drops unique images without any signal

**Location:** `_downgrade_seen_images`, first pass, the `else` branch

```python
if current_image_tokens + img_tokens <= self.config.max_image_tokens:
    keep_indices.add((i, j))
    seen_keys.add(key)
    current_image_tokens += img_tokens
else:
    seen_keys.add(key)  # Marked as seen but NOT kept
```

**Problem:** When a unique (never-before-seen) image exceeds the budget, it is added to `seen_keys` without being kept. This means if the same image appears *earlier* in the conversation (closer to the start), it's treated as a duplicate during the backward scan and also dropped. The user gets no logging, no metric, nothing — images just vanish. In a debugging scenario, this is very hard to trace.

**Suggestion:** Add `logger.debug` when images are dropped due to budget exhaustion vs. deduplication. Consider exposing a count or diagnostic method:

```python
logger.debug(
    "Image dropped (budget exceeded): msg=%d block=%d key=%s budget=%d/%d",
    i, j, key[:20], current_image_tokens, self.config.max_image_tokens
)
```

---

### 🔵 Minor

#### m1: Magic number 1500 duplicated across files

**Location:** `context.py` line with `total += 1500` and `mmu.py` line with `img_tokens = 1500`

**Problem:** The same magic number appears in two files with no shared constant. If the estimate changes, one file could be updated and not the other, causing inconsistency between token counting and budget enforcement.

**Suggestion:** Define `IMAGE_TOKEN_ESTIMATE = 1500` in a shared location (e.g., `MMUConfig` or a constants module) and import it in both places.

---

#### m2: `_image_key` doesn't handle URL-based images

**Location:** `MMU._image_key()`

**Problem:** The method assumes images have a `"data"` field (inline base64). If the codebase ever uses URL-referenced images (`"source": {"type": "url", "url": "https://..."}` in some APIs), the key degenerates to `"mime:"` for all URL images, treating them all as duplicates.

**Suggestion:** Add URL handling:

```python
url = block.get("source", {}).get("url", "")
if url:
    return f"url:{url}"
```

---

#### m3: `estimate_text` nested function is redefined on every call

**Location:** `Message.token_estimate()`

**Problem:** `estimate_text` is defined as a closure inside `token_estimate()`, so it's recreated on every invocation. While the performance impact is negligible, it would be cleaner as a module-level utility or `@staticmethod`, especially since it has no dependency on `self`.

---

#### m4: The backward scan doesn't consider block order within a message

**Location:** `_downgrade_seen_images`, inner loop

```python
for j in range(len(content) - 1, -1, -1):
```

**Problem:** The inner loop also scans blocks backward within each message. For a message with multiple images `[img_A, img_B]`, it processes `img_B` before `img_A`. If both are unique but only one fits the budget, `img_B` is kept and `img_A` is dropped. This is fine semantically (newer = later in the block list), but the intent isn't documented and could confuse maintainers.

---

## 4. Architecture/Design Observations

### Operating on `List[Dict]` vs `Message` objects

The `_downgrade_seen_images` method operates on serialized dicts (post-`to_dict()`). This is a pragmatic choice — it avoids coupling with the `Message` class — but it means the method is doing string-in-dict pattern matching (`block.get("type") == "image"`) without type safety. If the serialization format changes (e.g., the image block schema evolves), this code breaks silently. Consider adding a small helper like `is_image_block(block: dict) -> bool` to centralize this check.

### Design doc vs. implementation divergence

The budget approach is a **legitimate improvement** over the "keep last occurrence" design, but the divergence should be documented. Future readers will see the design doc and the code telling different stories. Either update the design doc or add a comment in `_downgrade_seen_images` explaining why the budget approach was chosen.

### Compaction is the real gap

Phase 3's absence is more than a missing feature — it's a **correctness bug in waiting**. When compaction fires on a conversation with images, the summarization model receives Python dict repr as "content." This will produce garbled summaries, potentially causing cascading quality degradation in long conversations. This should be prioritized above further optimization of Phases 1–2.

---

## 5. Actionable Recommendations (Prioritized)

### 1. 🔴 Fix `_image_key` to use content hashing (not prefix)
**Effort:** Small (5 lines changed)
**Impact:** Prevents false-positive deduplication that silently drops unique images. This is a data correctness issue that's extremely hard to debug in production.

### 2. 🔴 Add unit tests for all new image optimization logic
**Effort:** Medium (1–2 hours for ~8–10 focused tests)
**Impact:** The new code modifies message content in ways that are invisible to end users. Without tests, regressions are guaranteed as the codebase evolves. Also fix or remove the 10 failing legacy tests — a red test suite trains developers to ignore failures.

### 3. 🟡 Implement Phase 3 (`_format_messages` multimodal handling)
**Effort:** Small-medium (15–30 min for the guard, plus tests)
**Impact:** Prevents compaction from injecting garbage into conversation summaries. This is a latent bug that will manifest in any long conversation containing images.

---

*Supplementary: Extract `IMAGE_TOKEN_ESTIMATE` to a constant, add debug logging for image drops, and update the design doc to reflect the budget approach. These are low-effort, high-maintainability wins.*

---

## Review by `openai-codex/gpt-5.3-codex`

## 1) Overall Assessment

**Score: 6.5/10** — Solid Phase 1/2 progress with pragmatic token-saving logic, but Phase 3 is missing, core behavior is untested, and some implementation details introduce correctness/maintainability risk.

---

## 2) Strengths

1. **Phase 1 is implemented in the right place (`Message.token_estimate`)**  
   - Multimodal-aware counting in `token_estimate()` is the correct architectural layer for rough budgeting.
   - Adding image token accounting prevents severe underestimation regressions.

2. **Phase 2 has practical optimization behavior**
   - Backward scan (newest-first) in `_downgrade_seen_images()` aligns with conversational relevance.
   - Two-pass approach (select then rebuild) is readable and avoids in-loop mutation complexity.
   - Placeholder insertion preserves conversational continuity rather than silently dropping content.

3. **Reasonable operational guardrail added**
   - `max_image_tokens` in config gives a tunable safety valve and avoids runaway multimodal context growth.

---

## 3) Issues Found

### Issue A
- **Severity:** 🔴 Critical  
- **Location:** `src/nimbus/core/compaction.py::_format_messages()` (Phase 3 not implemented)  
- **Description:**  
  `_format_messages()` still treats `content` as scalar/string. For list-form multimodal content, output becomes Python `str(list/dict)` noise and does not preserve semantic structure (text + image markers). This directly breaks the stated design phase and can degrade compaction quality significantly.
- **Suggestion:**  
  Implement explicit list-content formatting:
  - For text blocks: append plain text.
  - For image blocks: append deterministic marker (e.g. `[Image: mime=image/png]`).
  - For unknown block types: append safe marker.
  - Reuse a helper shared with MMU/message formatting to avoid drift.

---

### Issue B
- **Severity:** 🟡 Major  
- **Location:** `mmu.py::_image_key()`  
- **Description:**  
  Fingerprint = `mime + first 64 chars of data` is collision-prone in realistic workflows:
  - Same prefix for different images (especially if preprocessing/headers/common patterns align).
  - Truncated base64 prefix is a weak identity proxy.
  - If URLs or non-base64 forms appear, behavior may be inconsistent.
- **Suggestion:**  
  Use stable hashing of full canonical payload when available (e.g., SHA-256 of decoded bytes or full data string).  
  Fallback hierarchy:
  1. explicit `image_id`/`file_id` if present,
  2. hash of full data/URL string,
  3. current prefix strategy only as last resort.

---

### Issue C
- **Severity:** 🟡 Major  
- **Location:** `mmu.py::_downgrade_seen_images()` behavior vs design  
- **Description:**  
  Implementation diverges from spec (“keep last occurrence of each image”) to a budget-based retention policy. This may be a good evolution, but it changes semantics and expected outputs. Without explicit design update, this is a spec/implementation mismatch that will confuse maintenance and testing.
- **Suggestion:**  
  Either:
  - Update design doc to formally adopt budget-aware strategy, including deterministic tie-break rules, or
  - Add mode switch (`dedupe_only` vs `dedupe_plus_budget`) and default per spec.

---

### Issue D
- **Severity:** 🟡 Major  
- **Location:** Test coverage (missing for new logic)  
- **Description:**  
  No direct tests for:
  - `Message.token_estimate()` with mixed list blocks/images,
  - `_image_key()` stability/collision behavior,
  - `_downgrade_seen_images()` dedupe and budget edge cases,
  - placeholder correctness and idempotence.  
  This is high-risk because logic is heuristic and branch-heavy.
- **Suggestion:**  
  Add targeted unit tests with table-driven cases:
  - duplicate images across messages,
  - budget boundary (exact fit, overflow by one),
  - mixed block types,
  - non-list content passthrough,
  - deterministic output order and placeholder text.

---

### Issue E
- **Severity:** 🔵 Minor  
- **Location:** `mmu.py::_downgrade_seen_images()` second pass copy behavior (`dict(msg)`)  
- **Description:**  
  `dict(msg)` is shallow copy. In current flow you replace `content` entirely when changed, so this is mostly safe, but nested mutable fields (e.g., metadata/tool_calls dicts) remain aliased.
- **Suggestion:**  
  Keep as-is for performance unless mutation downstream is expected; otherwise use `copy.deepcopy` only when changed + document rationale. Prefer immutability contract in this layer.

---

### Issue F
- **Severity:** 🔵 Minor  
- **Location:** 1500 fixed image token estimate in both `token_estimate()` and MMU budget logic  
- **Description:**  
  Single constant ignores image size/resolution and can under/over-trim. Acceptable as a heuristic, but currently duplicated magic number in multiple places.
- **Suggestion:**  
  Centralize as config constant (e.g., `estimated_tokens_per_image`) and optionally scale by metadata (`width*height` buckets) if available.

---

### Issue G
- **Severity:** 🔵 Minor  
- **Location:** Layering: `_downgrade_seen_images(List[Dict])` post-serialization  
- **Description:**  
  Operating on dicts is practical at assembly boundary, but weakens type safety and makes logic schema-fragile.
- **Suggestion:**  
  Either enforce a typed schema helper for content blocks or run downgrade on `Message` model before serialization. If staying with dicts, add strict validators/asserts.

---

## 4) Architecture/Design Observations

- **Good direction:** Token-aware multimodal memory is implemented where it matters (estimation + context assembly).
- **Main architectural gap:** Compaction path is inconsistent with memory path; multimodal awareness exists in MMU but not in compaction formatter, so system behavior is fragmented.
- **Spec governance issue:** The Phase 2 change is arguably better (budgeted dedupe), but this should be codified in design and tests. Right now it looks like undocumented product behavior drift.
- **Heuristic consistency:** Image token assumptions should be centralized; duplicated constants across modules will drift over time.

---

## 5) Actionable Recommendations (Top 3, prioritized)

1. **Implement Phase 3 immediately (`_format_messages` multimodal handling)**  
   This is the biggest correctness gap and directly affects downstream compaction quality.

2. **Add focused unit tests for Phase 1/2 logic before further tuning**  
   Especially boundary and determinism tests for `_downgrade_seen_images` + mixed-content `token_estimate`. This will stabilize future optimization changes.

3. **Harden and formalize image identity + policy**  
   Replace weak `_image_key` prefix fingerprint with robust hashing/ID strategy, and update design doc to explicitly define budget-aware retention semantics (or provide configurable modes).

---

## Review by `google-antigravity/gemini-3-pro-high`

# Code Review: MMU Image Token Optimization

**Reviewer:** google-antigravity/gemini-3-pro-high
**Date:** 2023-10-27
**Status:** 🔴 Changes Required

## 1. Overall Assessment
**Score:** 6/10 — **"Good Logic, Incomplete Execution"**

The core logic for token estimation and image downgrading (Phases 1 & 2) shows strong awareness of LLM context window constraints. The introduction of a "Token Budget" in Phase 2 is actually a superior architectural choice compared to the original design's simple deduplication. However, the omission of Phase 3 (Compaction) creates a functional regression for multimodal logging, and the image fingerprinting mechanism is mathematically risky. The complete lack of unit tests for complex logic is a blocking issue.

## 2. Strengths
*   **Budget-Aware Downgrade (Phase 2):** Implementing `max_image_tokens` (10,000) is a significant improvement over the original design. It effectively creates a "sliding window" specifically for images, ensuring the most recent visual context is preserved while aggressively pruning older heavy assets.
*   **Non-Destructive Pruning:** Replacing images with text placeholders (`[📷 Image ... Omitted]`) is excellent UX. It prevents the model from hallucinating that an image was never sent and maintains the conversational flow.
*   **Conservative Estimation:** The logic in `token_estimate` handles the mixed content (text + image) correctly and applies a safe upper-bound estimate (1500) for images, which reduces the risk of `ContextWindowExceeded` errors.

## 3. Issues Found

### 🔴 Critical Severity

**1. Phase 3 Implementation Missing (Functional Gap)**
*   **Location:** `src/nimbus/core/compaction.py` - `_format_messages`
*   **Description:** The design document Phase 3 explicitly required compaction updates. Currently, `_format_messages` performs a simple string concatenation. If `content` is a `list` (which it now is for multimodal messages), Python's default `str(list)` representation will be injected into the prompt/logs. This leaks raw JSON/Dict structures into the LLM context or logs, which is token-inefficient and confusing for the model during compaction/summarization.
*   **Suggestion:** Implement the logic to iterate through list content, extract text `["text"]`, and represent images with a tag like `[Image]`.

**2. High Collision Risk in Image Fingerprinting**
*   **Location:** `src/nimbus/core/memory/mmu.py` - `_image_key`
*   **Description:** The fingerprint relies on `data[:64]`. Base64 strings often start with identical headers (e.g., standard PNG file signatures) or extensive metadata headers. 64 characters represents only ~48 bytes of data. Two different images with the same dimensions/headers could easily share the first 48 bytes.
*   **Suggestion:** Use a hash of the full data string.
    ```python
    import hashlib
    # In _image_key:
    return hashlib.md5(block.get("data", "").encode()).hexdigest()
    ```

### 🟡 Major Severity

**3. Zero Test Coverage for New Logic**
*   **Location:** `tests/`
*   **Description:** The review request admits "NO tests for: `token_estimate()` with image blocks, `_downgrade_seen_images()`". The downgrade logic contains complex indexing (backwards pass, tuple tracking) which is prone to off-by-one errors.
*   **Suggestion:** Add a new test file `tests/test_mmu_optimization.py` verifying:
    *   Duplicate image removal.
    *   Budget enforcement (adding images until > 10000, ensuring oldest drops).
    *   Placeholder text formatting.

### 🔵 Minor Severity

**4. Hardcoded Magic Numbers**
*   **Location:** `src/nimbus/core/memory/context.py` & `mmu.py`
*   **Description:** The value `1500` is repeated in two files (`context.py` and `mmu.py`). If the token estimation strategy changes (e.g., GPT-4o uses different tiling math), you have to update it in multiple places.
*   **Suggestion:** Define `IMAGE_TOKEN_ESTIMATE = 1500` in a shared config or constant file (e.g., `nimbus.core.constants`).

## 4. Architecture/Design Observations

*   **Deviation from Design (Budgeting):** The deviation to use `max_image_tokens` instead of just "keep last occurrence" is **approved**. It makes the system robust against a user sending 20 *unique* images, which would otherwise blow the context window even with deduplication.
*   **Data Structure Layer:** The MMU operates on `List[Dict]` (serialized data) rather than `Message` objects. While this works, it creates a "stringly typed" interface where dictionary keys (`type`, `text`, `data`) must be perfectly spelled. A `ContentBlock` dataclass/Pydantic model would be safer, but given the current codebase state, refactoring this is out of scope for this PR.

## 5. Answers to Key Questions

1.  **Is `data[:64]` reliable?** **No.** It is highly risky for collision, especially with similar screenshots or standardized file headers. Switch to hashing.
2.  **Is the budget approach better?** **Yes.** It provides deterministic control over context usage, whereas simple deduplication fails against unique image flooding.
3.  **Is Phase 3 a gap?** **Yes, Critical.** It must be fixed before merge, or logs/summaries will be corrupted.
4.  **No unit tests risk?** **High.** The logic involves array manipulation and filtering; manual testing is insufficient.
5.  **Operating on `List[Dict]`?** It's acceptable for this iteration but fragile.
6.  **Shallow copy `dict(msg)`?** Safe in this context because `content` (the only mutable part changing) is being completely replaced by a new list `new_content`.
7.  **1500 token estimate?** Acceptable tradeoff. Precise calculation requires complex logic mimicking specific provider tiling (OpenAI vs Anthropic vs Gemini). A conservative average is fine for a general "Agent" loop.

## 6. Actionable Recommendations (Prioritized)

1.  **Implement Phase 3:** Update `src/nimbus/core/compaction.py` to handle list-based `content` to prevent garbage strings in logs/context.
2.  **Fix Fingerprinting:** Change `_image_key` to use `hashlib.md5` (or `sha256`) of the image data string to ensure uniqueness.
3.  **Add Unit Tests:** Create `tests/test_mmu_optimization.py` covering the downgrade logic and token estimation.
4.  **Refactor Constants:** Extract `1500` to a constant `DEFAULT_IMAGE_TOKEN_COST`.

---
