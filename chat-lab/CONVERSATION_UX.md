# Conversation UX upgrade — 2026-09-11 09:22 Beijing

Dennis confirmed that Telegram task creation, immediate execution and report delivery
worked. He reported three experience failures: background work blocked conversation,
raw state/attempt decorations were visible, and follow-up answers lacked the report's
research context. This upgrade addresses those without rebuilding or rerunning his job.

## Deployed changes

- Worker A is reserved for foreground conversation (`NIMBUS_WORKER_LANE=chat`).
  Workers B/C provide two background slots (`jobs`). A long background search no
  longer makes an ordinary progress question fail admission.
- Each schedule has its own persisted session/workspace lane. Different background
  tasks may run concurrently; repetitions of the same task serialize. Foreground
  messages buffer up to eight and are answered in order, not silently dropped.
  This is bounded concurrency, not unlimited parallelism or token-level steering.
- No normal queued acknowledgement, success prefix or internal attempt/run number.
  Explicitly requested task IDs remain available. Status/failure/cancel messages are
  human-readable. Telegram typing feedback is ephemeral and rate-limited; background
  drafts do not intrude into foreground conversation.
- Sent scheduled results enter the user's conversation context. Unsent future results
  do not. `/new` resets that automatic context without deleting jobs or saved memory.
- `activity` inspects actual background progress, results and stored research receipts;
  it can cancel a particular execution while retaining the daily schedule. Unattached
  queued runs are cancelled directly, without ever starting a worker.
- Future searches persist bounded query/source/usage/text receipts under the attempt's
  identity and commit fence. Two authentic historical search receipts for the existing
  report were recovered from protected original tool logs—no new searches were made.
  These are bounded excerpts, not a complete raw X corpus or exhaustive-search proof.
- Conversation instructions encourage concise first-person accountability, ordinary
  Chinese and plain-text presentation, not repeated operational disclaimers or
  third-person deflection about the bot's own output. No claim of being human.

## Preservation and verification

`upgrade_conversation_local.py --apply` stopped admission and waited for existing work
before owner migration. It made a private PG dump and verified archive readability;
it did not restore over production. The existing schedule ID, enabled flag, generation,
next delivery and instruction hash were checked unchanged. Identity/cursor were also
checked unchanged at migration. No task, digest run or model call was created by upgrade.

Evidence (private/ignored `.artifacts/`):

- `conversation-unit.xml`: **75 passing tests**, including concurrent lanes, FIFO
  foreground claims, separate workspaces/shared memory, natural receipts, sent-only
  report context, `/new`, progress/receipt inspection and queued-run cancellation.
- `conversation-smoke/verification.json`: real Astra inspected stored receipts and
  answered a synthetic “why only two?” naturally, with no debug prefixes, deflection,
  new search, rerun or schedule activation. **Synthetic research / isolated PG / no Telegram.**
- `conversation-review.txt`: independent Astra review; its unattached-run cancellation
  finding was fixed and regression-tested before deployment.
- `conversation-deployment.json`: one schedule preserved, two retained receipts
  recovered, actual worker environment verified as A=chat/B=jobs/C=jobs, services active.

User-confirmed original creation/manual report receipt is not proof of the new UX or
future scheduled delivery. Those need user observation. Do not rerun the one-time
upgrade against an already migrated database.

## Try through Telegram

No recreation or reauthorization of the existing daily task is needed. For context:

> 刚才那份日报为什么只有两条？查一下当时的检索记录，直接说明原因，不要重新搜索或重跑。

For an optional user-initiated concurrency check:

> 把现有的日报任务立即运行一次，在后台处理，不要新建定时任务。

Then send a normal question or ask progress while it runs. Ordinary foreground
messages still serialize if the foreground request itself is long; `/status` and
`/cancel` remain direct controls.

## Maintenance / rollback

Stop gateway, scheduler and **all three workers** before schema maintenance. Preserve
`conversation_schema.sql` and the lane-aware code together: reverting old Agent
workspace SQL against the new primary key is not a valid rollback. For a conservative
single-worker configuration, disable B/C and set A's lane to `all` while retaining the
new schema/code. Do not restore an old dump over subsequent user messages/jobs.

Keep the earlier isolation, secret handling, no ambiguous-send replay and machine
awake/online requirements. This upgrade does not implement automatic off-host backups,
retention, disk alerts, weekly cron, desktop access or an unrestricted public connector.
