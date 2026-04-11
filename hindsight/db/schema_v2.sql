-- Hindsight Database Schema v2
-- Supports REST API with repositories and analyses resources

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Repositories table
CREATE TABLE repositories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    github_url TEXT NOT NULL,
    name VARCHAR(255) NOT NULL,
    clone_path TEXT,
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'ready', 'error')),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, github_url)
);

-- Analyses table
CREATE TABLE analyses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    config JSONB,
    total_issues INTEGER DEFAULT 0,
    critical_issues INTEGER DEFAULT 0,
    high_issues INTEGER DEFAULT 0,
    medium_issues INTEGER DEFAULT 0,
    low_issues INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT
);

-- Analysis results table
CREATE TABLE analysis_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id UUID NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    file_path TEXT,
    function_name VARCHAR(500),
    line_number TEXT,
    severity VARCHAR(20) CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    issue_type VARCHAR(100),
    description TEXT NOT NULL,
    impact TEXT,
    potential_solution TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Prior results cache table for avoiding reanalysis
-- Stores function analysis results keyed by checksum for deduplication
CREATE TABLE function_analysis_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id UUID NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    function_name VARCHAR(500) NOT NULL,
    function_checksum VARCHAR(64) NOT NULL,
    result_data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(repository_id, file_path, function_name, function_checksum)
);

-- Indexes for performance
CREATE INDEX idx_repos_user ON repositories(user_id);
CREATE INDEX idx_repos_url ON repositories(github_url);
CREATE INDEX idx_repos_status ON repositories(status);
CREATE INDEX idx_repos_created ON repositories(created_at DESC);
CREATE INDEX idx_repos_user_created ON repositories(user_id, created_at DESC);

CREATE INDEX idx_analyses_repo ON analyses(repository_id);
CREATE INDEX idx_analyses_status ON analyses(status);
CREATE INDEX idx_analyses_created ON analyses(created_at DESC);

CREATE INDEX idx_results_analysis ON analysis_results(analysis_id);
CREATE INDEX idx_results_severity ON analysis_results(severity);
CREATE INDEX idx_results_file_path ON analysis_results(file_path);
CREATE INDEX idx_results_function ON analysis_results(function_name);

-- Indexes for function_analysis_cache (fast cache lookups)
CREATE INDEX idx_cache_repository ON function_analysis_cache(repository_id);
CREATE INDEX idx_cache_checksum ON function_analysis_cache(function_checksum);
CREATE INDEX idx_cache_function ON function_analysis_cache(function_name);
CREATE INDEX idx_cache_lookup ON function_analysis_cache(repository_id, file_path, function_name, function_checksum);

-- Trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_repositories_updated_at
    BEFORE UPDATE ON repositories
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_cache_updated_at
    BEFORE UPDATE ON function_analysis_cache
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- View for repository summaries
CREATE VIEW repository_summary AS
SELECT 
    r.id,
    r.user_id,
    r.github_url,
    r.name,
    r.status,
    r.created_at,
    COUNT(DISTINCT a.id) as total_analyses,
    COUNT(DISTINCT CASE WHEN a.status = 'running' THEN a.id END) as running_analyses,
    COUNT(DISTINCT CASE WHEN a.status = 'completed' THEN a.id END) as completed_analyses,
    SUM(a.total_issues) as total_issues_found
FROM repositories r
LEFT JOIN analyses a ON r.id = a.repository_id
GROUP BY r.id, r.user_id, r.github_url, r.name, r.status, r.created_at;

-- View for analysis summaries
CREATE VIEW analysis_summary AS
SELECT 
    a.id,
    a.repository_id,
    r.name as repository_name,
    a.status,
    a.progress,
    a.total_issues,
    a.critical_issues,
    a.high_issues,
    a.medium_issues,
    a.low_issues,
    a.created_at,
    a.started_at,
    a.completed_at,
    EXTRACT(EPOCH FROM (COALESCE(a.completed_at, NOW()) - a.started_at)) as duration_seconds
FROM analyses a
JOIN repositories r ON a.repository_id = r.id;

-- Function to get analysis statistics
CREATE OR REPLACE FUNCTION get_analysis_stats(analysis_uuid UUID)
RETURNS TABLE (
    severity VARCHAR(20),
    count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        ar.severity,
        COUNT(*) as count
    FROM analysis_results ar
    WHERE ar.analysis_id = analysis_uuid
    GROUP BY ar.severity
    ORDER BY 
        CASE ar.severity
            WHEN 'critical' THEN 1
            WHEN 'high' THEN 2
            WHEN 'medium' THEN 3
            WHEN 'low' THEN 4
        END;
END;
$$ LANGUAGE plpgsql;

-- Function to cleanup old analyses (optional, for maintenance)
CREATE OR REPLACE FUNCTION cleanup_old_analyses(days_old INTEGER DEFAULT 90)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    WITH deleted AS (
        DELETE FROM analyses
        WHERE status = 'completed'
        AND completed_at < NOW() - INTERVAL '1 day' * days_old
        RETURNING id
    )
    SELECT COUNT(*) INTO deleted_count FROM deleted;
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Comments for documentation
COMMENT ON TABLE repositories IS 'Stores GitHub repository information';
COMMENT ON TABLE analyses IS 'Stores code analysis jobs for repositories';
COMMENT ON TABLE analysis_results IS 'Stores individual issues found during analysis';
COMMENT ON TABLE function_analysis_cache IS 'Caches function analysis results to avoid reanalysis of unchanged code';

COMMENT ON COLUMN repositories.user_id IS 'Kinde user ID (sub claim from ID token) - identifies the repository owner';
COMMENT ON COLUMN repositories.github_url IS 'GitHub repository URL (HTTPS or SSH format)';
COMMENT ON COLUMN repositories.name IS 'Repository name in format org/repo';
COMMENT ON COLUMN repositories.clone_path IS 'Local filesystem path where repo is cloned';
COMMENT ON COLUMN repositories.status IS 'Repository status: pending (not cloned), ready (cloned), error (clone failed)';

COMMENT ON COLUMN analyses.config IS 'Analysis configuration in JSON format (exclude_directories, etc.)';
COMMENT ON COLUMN analyses.progress IS 'Analysis progress from 0-100%';
COMMENT ON COLUMN analyses.status IS 'Analysis status: pending, running, completed, failed';

COMMENT ON COLUMN analysis_results.severity IS 'Issue severity: critical, high, medium, low';
COMMENT ON COLUMN analysis_results.issue_type IS 'Type/category of issue (e.g., securityVulnerability)';

COMMENT ON COLUMN function_analysis_cache.repository_id IS 'Foreign key to repositories table';
COMMENT ON COLUMN function_analysis_cache.function_checksum IS 'SHA hash of function content for detecting changes';
COMMENT ON COLUMN function_analysis_cache.result_data IS 'Complete analysis result in JSON format';

