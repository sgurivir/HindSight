-- Migration 009: Set Repository Status to Ready
-- Date: 2026-01-02
-- Description: Update all existing repositories to 'ready' status.
--              New repositories will be created with 'ready' status by default.
--              This prepares for future indexing operations that may change status.

-- ============================================================================
-- Phase 1: Update existing repositories
-- ============================================================================

-- Update all repositories to 'ready' status
UPDATE repositories
SET status = 'ready', updated_at = NOW()
WHERE status IS NULL OR status != 'ready';

-- ============================================================================
-- Phase 2: Update default constraint (optional, for future-proofing)
-- ============================================================================

-- Note: The schema already has a default of 'pending', but we're setting
-- all repositories to 'ready' for now. When we add indexing operations,
-- we can update the default back to 'pending' and change it to 'ready' after indexing.

-- ============================================================================
-- Migration Complete
-- ============================================================================

DO $$
DECLARE
    updated_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO updated_count
    FROM repositories
    WHERE status = 'ready';

    RAISE NOTICE '==========================================================';
    RAISE NOTICE 'Migration 009 completed successfully!';
    RAISE NOTICE 'All repositories set to "ready" status.';
    RAISE NOTICE 'Total repositories with "ready" status: %', updated_count;
    RAISE NOTICE '==========================================================';
END $$;

