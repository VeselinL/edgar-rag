CREATE TABLE IF NOT EXISTS ava_oidc_transactions (
    state_hash TEXT PRIMARY KEY,
    nonce TEXT NOT NULL,
    code_verifier TEXT NOT NULL,
    return_to TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ava_oidc_transactions_expiry_idx
    ON ava_oidc_transactions (expires_at);

CREATE TABLE IF NOT EXISTS ava_auth_sessions (
    session_hash TEXT PRIMARY KEY,
    csrf_hash TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (tenant_id, user_id)
        REFERENCES ava_users(tenant_id, user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ava_auth_sessions_owner_expiry_idx
    ON ava_auth_sessions (tenant_id, user_id, expires_at);
CREATE INDEX IF NOT EXISTS ava_auth_sessions_expiry_idx
    ON ava_auth_sessions (expires_at);
