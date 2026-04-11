-- Migration: Add multi-provider repository connection support
-- Date: 2024-01-19
-- Description: Adds source_repo_connections table for storing OAuth/App tokens from multiple
--              repository providers (GitHub, GitLab, Bitbucket, etc.) and extends repositories
--              table to link with provider connections.

-- ============================================================================
-- Create source_repo_connections table
-- ============================================================================

CREATE TABLE source_repo_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,  -- FK to Kinde user ID (sub claim)
    provider VARCHAR(50) NOT NULL,  -- 'github', 'gitlab', 'bitbucket', 'perforce', etc.
    installation_id VARCHAR(255),   -- Provider-specific installation ID (e.g., GitHub App installation)
    account_type VARCHAR(50),       -- 'user', 'org', 'team'
    account_login VARCHAR(255),     -- Username or organization name
    access_token TEXT NOT NULL,     -- Encrypted OAuth/App token
    refresh_token TEXT,             -- Encrypted refresh token (if applicable)
    token_expires_at TIMESTAMP,     -- Token expiration timestamp
    scopes TEXT[],                  -- Array of granted permissions/scopes
    is_active BOOLEAN DEFAULT true, -- Whether connection is active
    last_used_at TIMESTAMP,         -- Last time connection was used
    metadata JSONB,                 -- Provider-specific metadata (e.g., server URL for GHES)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraints
    CONSTRAINT unique_user_provider_installation UNIQUE(user_id, provider, installation_id)
);

-- ============================================================================
-- Create indexes for source_repo_connections
-- ============================================================================

CREATE INDEX idx_src_conn_user_provider ON source_repo_connections(user_id, provider);
CREATE INDEX idx_src_conn_installation ON source_repo_connections(installation_id);
CREATE INDEX idx_src_conn_active ON source_repo_connections(is_active);
CREATE INDEX idx_src_conn_last_used ON source_repo_connections(last_used_at DESC);
CREATE INDEX idx_src_conn_expires ON source_repo_connections(token_expires_at);

-- ============================================================================
-- Extend repositories table
-- ============================================================================

-- Add columns for provider integration
ALTER TABLE repositories 
ADD COLUMN connection_id UUID REFERENCES source_repo_connections(id) ON DELETE SET NULL,
ADD COLUMN external_repo_id VARCHAR(255),  -- Provider's repository ID
ADD COLUMN visibility VARCHAR(50) CHECK (visibility IN ('public', 'private', 'internal'));

-- Create index for connection lookups
CREATE INDEX idx_repos_connection ON repositories(connection_id);
CREATE INDEX idx_repos_external_id ON repositories(external_repo_id);
CREATE INDEX idx_repos_visibility ON repositories(visibility);

-- ============================================================================
-- Add updated_at trigger for source_repo_connections
-- ============================================================================

CREATE TRIGGER update_src_conn_updated_at
    BEFORE UPDATE ON source_repo_connections
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- Comments for documentation
-- ============================================================================

COMMENT ON TABLE source_repo_connections IS 'Stores OAuth/App installation credentials for multiple repository providers';
COMMENT ON COLUMN source_repo_connections.user_id IS 'Kinde user ID who owns this connection';
COMMENT ON COLUMN source_repo_connections.provider IS 'Repository provider: github, gitlab, bitbucket, perforce, etc.';
COMMENT ON COLUMN source_repo_connections.installation_id IS 'Provider-specific installation/integration ID';
COMMENT ON COLUMN source_repo_connections.account_type IS 'Type of account: user, org, or team';
COMMENT ON COLUMN source_repo_connections.account_login IS 'Username or organization name on the provider';
COMMENT ON COLUMN source_repo_connections.access_token IS 'Encrypted OAuth access token or App installation token';
COMMENT ON COLUMN source_repo_connections.refresh_token IS 'Encrypted OAuth refresh token (if provider supports it)';
COMMENT ON COLUMN source_repo_connections.token_expires_at IS 'When the access token expires (NULL for non-expiring tokens)';
COMMENT ON COLUMN source_repo_connections.scopes IS 'Array of OAuth scopes or permissions granted';
COMMENT ON COLUMN source_repo_connections.is_active IS 'Whether this connection is currently active (false = revoked/disabled)';
COMMENT ON COLUMN source_repo_connections.last_used_at IS 'Last time this connection was used to access repositories';
COMMENT ON COLUMN source_repo_connections.metadata IS 'Provider-specific data: server URL for GHES/GitLab self-hosted, etc.';

COMMENT ON COLUMN repositories.connection_id IS 'Link to source_repo_connections table (NULL for manually added repos)';
COMMENT ON COLUMN repositories.external_repo_id IS 'Repository ID from the provider (e.g., GitHub repo ID)';
COMMENT ON COLUMN repositories.visibility IS 'Repository visibility: public, private, or internal';

