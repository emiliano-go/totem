-- Migration 001: Initial schema
-- Creates the base memory_items, conflicts, and FTS index

CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    details TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 1.0,
    importance REAL NOT NULL DEFAULT 0.5,
    evidence TEXT NOT NULL DEFAULT '[]',
    related_memory_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    verified_at TEXT,
    metadata TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS memory_items_fts ON memory_items USING fts (title, statement, details, tags);

CREATE TABLE IF NOT EXISTS conflicts (
    id TEXT PRIMARY KEY,
    item_a TEXT NOT NULL,
    item_b TEXT NOT NULL,
    claim_a TEXT NOT NULL,
    claim_b TEXT NOT NULL,
    condition TEXT NOT NULL,
    resolution_options TEXT NOT NULL,
    recommended TEXT,
    created_at TEXT NOT NULL
);
