-- Opt-in Agent mode, additive to S1. Run as dedicated schema owner.
CREATE TABLE IF NOT EXISTS agent_memory (
 bot_id bigint NOT NULL,user_id bigint NOT NULL,chat_id bigint NOT NULL,
 key text NOT NULL CHECK(length(key) BETWEEN 1 AND 64), value text NOT NULL CHECK(length(value)<=8000),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(bot_id,user_id,chat_id,key)
);
CREATE TABLE IF NOT EXISTS agent_workspaces (
 bot_id bigint NOT NULL,user_id bigint NOT NULL,chat_id bigint NOT NULL,
 archive bytea NOT NULL CHECK(octet_length(archive)<=16777216),
 PRIMARY KEY(bot_id,user_id,chat_id)
);
CREATE TABLE IF NOT EXISTS schedules (
 id uuid PRIMARY KEY, bot_id bigint NOT NULL REFERENCES bots(id), user_id bigint NOT NULL,chat_id bigint NOT NULL,
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 100), instructions text NOT NULL CHECK(length(instructions) BETWEEN 1 AND 6000),
 timezone text NOT NULL, hour integer NOT NULL CHECK(hour BETWEEN 0 AND 23), minute integer NOT NULL CHECK(minute BETWEEN 0 AND 59),
 lead_minutes integer NOT NULL DEFAULT 5 CHECK(lead_minutes BETWEEN 0 AND 30),
 enabled boolean NOT NULL, generation bigint NOT NULL DEFAULT 1,next_run timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(bot_id,user_id,chat_id,name), CHECK(user_id=chat_id AND user_id>0)
);
CREATE TABLE IF NOT EXISTS schedule_runs (
 id uuid PRIMARY KEY, schedule_id uuid NOT NULL REFERENCES schedules(id), generation bigint NOT NULL,
 slot timestamptz NOT NULL, manual_key uuid, turn_id uuid UNIQUE REFERENCES turns(id),
 state text NOT NULL CHECK(state IN ('queued','attached','published','cancelled','expired')),
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT schedule_generation_slot UNIQUE(schedule_id,generation,slot), UNIQUE(schedule_id,manual_key)
);
CREATE INDEX IF NOT EXISTS schedule_pending ON schedule_runs(created_at) WHERE state IN ('queued','attached');
ALTER TABLE schedule_runs DROP CONSTRAINT IF EXISTS schedule_runs_schedule_id_slot_key;
CREATE UNIQUE INDEX IF NOT EXISTS schedule_generation_slot ON schedule_runs(schedule_id,generation,slot);
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS user_id bigint;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS schedule_run_id uuid REFERENCES schedule_runs(id);
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS schedule_generation bigint;
UPDATE outbox o SET user_id=j.user_id,schedule_run_id=r.id,schedule_generation=r.generation
 FROM schedule_runs r JOIN schedules j ON j.id=r.schedule_id
 WHERE o.schedule_run_id IS NULL AND o.bot_id=j.bot_id AND o.chat_id=j.chat_id
   AND o.dedupe_key LIKE 'scheduled:'||r.id::text||':%';

-- Evaluated at request/mutation/delivery admission, not only by periodic cleanup.
CREATE OR REPLACE VIEW live_turn_authority AS
 SELECT t.id FROM turns t JOIN sessions s ON s.id=t.session_id
 WHERE EXISTS (SELECT 1 FROM allowlist a WHERE a.bot_id=s.bot_id AND a.user_id=s.user_id AND a.chat_id=s.chat_id)
 AND NOT EXISTS (
   SELECT 1 FROM schedule_runs r JOIN schedules j ON j.id=r.schedule_id
   WHERE r.turn_id=t.id AND (r.state<>'attached' OR r.generation<>j.generation
     OR r.expires_at<=clock_timestamp() OR (NOT j.enabled AND r.manual_key IS NULL))
 );
CREATE OR REPLACE VIEW live_delivery_authority AS
 SELECT o.id FROM outbox o
 WHERE EXISTS (SELECT 1 FROM allowlist a WHERE a.bot_id=o.bot_id AND a.chat_id=o.chat_id
   AND a.user_id=coalesce(o.user_id, CASE WHEN o.chat_id>0 THEN o.chat_id END))
 AND ((o.schedule_run_id IS NULL AND o.dedupe_key NOT LIKE 'scheduled:%') OR EXISTS (
   SELECT 1 FROM schedule_runs r JOIN schedules j ON j.id=r.schedule_id
   WHERE r.id=o.schedule_run_id AND r.state='published' AND r.generation=j.generation
     AND o.schedule_generation=j.generation AND r.expires_at>clock_timestamp()
     AND (j.enabled OR r.manual_key IS NOT NULL)
 ));
