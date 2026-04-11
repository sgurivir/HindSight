-- Migration: Add progress column to pr_analyses table
-- Description: Support progress tracking for PR analysis (0-100%)
-- Created: 2026-01-09

-- Add progress column to pr_analyses table
ALTER TABLE pr_analyses 
ADD COLUMN IF NOT EXISTS progress INTEGER DEFAULT 0;

-- Add comment for documentation
COMMENT ON COLUMN pr_analyses.progress IS 'Analysis progress percentage (0-100)';
