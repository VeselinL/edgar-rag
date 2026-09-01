CREATE TABLE IF NOT EXISTS ava_uploaded_documents (
    document_id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES ava_conversations(conversation_id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('text/plain', 'application/pdf')),
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 20971520),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    asset_key UUID NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('ready', 'failed')),
    page_count INTEGER CHECK (page_count IS NULL OR page_count BETWEEN 1 AND 200),
    token_count INTEGER NOT NULL CHECK (token_count BETWEEN 1 AND 200000),
    chunk_count INTEGER NOT NULL CHECK (chunk_count > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES ava_users(tenant_id, user_id) ON DELETE CASCADE,
    UNIQUE (conversation_id, sha256)
);
CREATE INDEX IF NOT EXISTS ava_uploaded_documents_owner_chat_idx
    ON ava_uploaded_documents (tenant_id, user_id, conversation_id, created_at, document_id);

CREATE TABLE IF NOT EXISTS ava_uploaded_document_chunks (
    document_id UUID NOT NULL REFERENCES ava_uploaded_documents(document_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    page_number INTEGER,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count > 0 AND token_count <= 500),
    PRIMARY KEY (document_id, ordinal)
);
