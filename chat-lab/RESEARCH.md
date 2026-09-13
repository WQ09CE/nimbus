# Public research execution contract

## Current update: exact windows and task quality (2026-09-13 14:38 Shanghai)

Limited-trial deployment: `clock.search_windows.last_24h/last_48h` provides fixed timestamp windows; `search.x_window` computes covering UTC dates and returns `search_window`. The former claim that raw `to_date` included the entire end day was wrong for the observed case. A matched known-post probe returned no post with the old end date and the post with the next UTC date. This is case evidence, not proof of universal provider semantics or complete recall.

AI task generation 7→8 keeps an AI-wide important-change lane alongside Agent products, research/reliability and practical discoveries. Exact windows use the new interface; targeted verification is limited to one complex event or at most two simple original-post checks per request, not three unrelated full investigations. The normal task retains a maximum of seven searches and the native 8/4 per-turn contract; no new executor/provider was added. The 08:00 time and five-minute lead are unchanged.

A preceding cold-start full policy experiment naturally recovered the known missing article, but still produced only three items, with six of seven searches completing, one bridge `missing_result` failure, and a 459s runtime. It is not a successful breadth/punctuality benchmark. The final exact-window native model/tool smoke verified computed dates through a real Grok request. Final regressions: 173 chat, 78 health, 551 core passed/3 skipped. Maintenance drained all lanes, updated the two existing task policies atomically, restarted application workers and the health gateway but not PG, preserved scopes/time/lead/cursor and created no test run or Telegram delivery. Evidence: `.artifacts/task-enhancement-v3/deployment.json`; real report bodies remain private. Future editorial quality and punctuality remain separate acceptance.

Deployed 2026-09-12 19:36 Asia/Shanghai. This repairs the execution/tool-contract failures observed in the AI digest, not an assertion that all future reports are editorially satisfactory.

## Top/Latest production update (2026-09-12 22:09 Shanghai)

After explicit user authorization, deployed bounded Responses SSE parsing (`search_stream.ts`) and safe `custom_tool_call.input` projection. Search results expose `search_observation`: completed keyword modes, up to eight bounded call summaries, missing/truncated trace, and explicit false flags for independently verified engagement filtering/views ranking. No reasoning/encrypted content is forwarded. Existing private bounded failure diagnostics remain; stream EOF without a terminal, duplicate terminals, malformed data and oversized bodies fail rather than inventing completion. JSON responses remain a compatible fallback with honest missing-observation status.

Public research receipts persist only safe observed mode summaries, visible through activity. Query intent is not execution proof; the system prompt tells the controller to check actual observation and not automatically retry merely to obtain a matching mode.

AI schedule policy separately changed generation 6→7: four concurrent discovery requests, product/platform Top with a 48h auxiliary window; infrastructure Latest, safety/reliability Top, and developer practice Latest over the 24h main window. Original event-time/continuing-story rules still apply. No default engagement threshold, no pure-views/global ranking claim; up to three additional supplement/verification requests, seven admitted requests for this task. Generic native search still has its existing 8/4 per-turn limits. This is a normal task policy, not a digest executor.

Acceptance: 137 chat tests and 72 health tests passed; full deployed chat tests and lint passed. An actual isolated Astra→Grok two-request run took about116s and recorded both Top and Latest in successful PG research receipts; mock outbox only, no Telegram. This checks mode observability, not full new-policy editorial recall or next-day punctuality. The prior broader event-policy comparison did not establish superiority and is not relabeled a successful quality benchmark.

Maintenance drained all application work, promoted five source/two test files, verified worker health-path isolation, and applied the guarded policy transaction. PG and health gateway PIDs unchanged. AI 08:00, five-minute lead and next slot 2026-09-13 00:00 UTC preserved; other schedules, identity and maintenance cursor unchanged. No new production run or test delivery. Evidence: `.artifacts/top-latest-production/{deployment,model-smoke,schedule-policy-update}.json`.

## What changed in the earlier execution repair

- Replaced whole-tool serialization with a short, sticky data-scope admission gate. Public requests can overlap; a health read and a public export still cannot both be authorized in the same turn. Existing workspace transaction serialization remains intact.
- Search permits **8 admitted requests per turn**, including failed/cancelled requests; **4 in flight per turn**. Saturation returns `search_busy` immediately, without a provider call or request charge. There is no provider-I/O queue hidden inside the native timeout. These are not global limits across workers, nor a hard bound on xAI's nested tool usage/cost.
- Native tool deadline remains 200s; bridge maximum 190s; HTTP maximum 175s. The actual worker run timeout is passed to the engine. Search HTTP time is shortened as the turn deadline approaches, reserving 195s plus overhead for final model work. This is a resource bound, not a guarantee of timely/complete delivery.
- `clock` and search results expose actual request usage, availability, concurrency and remaining-time information. For parallel receipts use the largest `requests_used`, or refresh with `clock`; do not sum xAI server-side tool counts as if they were Nimbus requests.
- Local budget/deadline/busy rejection, HTTP error, HTTP timeout, bridge timeout, incomplete response and no-search response have distinct safe outcomes. Wrapper errors now use the Gate's split-result dictionary contract, not a nested `ToolResult` object erroneously wrapped in OK.
- A 429 honors bounded Retry-After within the turn. Switching X to web cannot bypass this cooldown: both use the same xAI provider. This is **not** a durable/global provider cooldown. Already admitted remote requests may continue.

## Search interface

Existing `search(query, source)` remains valid. Optional fields:

- `mode: discover | verify | research` (default research).
- `x_filters`: raw `from_date`/`to_date` in YYYY-MM-DD, passed through without an inclusive-whole-day promise; `allowed_x_handles` **or** `excluded_x_handles`, 1–20 handles without `@`.
- `x_window`: `{start,end}` timezone-aware ISO timestamps, `[start,end)`, up to180 days. Start is floored to a UTC day; a non-midnight end is ceiled to the next UTC day. Date fields in `x_filters` cannot be combined with `x_window`; handle filters can. Invalid combinations are not dispatched and do not spend budget.

Filters are real xAI X-tool parameters, not only prose. Prefer the ready-to-use windows from `clock`; the runtime attaches the calendar envelope to the query and exposes it in the receipt. Calendar bounds remain a superset, not independently verified second-level filtering: check source timestamps before claiming news eligibility. X filters/windows cannot be used with web-only search. Do not constrain older original verification documents to the news window.

Discovery asks for bounded candidates; verification asks for targeted claims/quotes/sources. Modes have bounded model turns/output, but a model turn is not necessarily one server-side search invocation.

The response includes text, citations, candidate metadata, provider tool counts, truncation and `quality`. Candidate facts remain provider claims, not independently verified ground truth. Uncited but syntactically valid leads remain available with `citation_bound:false` and `uncited_lead_requires_lookup`; they cannot be treated as verified posts. Missing author/date/excerpt and incomplete structure yield `partial`, not fabricated values. JSON fences with explanatory prose are supported. Intermediate progress messages are not concatenated into the final answer. Overall projected JSON is bounded to 24,000 UTF-8 bytes, with explicit truncation.

## Evidence and privacy

`search_attempt` events record bounded safe admission/completion/error/rejection/cancellation metadata. Activity exposes the most recent outcome for up to 12 attempts per public background run, alongside successful research receipts. An admitted bridge call is **not proof HTTP was sent**; cancellation has unknown remote effects. Missing final evidence is not proof of liveness. Event persistence is bounded/best-effort and never authorizes retries or changes a completed external outcome.

Raw provider error details remain in the existing private diagnostics, not Telegram/model-visible errors. Health-scoped activity/history remains masked; the research controller also rejects private scopes before query receipt/provider access. This is enforcement of known health paths, not a universal classifier for arbitrary private text. Do not roll back to code that ignores health scopes after private reports exist.

## Acceptance

- 135 chat-lab tests; 551 core tests passed / 3 skipped; 72 health-lab tests passed.
- Actual native runtime regressions: concurrent service work receives its deadline, ninth admitted request is rejected with a real ERROR, overflow is not sent/charged, cancellation does not retry, deadline reserves final work, safe 500/timeout/429 behavior, real TS projection/filter validation, both health/search admission orders, and private activity suppression.
- One actual Astra→Grok run against isolated PG/mock outbox used the same 24h window as the afternoon failed report: seven successful searches, three-topic limited brief, no Telegram delivery. Took 681s under the **unchanged serial task instructions**. Queries were not byte-identical and the live index could have changed; this is not a controlled causal A/B or full editorial verification.
- A subsequent actual Astra→Grok two-query parallel check validated provider date/account filters and structured candidate projection. Both completed in about 104s of tool time. Six candidate leads were projected; four had matching provider citations. Uncited leads remained explicitly partial.
- Maintenance stopped ingress, drained all work, promoted tested code and restarted A/B/C. PG and health gateway were not restarted; schedules/generations/next_run, allowlist and maintenance cursor were preserved. No extra Telegram test message.

Ignored local evidence: `.artifacts/research-incident-20260912/`, `.artifacts/research-fix/`, `.artifacts/research-fix-v2/`. Full public-conversation/test reports stay in the private local investigation directory, not Git.

## Still pending

After the code deployment, the user separately authorized updating the AI task: up to four independent discovery calls in one batch, then up to three independent targeted verification/supplement calls. The obsolete serial/workaround text was replaced through an explicit operator transaction, generation 5→6, with no active work interrupted or new run/message created. The 08:00 time, next delivery (2026-09-13 00:00 UTC), five-minute lead and all other schedules were preserved. Evidence: `schedule-policy-{intent,update}.json` under the v2 evidence directory. The 681s full test used the earlier serial policy; it is not the runtime of this new workflow. Future actual delivery timing and editorial quality remain unverified.

A separately authorized production rerun then delivered a five-item limited brief to Telegram: four discovery calls overlapped and completed in about 80s; six of seven total requests completed, one product supplement hit a real 175s upstream timeout; end-to-end execution took 367s. The schedule stayed unchanged. This still exceeds the five-minute preparation lead. The user identified a missing major event (OpenAI Agents API), so editorial recall has **not** passed acceptance. Evidence: `production-rerun-{admitted,result}.json` and `recall-acceptance.json` in the v2 evidence directory.

[Research quality design](RESEARCH_QUALITY_DESIGN.md) studies event-level discovery, scoped evidence reuse and recall evaluation while keeping xAI's built-in X Search. It is a proposal, not deployed behavior.

Public cross-turn evidence caching, deterministic editorial acceptance, broad recall benchmarks, global provider cooldown/cost governance and future real scheduled-delivery validation remain separate work. The bot has better execution evidence now, but a prompt alone does not prevent every unsupported claim or false retraction about earlier research.
