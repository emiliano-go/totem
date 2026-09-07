-- Migration 002: Add resolved_at/resolution to conflicts

ALTER TABLE conflicts ADD COLUMN resolved_at TEXT;
ALTER TABLE conflicts ADD COLUMN resolution TEXT;
