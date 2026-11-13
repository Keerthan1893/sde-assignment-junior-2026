
CREATE TABLE IF NOT EXISTS metadata (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per (workflow, canonical phone). The full phone and name are never stored.
CREATE TABLE IF NOT EXISTS leads (
    workflow_id TEXT NOT NULL,
    phone_token TEXT NOT NULL,
    phone_last4 TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    PRIMARY KEY (workflow_id, phone_token)
);

-- Append-only interaction ledger. phone_token is null for phone-less overrides.
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    workflow_id TEXT,
    call_id TEXT,
    event_type TEXT,
    occurred_at TEXT,
    phone_token TEXT,
    disposition_code TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_call ON events (workflow_id, call_id);

-- Rejected lead rows contain only location and reason, never the original row.
CREATE TABLE IF NOT EXISTS rejections (
    source_path TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (source_path, row_number)
);

-- File receipts make re-ingestion idempotent and hold irreversible counters.
CREATE TABLE IF NOT EXISTS ingested_files (
    source_path TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    lead_rows_total INTEGER NOT NULL DEFAULT 0,
    duplicate_lead_rows_merged INTEGER NOT NULL DEFAULT 0,
    field_warnings INTEGER NOT NULL DEFAULT 0,
    events_total INTEGER NOT NULL DEFAULT 0,
    malformed_events INTEGER NOT NULL DEFAULT 0,
    duplicate_events_dropped INTEGER NOT NULL DEFAULT 0
);
