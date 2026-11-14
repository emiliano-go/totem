-- Migration 005: observed checks and a hash-chained history (schema v10)
--
-- memory_history gains a per-row chain (prev_hash/hash) plus a digest of the
-- item state at event time, so an out-of-band UPDATE to memory_items is
-- detectable. memory_checks stores observed check results; memory_verify
-- consumes one instead of trusting a free-text method claim.

ALTER TABLE memory_history ADD COLUMN prev_hash TEXT;
ALTER TABLE memory_history ADD COLUMN hash TEXT;
ALTER TABLE memory_history ADD COLUMN state_hash TEXT;

CREATE TABLE IF NOT EXISTS memory_checks (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    name TEXT NOT NULL,
    command TEXT,
    cwd TEXT,
    exit_code INTEGER NOT NULL,
    output_hash TEXT,
    evidence_hash TEXT,
    commit_sha TEXT,
    ran_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS memory_checks_item ON memory_checks (item_id);
