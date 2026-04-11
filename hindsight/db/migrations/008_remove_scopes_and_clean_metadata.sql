-- Migration 008: Remove Scopes Column and Clean Up Metadata
-- Date: 2026-01-02
-- Description: Remove redundant scopes column (permissions already in metadata).
--              Clean up metadata duplication between source_accounts and github_installations.
--              source_accounts.metadata: account-level info (app_id)
--              github_installations.metadata: installation-specific info (permissions, urls, etc.)

-- ============================================================================
-- Phase 1: Drop Scopes Column
-- ============================================================================

-- Drop the scopes column (redundant with metadata->permissions)
ALTER TABLE github_installations DROP COLUMN IF EXISTS scopes;

COMMENT ON TABLE github_installations IS
'Ephemeral GitHub App installations. Multiple Hindsight users can have active installations
for the same source account. Installation-specific data (permissions, URLs) stored in metadata.
Permissions can be accessed via metadata->permissions JSONB field.';

-- ============================================================================
-- Phase 2: Update Comments
-- ============================================================================

COMMENT ON COLUMN source_accounts.metadata IS
'Account-level metadata (stable across installations). Example: app_id, provider config.';

COMMENT ON COLUMN github_installations.metadata IS
'Installation-specific metadata (can change per installation). Contains: permissions (dict),
github_url, api_url, repository_selection. Permissions can be accessed via metadata->permissions.';

-- ============================================================================
-- Migration Complete
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '==========================================================';
    RAISE NOTICE 'Migration 008 completed successfully!';
    RAISE NOTICE 'Removed redundant scopes column.';
    RAISE NOTICE 'Use metadata->permissions to access permissions.';
    RAISE NOTICE '==========================================================';
END $$;

