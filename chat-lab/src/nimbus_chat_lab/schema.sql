-- v1: separate database required; never run against Nimbus's existing stores.
CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY CHECK (version = 1));
INSERT INTO schema_version VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS bots (
  id bigint PRIMARY KEY, poll_offset bigint NOT NULL DEFAULT 0,
  send_after timestamptz NOT NULL DEFAULT '-infinity'
);
CREATE TABLE IF NOT EXISTS allowlist (
  bot_id bigint NOT NULL REFERENCES bots(id), user_id bigint NOT NULL, chat_id bigint NOT NULL,
  PRIMARY KEY (bot_id, user_id, chat_id)
);
CREATE TABLE IF NOT EXISTS inbox (
  bot_id bigint NOT NULL REFERENCES bots(id), update_id bigint NOT NULL,
  disposition text NOT NULL, turn_id uuid, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (bot_id, update_id)
);
CREATE TABLE IF NOT EXISTS sessions (
  id uuid PRIMARY KEY, bot_id bigint NOT NULL REFERENCES bots(id), user_id bigint NOT NULL,
  chat_id bigint NOT NULL, thread_id bigint NOT NULL DEFAULT 0, epoch integer NOT NULL DEFAULT 0,
  UNIQUE (bot_id, user_id, chat_id, thread_id)
);
CREATE TABLE IF NOT EXISTS turns (
  id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES sessions(id), epoch integer NOT NULL,
  state text NOT NULL CHECK (state IN ('queued','running','cancel_requested','succeeded','failed','interrupted','cancelled')),
  input text NOT NULL CHECK (length(input) <= 8000), result text NOT NULL DEFAULT '' CHECK (length(result) <= 16000),
  generation bigint NOT NULL DEFAULT 0, attempt_id uuid,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_turn_per_session ON turns(session_id)
  WHERE state IN ('queued','running','cancel_requested');
CREATE INDEX IF NOT EXISTS queued_turns ON turns(created_at) WHERE state = 'queued';
CREATE TABLE IF NOT EXISTS attempts (
  id uuid PRIMARY KEY, turn_id uuid NOT NULL REFERENCES turns(id), generation bigint NOT NULL,
  incarnation uuid NOT NULL, lease_until timestamptz NOT NULL,
  state text NOT NULL CHECK (state IN ('running','succeeded','failed','interrupted','cancelled')),
  UNIQUE (turn_id, generation)
);
CREATE TABLE IF NOT EXISTS turn_events (
  seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  turn_id uuid NOT NULL REFERENCES turns(id), attempt_id uuid NOT NULL REFERENCES attempts(id),
  kind text NOT NULL, text text NOT NULL CHECK (length(text) <= 16000),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS events_by_turn ON turn_events(turn_id, seq);
CREATE TABLE IF NOT EXISTS outbox (
  id uuid PRIMARY KEY, seq bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
  dedupe_key text NOT NULL UNIQUE, bot_id bigint NOT NULL REFERENCES bots(id),
  chat_id bigint NOT NULL, thread_id bigint NOT NULL DEFAULT 0, text text NOT NULL,
  state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','sent','uncertain','failed')),
  tries integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  claimed_at timestamptz, message_id bigint, error_class text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS pending_outbox ON outbox(bot_id, available_at) WHERE state = 'pending';
CREATE INDEX IF NOT EXISTS ordered_delivery ON outbox(bot_id,chat_id,seq) WHERE state IN ('pending','sending');
