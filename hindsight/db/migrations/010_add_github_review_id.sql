-- Migration 010: Add GitHub Review ID Tracking
-- Date: 2026-01-02
-- Description: Add github_review_id column to track GitHub review IDs for deleting
--              old reviews before posting new ones. This prevents review clutter.

-- ============================================================================
-- Phase 1: Add github_review_id Column
-- ============================================================================

ALTER TABLE pr_analyses
ADD COLUMN github_review_id BIGINT;

-- ============================================================================
-- Phase 2: Create Index
-- ============================================================================

CREATE INDEX idx_pr_analyses_review_id ON pr_analyses(github_review_id);

-- ============================================================================
-- Phase 3: Add Comments
-- ============================================================================

COMMENT ON COLUMN pr_analyses.github_review_id IS
'GitHub review ID for deleting/updating previous reviews. When a new analysis runs,
the old review is deleted before posting a new one to prevent review clutter.';

-- ============================================================================
-- Migration Complete
-- ============================================================================

DO $$
BEGIN
    RAISE NOTICE '==========================================================';
    RAISE NOTICE 'Migration 010 completed successfully!';
    RAISE NOTICE 'Added github_review_id column to pr_analyses table.';
    RAISE NOTICE 'Old reviews will now be deleted before posting new ones.';
    RAISE NOTICE '==========================================================';
END $$;

