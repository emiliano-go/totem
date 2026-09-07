"""SQLite storage layer for srcmem."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .models import Conflict, Evidence, MemoryItem, MemoryStatus, MemoryType

SCHEMA_VERSION = 1

CREATE_TABLE = """
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
"""

CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts USING fts5(
    title,
    statement,
    details,
    content='memory_items',
    content_rowid='rowid'
);
"""

CREATE_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS memory_items_ai AFTER INSERT ON memory_items BEGIN
    INSERT INTO memory_items_fts(rowid, title, statement, details)
    VALUES (new.rowid, new.title, new.statement, new.details);
END;

CREATE TRIGGER IF NOT EXISTS memory_items_ad AFTER DELETE ON memory_items BEGIN
    INSERT INTO memory_items_fts(memory_items_fts, rowid, title, statement, details)
    VALUES ('delete', old.rowid, old.title, old.statement, old.details);
END;

CREATE TRIGGER IF NOT EXISTS memory_items_au AFTER UPDATE ON memory_items BEGIN
    INSERT INTO memory_items_fts(memory_items_fts, rowid, title, statement, details)
    VALUES ('delete', old.rowid, old.title, old.statement, old.details);
    INSERT INTO memory_items_fts(rowid, title, statement, details)
    VALUES (new.rowid, new.title, new.statement, new.details);
END;
"""

CREATE_CONFLICTS = """
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
"""


def get_db_path() -> Path:
    return Path.cwd() / ".srcmem" / "srcmem.db"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(CREATE_TABLE)
    conn.executescript(CREATE_FTS)
    conn.executescript(CREATE_FTS_TRIGGERS)
    conn.executescript(CREATE_CONFLICTS)
    conn.commit()


def _row_to_item(row: sqlite3.Row) -> MemoryItem:
    return MemoryItem(
        id=row["id"],
        type=MemoryType(row["type"]),
        title=row["title"],
        statement=row["statement"],
        details=row["details"],
        tags=json.loads(row["tags"]),
        status=MemoryStatus(row["status"]),
        confidence=row["confidence"],
        importance=row["importance"],
        evidence=[Evidence.model_validate(e) for e in json.loads(row["evidence"])],
        related_memory_ids=json.loads(row["related_memory_ids"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        verified_at=row["verified_at"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else None,
    )
