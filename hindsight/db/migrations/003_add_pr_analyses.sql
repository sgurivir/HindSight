-- Migration: Add PR Analyses and Webhook Events Tables
-- Description: Support GitHub Pull Request analysis tracking and webhook event logging
-- Created: 2024-12-10

-- Table: pr_analyses
-- Tracks PR analysis runs independently with complete fields
CREATE TABLE IF NOT EXISTS pr_analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    pr_number INTEGER NOT NULL,
    head_sha VARCHAR(40) NOT NULL,
    base_sha VARCHAR(40) NOT NULL,
    pr_url TEXT,
    installation_id VARCHAR(255),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    config JSONB,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    results JSONB,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP,
    CONSTRAINT pr_analyses_status_check CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    CONSTRAINT pr_analyses_unique_analysis UNIQUE(repository_id, pr_number, head_sha)
);

-- Indexes for pr_analyses
CREATE INDEX idx_pr_analyses_repository ON pr_analyses(repository_id);
CREATE INDEX idx_pr_analyses_status ON pr_analyses(status);
CREATE INDEX idx_pr_analyses_created ON pr_analyses(created_at DESC);
CREATE INDEX idx_pr_analyses_pr_number ON pr_analyses(repository_id, pr_number);

-- Table: webhook_events
-- Logs all incoming webhook events for debugging and audit trail
CREATE TABLE IF NOT EXISTS webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,
    delivery_id VARCHAR(255),
    payload JSONB NOT NULL,
    signature VARCHAR(255),
    status VARCHAR(20) DEFAULT 'received',
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMP,
    CONSTRAINT webhook_events_status_check CHECK (status IN ('received', 'processing', 'processed', 'failed'))
);

-- Indexes for webhook_events
CREATE INDEX idx_webhook_events_type ON webhook_events(event_type);
CREATE INDEX idx_webhook_events_created ON webhook_events(created_at DESC);
CREATE INDEX idx_webhook_events_delivery_id ON webhook_events(delivery_id);
CREATE INDEX idx_webhook_events_status ON webhook_events(status);

-- Comments for documentation
COMMENT ON TABLE pr_analyses IS 'Tracks GitHub Pull Request analysis runs with results and token usage';
COMMENT ON TABLE webhook_events IS 'Logs GitHub webhook events for debugging and audit purposes';
COMMENT ON COLUMN pr_analyses.status IS 'Analysis status: pending, running, completed, failed';
COMMENT ON COLUMN pr_analyses.input_tokens IS 'LLM input tokens used during analysis';
COMMENT ON COLUMN pr_analyses.output_tokens IS 'LLM output tokens generated during analysis';
COMMENT ON COLUMN pr_analyses.results IS 'JSON array of analysis issues found';
COMMENT ON COLUMN webhook_events.status IS 'Webhook processing status: received, processing, processed, failed';

