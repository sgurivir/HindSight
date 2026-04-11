-- Migration: Add MCP Servers table
-- Purpose: Store Model Context Protocol server configurations for external data sources
-- Date: 2024-12-06

-- MCP Servers table (system-scoped, shared across all users)
CREATE TABLE mcp_servers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL UNIQUE,
    description TEXT,
    server_type VARCHAR(50) NOT NULL CHECK (server_type IN ('datadog', 'splunk', 'newrelic', 'postgres', 'custom')),
    connection_type VARCHAR(20) NOT NULL CHECK (connection_type IN ('stdio', 'sse', 'http')),
    endpoint TEXT NOT NULL,
    auth_config TEXT,  -- Encrypted JSON (using existing ENCRYPTION_KEY)
    capabilities JSONB DEFAULT '{}'::jsonb,
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'error')),
    is_enabled BOOLEAN DEFAULT true,
    created_by VARCHAR(255) NOT NULL,  -- Kinde user ID
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for efficient queries
CREATE INDEX idx_mcp_servers_status ON mcp_servers(status);
CREATE INDEX idx_mcp_servers_enabled ON mcp_servers(is_enabled);
CREATE INDEX idx_mcp_servers_created_by ON mcp_servers(created_by);
CREATE INDEX idx_mcp_servers_type ON mcp_servers(server_type);

-- Comments
COMMENT ON TABLE mcp_servers IS 'Model Context Protocol servers for external data sources (Datadog, Splunk, etc.)';
COMMENT ON COLUMN mcp_servers.name IS 'Unique human-readable server name (e.g., "Production Datadog")';
COMMENT ON COLUMN mcp_servers.server_type IS 'Type of MCP server: datadog, splunk, newrelic, postgres, custom';
COMMENT ON COLUMN mcp_servers.connection_type IS 'Connection protocol: stdio, sse, http';
COMMENT ON COLUMN mcp_servers.endpoint IS 'Server endpoint URL or command to execute';
COMMENT ON COLUMN mcp_servers.auth_config IS 'Encrypted authentication credentials (JSON)';
COMMENT ON COLUMN mcp_servers.capabilities IS 'Discovered server capabilities: tools, resources, etc.';
COMMENT ON COLUMN mcp_servers.is_enabled IS 'Whether server is enabled (admin can disable without deleting)';
COMMENT ON COLUMN mcp_servers.created_by IS 'Kinde user ID of admin who registered the server';

