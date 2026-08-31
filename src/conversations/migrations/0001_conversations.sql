CREATE TABLE IF NOT EXISTS ava_tenants (
    tenant_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ava_users (
    user_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL REFERENCES ava_tenants(tenant_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS ava_conversations (
    conversation_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    memory_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES ava_users(tenant_id, user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ava_conversations_owner_updated_idx
    ON ava_conversations (tenant_id, user_id, updated_at DESC, conversation_id DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS ava_messages (
    message_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES ava_conversations(conversation_id) ON DELETE CASCADE,
    client_turn_id UUID NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('in_progress', 'completed', 'failed')),
    ordinal BIGINT NOT NULL,
    request_id UUID,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (conversation_id, client_turn_id, role),
    UNIQUE (conversation_id, ordinal)
);
CREATE INDEX IF NOT EXISTS ava_messages_conversation_order_idx
    ON ava_messages (conversation_id, ordinal);

CREATE TABLE IF NOT EXISTS ava_conversation_summaries (
    summary_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL UNIQUE REFERENCES ava_conversations(conversation_id) ON DELETE CASCADE,
    through_ordinal BIGINT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ava_source_uses (
    conversation_id UUID NOT NULL REFERENCES ava_conversations(conversation_id) ON DELETE CASCADE,
    assistant_message_id UUID NOT NULL REFERENCES ava_messages(message_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    source_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (assistant_message_id, source_id)
);

CREATE TABLE IF NOT EXISTS ava_feedback (
    feedback_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES ava_conversations(conversation_id) ON DELETE CASCADE,
    assistant_message_id UUID NOT NULL REFERENCES ava_messages(message_id) ON DELETE CASCADE,
    value TEXT NOT NULL,
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ava_deletion_audit (
    deletion_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conversation_id UUID,
    scope TEXT NOT NULL CHECK (scope IN ('conversation', 'all_conversations', 'retention')),
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ava_deletion_audit_owner_time_idx
    ON ava_deletion_audit (tenant_id, user_id, deleted_at DESC);
