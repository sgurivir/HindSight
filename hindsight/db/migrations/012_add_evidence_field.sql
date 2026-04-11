-- Migration: Add evidence field to analysis_results table
-- Description: Add evidence column to store LLM-generated reasoning explaining why each issue is legitimate
-- Created: 2026-01-09

-- Add evidence column to analysis_results table
ALTER TABLE analysis_results 
ADD COLUMN IF NOT EXISTS evidence TEXT DEFAULT '';

-- Add comment explaining the field
COMMENT ON COLUMN analysis_results.evidence IS 
'LLM-generated reasoning explaining why this issue is legitimate and worth fixing. Generated during Level 3 (Response Challenger) filtering.';

-- Optional: Create GIN index for full-text search (for future search features)
CREATE INDEX IF NOT EXISTS idx_analysis_results_evidence_search 
ON analysis_results USING gin(to_tsvector('english', evidence))
WHERE evidence != '';
