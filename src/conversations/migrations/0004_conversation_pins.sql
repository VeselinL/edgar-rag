ALTER TABLE ava_conversations
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE ava_conversations
    ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ;

DO $$
BEGIN
    ALTER TABLE ava_conversations
        ADD CONSTRAINT ava_conversations_pin_state_check
        CHECK (
            (pinned = TRUE AND pinned_at IS NOT NULL)
            OR (pinned = FALSE AND pinned_at IS NULL)
        );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS ava_conversations_owner_pin_order_idx
    ON ava_conversations (
        tenant_id,
        user_id,
        pinned DESC,
        pinned_at DESC,
        updated_at DESC,
        conversation_id DESC
    )
    WHERE deleted_at IS NULL;
