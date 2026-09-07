-- Migration 004: Add scope column, history table, bump schema version

ALTER TABLE memory_items ADD COLUMN scope TEXT;

CREATE TABLE IF NOT EXISTS memory_history (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    event TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    timestamp TEXT NOT NULL
);
