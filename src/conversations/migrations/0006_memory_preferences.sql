ALTER TABLE ava_conversations
    ALTER COLUMN memory_enabled SET DEFAULT TRUE;

UPDATE ava_conversations
    SET memory_enabled=TRUE
    WHERE memory_enabled=FALSE;

CREATE TABLE IF NOT EXISTS ava_memory_items (
    memory_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL CHECK (char_length(content) BETWEEN 1 AND 1500),
    memory_type TEXT NOT NULL CHECK (memory_type IN ('explicit', 'conversation_summary')),
    source_conversation_id UUID REFERENCES ava_conversations(conversation_id) ON DELETE CASCADE,
    source_message_id UUID REFERENCES ava_messages(message_id) ON DELETE SET NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES ava_users(tenant_id, user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ava_memory_items_owner_updated_idx
    ON ava_memory_items (tenant_id, user_id, updated_at DESC, memory_id DESC)
    WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ava_memory_summary_per_conversation_idx
    ON ava_memory_items (tenant_id, user_id, source_conversation_id)
    WHERE memory_type='conversation_summary' AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS ava_user_preferences (
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    nickname TEXT NOT NULL DEFAULT '' CHECK (char_length(nickname) <= 50),
    warmth TEXT NOT NULL DEFAULT 'balanced' CHECK (warmth IN ('cold', 'balanced', 'warm')),
    enthusiasm TEXT NOT NULL DEFAULT 'balanced' CHECK (enthusiasm IN ('low', 'balanced', 'high')),
    emoji_use TEXT NOT NULL DEFAULT 'off' CHECK (emoji_use IN ('off', 'light')),
    custom_instructions TEXT NOT NULL DEFAULT '' CHECK (char_length(custom_instructions) <= 1500),
    language TEXT NOT NULL DEFAULT 'en' CHECK (language IN ('en', 'sr')),
    model TEXT NOT NULL DEFAULT 'AZURE_GPT_4o_2024_1120',
    theme TEXT NOT NULL DEFAULT 'system' CHECK (theme IN ('light', 'dark', 'system')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES ava_users(tenant_id, user_id) ON DELETE CASCADE
);
