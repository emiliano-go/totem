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


def insert_item(conn: sqlite3.Connection, item: MemoryItem) -> None:
    conn.execute(
        """INSERT INTO memory_items
           (id, type, title, statement, details, tags, status, confidence,
            importance, evidence, related_memory_ids, created_at, updated_at,
            verified_at, metadata, schema_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item.id,
            item.type.value,
            item.title,
            item.statement,
            item.details,
            json.dumps(item.tags),
            item.status.value,
            item.confidence,
            item.importance,
            json.dumps([e.model_dump(by_alias=True) for e in item.evidence]),
            json.dumps(item.related_memory_ids),
            item.created_at,
            item.updated_at,
            item.verified_at,
            json.dumps(item.metadata) if item.metadata else None,
            SCHEMA_VERSION,
        ),
    )
    conn.commit()


def get_item(conn: sqlite3.Connection, item_id: str) -> MemoryItem | None:
    row = conn.execute(
        "SELECT * FROM memory_items WHERE id = ? AND status != 'deleted'",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_item(row)


def update_item_row(
    conn: sqlite3.Connection,
    item_id: str,
    fields: dict[str, Any],
) -> None:
    if not fields:
        return
    set_clauses = []
    values = []
    for key, val in fields.items():
        set_clauses.append(f"{key} = ?")
        values.append(val)
    values.append(item_id)
    conn.execute(
        f"UPDATE memory_items SET {', '.join(set_clauses)} WHERE id = ?",
        values,
    )
    conn.commit()


def soft_delete(conn: sqlite3.Connection, item_id: str) -> None:
    from datetime import datetime, timezone

    update_item_row(
        conn,
        item_id,
        {
            "status": MemoryStatus.DELETED.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def list_items(
    conn: sqlite3.Connection,
    type_: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[MemoryItem]:
    query = "SELECT * FROM memory_items WHERE status != 'deleted'"
    params: list[Any] = []
    if type_:
        query += " AND type = ?"
        params.append(type_)
    if status:
        query += " AND status = ?"
        params.append(status)
    if tags:
        placeholders = ",".join("?" for _ in tags)
        query += f" AND EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value IN ({placeholders}))"
        params.extend(tags)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_row_to_item(row) for row in rows]


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    types: list[str] | None = None,
    tags: list[str] | None = None,
    include_stale: bool = False,
    limit: int = 20,
) -> list[MemoryItem]:
    fts_query = " OR ".join(query.split())
    sql = """
        SELECT m.* FROM memory_items m
        JOIN memory_items_fts fts ON m.rowid = fts.rowid
        WHERE memory_items_fts MATCH ?
          AND m.status != 'deleted'
    """
    params: list[Any] = [fts_query]
    if not include_stale:
        sql += " AND m.status != 'potentially_stale'"
    if types:
        placeholders = ",".join("?" for _ in types)
        sql += f" AND m.type IN ({placeholders})"
        params.extend(types)
    if tags:
        tag_placeholders = ",".join("?" for _ in tags)
        sql += f" AND EXISTS (SELECT 1 FROM json_each(m.tags) WHERE json_each.value IN ({tag_placeholders}))"
        params.extend(tags)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_item(row) for row in rows]


def get_overlapping_items(
    conn: sqlite3.Connection,
    type_: str,
    path: str,
    start_line: int,
    end_line: int,
) -> list[MemoryItem]:
    """Find active items of the same type with overlapping evidence ranges."""
    rows = conn.execute(
        """SELECT * FROM memory_items
           WHERE type = ? AND status = 'active' AND id != ''
           ORDER BY updated_at DESC""",
        (type_,),
    ).fetchall()
    results = []
    for row in rows:
        item = _row_to_item(row)
        for ev in item.evidence:
            if ev.path == path and ev.start_line <= end_line and ev.end_line >= start_line:
                results.append(item)
                break
    return results


def insert_conflict(conn: sqlite3.Connection, conflict: Conflict) -> None:
    """Persist a conflict to the conflicts table (§42)."""
    from datetime import datetime, timezone

    conn.execute(
        """INSERT INTO conflicts
           (id, item_a, item_b, claim_a, claim_b, condition,
            resolution_options, recommended, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            conflict.item_a,
            conflict.item_b,
            conflict.claim_a,
            conflict.claim_b,
            conflict.condition,
            json.dumps(conflict.resolution_options),
            conflict.recommended,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def get_conflicts_for_item(conn: sqlite3.Connection, item_id: str) -> list[Conflict]:
    """Retrieve all conflicts involving a given item."""
    rows = conn.execute(
        "SELECT * FROM conflicts WHERE item_a = ? OR item_b = ?",
        (item_id, item_id),
    ).fetchall()
    return [
        Conflict(
            itemA=row["item_a"],
            itemB=row["item_b"],
            claimA=row["claim_a"],
            claimB=row["claim_b"],
            condition=row["condition"],
            resolutionOptions=json.loads(row["resolution_options"]),
            recommended=row["recommended"],
        )
        for row in rows
    ]


def get_all_conflicts(conn: sqlite3.Connection) -> list[Conflict]:
    """Retrieve all stored conflicts."""
    rows = conn.execute("SELECT * FROM conflicts").fetchall()
    return [
        Conflict(
            itemA=row["item_a"],
            itemB=row["item_b"],
            claimA=row["claim_a"],
            claimB=row["claim_b"],
            condition=row["condition"],
            resolutionOptions=json.loads(row["resolution_options"]),
            recommended=row["recommended"],
        )
        for row in rows
    ]
