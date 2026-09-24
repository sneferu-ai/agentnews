-- AgentNews initial schema (FR-004, FR-005, FR-026, §5.1)
-- Forward-only migration. Wrapped in BEGIN/COMMIT by the migration runner.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE packages (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    run_type TEXT,
    question TEXT NOT NULL,
    summary TEXT,
    findings TEXT NOT NULL,
    method_notes TEXT,
    quality_score REAL NOT NULL,
    signature TEXT NOT NULL,
    reuse_terms TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'imported',
    is_sample INTEGER NOT NULL DEFAULT 0,
    publish_after TEXT,
    latency_hours REAL,
    latency_disclosure TEXT NOT NULL,
    cost_data TEXT,
    run_started_at TEXT,
    run_completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    published_at TEXT,
    withheld_at TEXT,
    extensions TEXT DEFAULT '{}'
);

CREATE INDEX idx_packages_status ON packages(status);
CREATE INDEX idx_packages_published_at ON packages(published_at DESC);
CREATE UNIQUE INDEX idx_packages_single_sample ON packages(is_sample) WHERE is_sample = 1;

CREATE TABLE citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    source_type TEXT,
    title TEXT,
    url TEXT,
    authors TEXT,
    year TEXT,
    verified INTEGER DEFAULT 0,
    excerpt TEXT,
    accessed_at TEXT,
    UNIQUE(package_id, ordinal)
);

CREATE INDEX idx_citations_package ON citations(package_id);

CREATE TABLE api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    label TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX idx_api_keys_status ON api_keys(status);

CREATE TABLE request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_prefix TEXT,
    method TEXT,
    endpoint TEXT,
    status_code INTEGER,
    response_time_ms INTEGER,
    ip_hash TEXT,
    user_agent_hash TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_request_log_key_date ON request_log(key_prefix, created_at);
CREATE INDEX idx_request_log_ip_date ON request_log(ip_hash, created_at);
CREATE INDEX idx_request_log_ua_date ON request_log(user_agent_hash, created_at);
CREATE INDEX idx_request_log_created_at ON request_log(created_at);

CREATE TABLE ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    cost_data TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
