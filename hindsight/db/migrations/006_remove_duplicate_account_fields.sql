-- Migration 006: Remove Duplicate Account Fields from github_installations
-- Date: 2026-01-02
-- Description: Remove provider, account_type, and account_login from github_installations
--              since this information is now available via source_accounts table.
--              This eliminates data duplication and ensures single source of truth.

-- ============================================================================
-- Phase 1: Verify Data Integrity
-- ============================================================================

-- Check that all installations have source_account_id
DO $$
DECLARE
    unlinked_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO unlinked_count
    FROM github_installations
    WHERE source_account_id IS NULL;
    
    IF unlinked_count > 0 THEN
        RAISE EXCEPTION 'Cannot proceed: % installations without source_account_id. Run migration 005 first.', unlinked_count;
    ELSE
        RAISE NOTICE 'All installations have source_account_id ✓';
    END IF;
END $$;

-- ============================================================================
-- Phase 2: Drop Columns
-- ============================================================================

-- Drop account_login column (replaced by source_accounts.handle)
ALTER TABLE github_installations DROP COLUMN IF EXISTS account_login;

-- Drop account_type column (replaced by source_accounts.account_type)
ALTER TABLE github_installations DROP COLUMN IF EXISTS account_type;

-- Drop provider column (replaced by source_accounts.provider)
-- Note: provider is always 'github' for this table, but we get it from source_accounts
ALTER TABLE github_installations DROP COLUMN IF EXISTS provider;

-- ============================================================================
-- Phase 3: Update Comments
-- ============================================================================

COMMENT ON TABLE github_installations IS
'Ephemeral GitHub App installations. Account information (provider, handle, account_type)
is stored in source_accounts table via source_account_id foreign key. This table only
stores installation-specific data: tokens, scopes, and installation metadata.';

COMMENT ON COLUMN github_installations.source_account_id IS
'References source_accounts table for account information. Use JOIN to get provider,
handle, and account_type when needed.';

-- ============================================================================
-- Migration Complete
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '==========================================================';
    RAISE NOTICE 'Migration 006 completed successfully!';
    RAISE NOTICE 'Removed duplicate account fields from github_installations.';
    RAISE NOTICE 'Account information is now only in source_accounts table.';
    RAISE NOTICE '==========================================================';
END $$;

