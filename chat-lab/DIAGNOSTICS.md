# Local plaintext failure diagnostics

Explicitly requested by the operator on 2026-09-11 after a first-model failure could only be
localized, not explained, because the original provider/bridge error was discarded.
This changes diagnostics, not retry policy, model authorization, tools, or schedules.

## What is retained

Each failing Pi request gets an opaque UUID and one local JSON record under:

```
~/.local/share/nimbus-chat-lab/worker-{a,b,c}/attempts/<attempt-id>/diagnostics/<request-id>.json
```

- Original child stderr, raw bridge stdout (including original error details), child exit code.
- Python exception type/message/stack, stage and timestamp.
- Extension exceptions: original name/message/stack/code/status and up to four cause levels.
- Non-success model results: SDK-provided errorMessage, stopReason and returned response.
- xAI search errors: HTTP status and original response body, including malformed JSON or
  incomplete responses. No response body is silently discarded on non-200 status anymore.
- Non-bridge AgentEngine errors and non-OK native results also get a local diagnostic.
- Successful requests with stderr warnings retain the warning/exit metadata, but not stdout.
  Quiet successful requests do not create a raw diagnostic record.

These are **plaintext, not redacted**. Error messages, bodies, stacks and stderr can contain
private information returned by upstream libraries. They must not be shared wholesale.
We do not collect environments, OAuth files, authentication/request headers, full request
payloads, or traceback locals. SDKs sometimes return only an error message: a provider's
internal stack or wire status that the SDK never exposes cannot be reconstructed.

## Boundaries and limits

- Directory 0700, files 0600, owned local paths, exclusive-create/no-follow file writes.
- Raw diagnostics are outside Git and outside the gVisor workspace; they are not put in
  Telegram messages, model/tool results, PG progress/research text, or operator report bodies.
- Only a safe failure stage, request UUID and `diagnostic_saved` boolean enter telemetry.
  AgentOS/AgentEngine retain this same correlation instead of replacing it with a fresh
  generic RuntimeError. The user-facing failure notification remains concise.
- Stdout capture retains up to the existing 3,000,000-byte protocol bound. Stderr retains
  its first 256 KiB while continuously draining the pipe to prevent backpressure deadlocks;
  total bytes and truncation are recorded. A request keeps the existing 190-second bound.
- A JSON record is limited to 4 MiB. Pathological oversized serialization produces valid
  JSON with `record_truncated`, original size and a bounded JSON-text prefix. At most 16
  records per attempt are written. Bounds are not redaction; truncation is explicit.
- Failed writes do not turn successful calls into failures or retries. A failed bridge call
  reports whether its diagnostic was actually saved. Hard process death/power loss can still
  occur before the final diagnostic is written. This is not a crash-consistent checkpoint.
- No global retention/automatic deletion is added here. Diagnostics grow with failed tasks;
  disk monitoring and operator-controlled retention remain operations work.

## Failure stages

`request_encode`, `spawn`, `child_io`, `child_exit`, `missing_result`, `duplicate_result`,
`provider_error`, `response_validation`, `timeout`, `cancelled`, and `runtime` distinguish
parent/child protocol failures from native result failures. The local original extension
record further distinguishes model call/response and search HTTP/body/response stages.

Use `uv run nimbus-chat-lab ops --local report` for safe metadata. Its model/runtime finish
receipts can share a failure request UUID; that is one failure observed at two levels,
not two provider requests. Inspect the corresponding private JSON locally when authorized.
Do not paste entire records into issue trackers or Telegram.

## Native terminal projection fix

The old RuntimeLoop unconditionally saved `completed` at its ordinary final return, even
when the native result was ERROR. The result now determines snapshot/turn-end projection:
OK → completed/completed; CANCELLED → suspended/aborted; PAUSED → paused/paused;
other non-OK or missing results → error/error. Failed context/budget compaction exits also
persist an error snapshot. Explicit follow-up turns retain their own distinct end reasons.
No log format/contract bump, historical log rewrite, PG terminal rewrite or new replay path
is introduced. The earlier production turn was correctly failed in PG; its old native
completed record remains historical evidence, not retroactively corrected data.

## Verification scope

Regression tests use real AgentOS/Worker/isolated PG with scripted adapters and real child
processes, plus the actual TypeScript input handler with scripted registry/fetch responses.
They exercise detailed errors/causes/HTTP bodies, private storage and bounds, child exit /
protocol validation, stderr flood/drain, cancellation/reaping, cross-layer correlation,
no raw error leakage into PG/outbox, and native result/snapshot/log agreement.
These tests do not recreate the earlier upstream failure, prove its root cause, or make
paid model calls. Deployment and actual Pi-loader smoke evidence are recorded separately.
