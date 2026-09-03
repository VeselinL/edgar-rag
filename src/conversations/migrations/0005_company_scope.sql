ALTER TABLE ava_conversations
    ADD COLUMN IF NOT EXISTS company_scope TEXT[] NOT NULL DEFAULT '{}';
