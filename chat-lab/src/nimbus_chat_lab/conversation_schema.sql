-- Foreground conversations and per-schedule background lanes are independently runnable.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS lane text NOT NULL DEFAULT 'chat';
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS context_since timestamptz NOT NULL DEFAULT '1970-01-01 00:00:00+00';
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_bot_id_user_id_chat_id_thread_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS session_lane_identity ON sessions(bot_id,user_id,chat_id,thread_id,lane);
DROP INDEX IF EXISTS one_active_turn_per_session;
CREATE UNIQUE INDEX one_active_turn_per_session ON turns(session_id) WHERE state IN ('running','cancel_requested');
ALTER TABLE agent_workspaces ADD COLUMN IF NOT EXISTS lane text NOT NULL DEFAULT 'chat';
ALTER TABLE agent_workspaces DROP CONSTRAINT IF EXISTS agent_workspaces_pkey;
ALTER TABLE agent_workspaces ADD PRIMARY KEY(bot_id,user_id,chat_id,lane);
