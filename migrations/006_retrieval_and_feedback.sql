-- Migration 006: persisted embeddings, feedback, and temporal validity (v11)

ALTER TABLE memory_items ADD COLUMN valid_from TEXT;
ALTER TABLE memory_items ADD COLUMN valid_to TEXT;

CREATE TABLE IF NOT EXISTS memory_embeddings (
    item_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_embeddings_model ON memory_embeddings (model);

CREATE TABLE IF NOT EXISTS memory_feedback (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    signal TEXT NOT NULL,
    query TEXT,
    tags TEXT,
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_feedback_item ON memory_feedback (item_id);
CREATE INDEX IF NOT EXISTS memory_feedback_signal ON memory_feedback (signal);
