ALTER TABLE ava_feedback
    ADD COLUMN IF NOT EXISTS answer_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE ava_feedback
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE UNIQUE INDEX IF NOT EXISTS ava_feedback_assistant_message_idx
    ON ava_feedback (assistant_message_id);
