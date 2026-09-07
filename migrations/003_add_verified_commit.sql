-- Migration 003: Add verified_commit column

ALTER TABLE memory_items ADD COLUMN verified_commit TEXT;
