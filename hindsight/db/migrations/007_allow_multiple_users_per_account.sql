-- Migration 007: Allow Multiple Users Per Source Account
-- Date: 2026-01-02
-- Description: Update constraint to allow multiple Hindsight users to have active installations
--              for the same source account (e.g., multiple users connecting to same GitHub org).
--              This enables team collaboration where multiple team members can import repos
--              from the same organization.

-- ============================================================================
-- Phase 1: Drop Old Constraint
-- ============================================================================

-- Drop the old constraint that only allowed one active installation per account
DROP INDEX IF EXISTS unique_active_installation_per_account;

-- ============================================================================
-- Phase 2: Create New Constraint
-- ============================================================================

-- New constraint: one active installation per (source_account_id, user_id) combination
-- This allows multiple users to have active installations for the same source account
CREATE UNIQUE INDEX unique_active_installation_per_account_user
ON github_installations(source_account_id, user_id)
WHERE is_active = TRUE;

COMMENT ON INDEX unique_active_installation_per_account_user IS
'Ensures each Hindsight user has exactly one active GitHub installation per source account.
Multiple users can have active installations for the same source account (e.g., same org).';

-- ============================================================================
-- Phase 3: Update Health Check Function
-- ============================================================================

-- Update the health check function to reflect the new constraint
CREATE OR REPLACE FUNCTION check_account_architecture_health()
RETURNS TABLE(
    check_name TEXT,
    status TEXT,
    details TEXT
) AS $$
BEGIN
    -- Check 1: All installations have source accounts
    RETURN QUERY
    SELECT 
        'Installations with accounts'::TEXT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'FAIL' END::TEXT,
        COUNT(*)::TEXT || ' installations without source_account_id'
    FROM github_installations
    WHERE source_account_id IS NULL;
    
    -- Check 2: All repositories have source accounts
    RETURN QUERY
    SELECT 
        'Repositories with accounts'::TEXT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'FAIL' END::TEXT,
        COUNT(*)::TEXT || ' repositories without owner_source_account_id'
    FROM repositories
    WHERE owner_source_account_id IS NULL;
    
    -- Check 3: Multiple active installations per (account, user) - should be 0 with new constraint
    RETURN QUERY
    SELECT 
        'Multiple active installations per user+account'::TEXT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'FAIL' END::TEXT,
        COUNT(*)::TEXT || ' (user_id, source_account_id) pairs with multiple active installations'
    FROM (
        SELECT source_account_id, user_id
        FROM github_installations
        WHERE is_active = TRUE
        GROUP BY source_account_id, user_id
        HAVING COUNT(*) > 1
    ) duplicates;
    
    -- Check 4: Repositories without active installation for their owner user
    RETURN QUERY
    SELECT 
        'Repositories without active user installation'::TEXT,
        CASE WHEN COUNT(*) = 0 THEN 'OK' ELSE 'FAIL' END::TEXT,
        COUNT(*)::TEXT || ' repositories where owner user has no active installation for the account'
    FROM repositories r
    WHERE NOT EXISTS (
        SELECT 1 FROM github_installations i
        WHERE i.source_account_id = r.owner_source_account_id
        AND i.user_id = r.user_id
        AND i.is_active = TRUE
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION check_account_architecture_health IS
'Health check function to verify account-based architecture integrity.
Updated for multi-user support: checks for violations of (source_account_id, user_id) constraint.';

-- ============================================================================
-- Phase 4: Update Table Comment
-- ============================================================================

COMMENT ON TABLE github_installations IS
'Ephemeral GitHub App installations. Multiple Hindsight users can have active installations
for the same source account (e.g., multiple team members connecting to same GitHub org).
When a user reinstalls the app or updates permissions, their old installation is marked
inactive and a new one created. Repositories remain linked to source_accounts, not installations.';

-- ============================================================================
-- Migration Complete
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '==========================================================';
    RAISE NOTICE 'Migration 007 completed successfully!';
    RAISE NOTICE 'Multiple users can now connect to the same source account.';
    RAISE NOTICE 'Constraint updated: (source_account_id, user_id) per active installation.';
    RAISE NOTICE 'Health check function updated for multi-user support.';
    RAISE NOTICE '==========================================================';
END $$;
