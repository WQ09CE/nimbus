-- Additive data-scope policy, not a second scheduler or execution loop.
ALTER TABLE turns ADD COLUMN IF NOT EXISTS data_scope text NOT NULL DEFAULT 'public'
 CHECK (data_scope IN ('public','health'));
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS data_scope text NOT NULL DEFAULT 'public'
 CHECK (data_scope IN ('public','health'));
