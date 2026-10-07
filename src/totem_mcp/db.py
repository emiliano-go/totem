"""Turso storage layer for totem."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import turso

from .models import Conflict, Evidence, MemoryItem, MemoryStatus, MemoryType

SCHEMA_VERSION = 8

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    statement_normalized TEXT,
    details TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 1.0,
    importance REAL NOT NULL DEFAULT 0.5,
    scope TEXT,
    evidence TEXT NOT NULL DEFAULT '[]',
    related_memory_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    verified_at TEXT,
    verified_commit TEXT,
    metadata TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    asserted_by TEXT,
    applicability TEXT
);
"""

CREATE_FTS = """
CREATE INDEX IF NOT EXISTS memory_items_fts ON memory_items USING fts (title, statement, details, tags);
"""
# NOTE: This FTS index uses Turso/libSQL's FTS5 implementation. It is NOT compatible
# with standard sqlite3's FTS5: the internal schema entries (__turso_internal_fts_dir_*)
# cause "malformed database schema" errors if accessed via `import sqlite3`. Use pyturso
# exclusively for all database access.

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
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT
);
"""

CREATE_RELATIONS = """
CREATE TABLE IF NOT EXISTS memory_relations (
    id TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (from_id, to_id, kind)
);
"""
CREATE_RELATIONS_INDEXES = """
CREATE INDEX IF NOT EXISTS memory_relations_from ON memory_relations (from_id);
CREATE INDEX IF NOT EXISTS memory_relations_to ON memory_relations (to_id);
"""

CREATE_ITEM_INDEXES = """
CREATE INDEX IF NOT EXISTS memory_items_stmt_norm ON memory_items (statement_normalized);
"""

# Locators: indexed retrieval of implementation memories by (path, subject) so
# registration never scans the whole corpus.
CREATE_LOCATORS = """
CREATE TABLE IF NOT EXISTS memory_locators (
    item_id TEXT NOT NULL,
    path TEXT NOT NULL,
    subject TEXT,
    symbol TEXT,
    PRIMARY KEY (item_id, path)
);
"""
CREATE_LOCATORS_INDEXES = """
CREATE INDEX IF NOT EXISTS memory_locators_path_subject ON memory_locators (path, subject);
CREATE INDEX IF NOT EXISTS memory_locators_path_symbol ON memory_locators (path, symbol);
"""

CREATE_META = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

CREATE_OPERATIONS = """
CREATE TABLE IF NOT EXISTS memory_operations (
    op_id TEXT PRIMARY KEY,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

CREATE_HISTORY = """
CREATE TABLE IF NOT EXISTS memory_history (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    event TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    actor TEXT,
    session TEXT,
    source TEXT,
    commit_sha TEXT,
    request_id TEXT,
    timestamp TEXT NOT NULL
);
"""


def get_git_root() -> Path | None:
    """Return git repo root, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def get_db_path(project: str | None = None) -> Path:
    if project:
        return Path(project) / ".totem" / "totem.db"
    git_root = get_git_root()
    if git_root:
        return git_root / ".totem" / "totem.db"
    return Path.cwd() / ".totem" / "totem.db"


def get_user_db_path() -> Path:
    """Global user memory DB; ``TOTEM_USER_DB`` overrides the default location.

    Hosts that keep their data in a volume (Hestia) point this at a persistent
    path, and tests isolate it to a temp directory.
    """
    override = os.environ.get("TOTEM_USER_DB")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "totem" / "totem.db"


def connect(db_path: Path | None = None, project: str | None = None) -> turso.Connection:
    path = db_path or get_db_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = turso.connect(str(path), experimental_features="index_method")
    return conn


@contextmanager
def db_connection(project: str | None = None):
    """Context manager: connect, init schema, auto-init agent config on first use, auto-close."""
    from pathlib import Path

    project_dir = Path(project) if project else get_git_root()
    if project_dir is None:
        project_dir = Path.cwd()

    totem_db = project_dir / ".totem" / "totem.db"
    db_is_new = not totem_db.exists()

    conn = connect(project=project)
    try:
        init_db(conn)
        if db_is_new:
            init_project(project_dir)
    except Exception:
        conn.close()
        raise

    try:
        yield conn
    finally:
        conn.close()


def _has_column(conn: turso.Connection, table: str, name: str) -> bool:
    try:
        row = conn.execute(
            f"SELECT 1 FROM pragma_table_info('{table}') WHERE name = '{name}'"
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _schema_version(conn: turso.Connection) -> int:
    """Current schema version; legacy DBs are inferred from their columns."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is not None:
            return int(row[0])
    except Exception:
        pass
    # DBs created before the meta table: scope marks v3, otherwise v1
    return 3 if _has_column(conn, "memory_items", "scope") else 1


def _set_schema_version(conn: turso.Connection, version: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )


def init_db(conn: turso.Connection) -> None:
    conn.executescript(CREATE_TABLE)
    conn.executescript(CREATE_FTS)
    conn.executescript(CREATE_CONFLICTS)
    conn.executescript(CREATE_HISTORY)
    conn.executescript(CREATE_RELATIONS)
    conn.executescript(CREATE_RELATIONS_INDEXES)
    conn.executescript(CREATE_LOCATORS)
    conn.executescript(CREATE_LOCATORS_INDEXES)
    conn.executescript(CREATE_META)
    conn.executescript(CREATE_OPERATIONS)
    _migrate(conn)
    conn.commit()


@contextmanager
def transaction(conn: turso.Connection):
    """Run a block atomically; commit on success, roll back on any exception.

    Nested calls reuse the outer transaction, so composite operations (create +
    history + relation + status update) commit or fail as one unit. Low-level
    helpers never commit; the semantic operation owns the boundary.
    """
    if getattr(conn, "in_transaction", False):
        yield conn
        return
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def atomic(fn):
    """Decorator: run a tool function inside one transaction (first arg is conn)."""
    import functools

    @functools.wraps(fn)
    def wrapper(conn, *args, **kwargs):
        with transaction(conn):
            return fn(conn, *args, **kwargs)

    return wrapper


def _columns(conn: turso.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _has_index(conn: turso.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _has_table(conn: turso.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _add_columns(
    conn: turso.Connection, table: str, columns: list[tuple[str, str]]
) -> None:
    """Add missing columns. Presence is checked so real failures surface."""
    existing = _columns(conn, table)
    for name, ddl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate_conflicts(conn: turso.Connection) -> None:
    """v2: resolved_at/resolution columns on conflicts."""
    _add_columns(
        conn,
        "conflicts",
        [("resolved_at", "resolved_at TEXT"), ("resolution", "resolution TEXT")],
    )


def _migrate_v3(conn: turso.Connection) -> None:
    """v3: verified_commit and scope columns on memory_items."""
    _add_columns(
        conn,
        "memory_items",
        [("verified_commit", "verified_commit TEXT"), ("scope", "scope TEXT")],
    )


def _migrate_epistemics(conn: turso.Connection) -> None:
    """v4: provenance and applicability columns."""
    _add_columns(
        conn,
        "memory_items",
        [("asserted_by", "asserted_by TEXT"), ("applicability", "applicability TEXT")],
    )


def _migrate_relations(conn: turso.Connection) -> None:
    """v5: dedupe relations, then add unique + lookup indexes."""
    conn.execute(
        "DELETE FROM memory_relations WHERE id NOT IN "
        "(SELECT MIN(id) FROM memory_relations GROUP BY from_id, to_id, kind)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS memory_relations_from ON memory_relations (from_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS memory_relations_to ON memory_relations (to_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS memory_relations_unique "
        "ON memory_relations (from_id, to_id, kind)"
    )


def _normalize_statement(statement: str) -> str:
    return " ".join((statement or "").lower().split())


def _migrate_history_audit(conn: turso.Connection) -> None:
    """v7: actor/session/source/commit/request_id on memory_history."""
    _add_columns(
        conn,
        "memory_history",
        [
            ("actor", "actor TEXT"),
            ("session", "session TEXT"),
            ("source", "source TEXT"),
            ("commit_sha", "commit_sha TEXT"),
            ("request_id", "request_id TEXT"),
        ],
    )


def _migrate_operations(conn: turso.Connection) -> None:
    """v8: idempotency operations table."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_operations ("
        "op_id TEXT PRIMARY KEY, result TEXT NOT NULL, created_at TEXT NOT NULL)"
    )


def _migrate_indexes(conn: turso.Connection) -> None:
    """v6: statement_normalized column + locator table, with backfill."""
    _add_columns(
        conn, "memory_items", [("statement_normalized", "statement_normalized TEXT")]
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_locators ("
        "item_id TEXT NOT NULL, path TEXT NOT NULL, subject TEXT, symbol TEXT, "
        "PRIMARY KEY (item_id, path))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS memory_locators_path_subject "
        "ON memory_locators (path, subject)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS memory_locators_path_symbol "
        "ON memory_locators (path, symbol)"
    )
    for item_id, statement in conn.execute(
        "SELECT id, statement FROM memory_items"
    ).fetchall():
        conn.execute(
            "UPDATE memory_items SET statement_normalized = ? WHERE id = ?",
            (_normalize_statement(statement), item_id),
        )
    for item_id, meta_json in conn.execute(
        "SELECT id, metadata FROM memory_items "
        "WHERE type = 'implementation' AND status != 'deleted'"
    ).fetchall():
        try:
            meta = json.loads(meta_json) if meta_json else {}
        except ValueError:
            meta = {}
        path = meta.get("path")
        if path:
            conn.execute(
                "INSERT OR REPLACE INTO memory_locators (item_id, path, subject, symbol) "
                "VALUES (?, ?, ?, ?)",
                (item_id, path, meta.get("subject"), meta.get("symbol")),
            )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS memory_items_stmt_norm "
        "ON memory_items (statement_normalized)"
    )


MIGRATIONS = [
    (2, _migrate_conflicts),
    (3, _migrate_v3),
    (4, _migrate_epistemics),
    (5, _migrate_relations),
    (6, _migrate_indexes),
    (7, _migrate_history_audit),
    (8, _migrate_operations),
]


def _verify_migration(conn: turso.Connection, target: int) -> None:
    """Postcondition check; raises so a bad migration is never stamped."""
    if target == 2:
        missing = {"resolved_at", "resolution"} - _columns(conn, "conflicts")
    elif target == 3:
        missing = {"verified_commit", "scope"} - _columns(conn, "memory_items")
    elif target == 4:
        missing = {"asserted_by", "applicability"} - _columns(conn, "memory_items")
    elif target == 5:
        missing = (
            set()
            if _has_index(conn, "memory_relations_unique")
            else {"memory_relations_unique"}
        )
    elif target == 6:
        missing = set()
        if "statement_normalized" not in _columns(conn, "memory_items"):
            missing.add("statement_normalized")
        if not _has_index(conn, "memory_items_stmt_norm"):
            missing.add("memory_items_stmt_norm")
        if not _has_table(conn, "memory_locators"):
            missing.add("memory_locators")
    elif target == 7:
        missing = {
            "actor",
            "session",
            "source",
            "commit_sha",
            "request_id",
        } - _columns(conn, "memory_history")
    elif target == 8:
        missing = set() if _has_table(conn, "memory_operations") else {"memory_operations"}
    else:
        missing = set()
    if missing:
        raise RuntimeError(
            f"migration v{target} postcondition failed: missing {sorted(missing)}"
        )


def _migrate(conn: turso.Connection) -> None:
    """Run ordered migrations under a write lock, atomically.

    Commits only after every migration and its postcondition pass; on any
    failure everything rolls back and the schema version is left unchanged, so
    the next run retries cleanly. A no-op when already current.
    """
    version = _schema_version(conn)
    if version >= SCHEMA_VERSION:
        return
    owns = not getattr(conn, "in_transaction", False)
    if owns:
        try:
            conn.execute("BEGIN IMMEDIATE")  # cross-process write lock
        except Exception:
            conn.execute("BEGIN")
    try:
        for target, fn in MIGRATIONS:
            if version < target:
                fn(conn)
                _verify_migration(conn, target)
        _set_schema_version(conn, SCHEMA_VERSION)
    except BaseException:
        if owns:
            conn.rollback()
        raise
    if owns:
        conn.commit()


COLUMNS = [
    "id", "type", "title", "statement", "details", "tags", "status",
    "confidence", "importance", "evidence", "related_memory_ids",
    "created_at", "updated_at", "verified_at", "metadata",
    "schema_version", "verified_commit", "scope", "asserted_by", "applicability",
]
COL_IDX = {name: i for i, name in enumerate(COLUMNS)}
# Explicit column list for SELECTs. NEVER use SELECT * on memory_items:
# fresh CREATE_TABLE puts `scope` mid-table while migrated DBs have it
# appended at the end, so positional reads with SELECT * misalign.
ITEM_COLS = ", ".join(COLUMNS)
ITEM_COLS_M = ", ".join(f"m.{c}" for c in COLUMNS)


def _row_to_item(row: tuple) -> MemoryItem:
    r = COL_IDX
    return MemoryItem(
        id=row[r["id"]],
        type=MemoryType(row[r["type"]]),
        title=row[r["title"]],
        statement=row[r["statement"]],
        details=row[r["details"]],
        tags=json.loads(row[r["tags"]]),
        status=MemoryStatus(row[r["status"]]),
        confidence=row[r["confidence"]],
        importance=row[r["importance"]],
        scope=row[r["scope"]],
        evidence=[Evidence.model_validate(e) for e in json.loads(row[r["evidence"]])],
        related_memory_ids=json.loads(row[r["related_memory_ids"]]),
        created_at=row[r["created_at"]],
        updated_at=row[r["updated_at"]],
        verified_at=row[r["verified_at"]],
        verified_commit=row[r["verified_commit"]],
        asserted_by=row[r["asserted_by"]],
        applicability=row[r["applicability"]],
        metadata=json.loads(row[r["metadata"]]) if row[r["metadata"]] else None,
    )


def insert_item(conn: turso.Connection, item: MemoryItem) -> None:
    conn.execute(
        """INSERT INTO memory_items
           (id, type, title, statement, statement_normalized, details, tags, status,
            confidence, importance, scope, evidence, related_memory_ids, created_at,
            updated_at, verified_at, verified_commit, metadata, schema_version,
            asserted_by, applicability)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item.id,
            item.type.value,
            item.title,
            item.statement,
            _normalize_statement(item.statement),
            item.details,
            json.dumps(item.tags),
            item.status.value,
            item.confidence,
            item.importance,
            item.scope,
            json.dumps([e.model_dump(by_alias=True) for e in item.evidence]),
            json.dumps(item.related_memory_ids),
            item.created_at,
            item.updated_at,
            item.verified_at,
            item.verified_commit,
            json.dumps(item.metadata) if item.metadata else None,
            SCHEMA_VERSION,
            item.asserted_by,
            item.applicability,
        ),
    )


def insert_history(
    conn: turso.Connection,
    item_id: str,
    event: str,
    field: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    reason: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    source: str | None = None,
    commit: str | None = None,
    request_id: str | None = None,
) -> None:
    """Append an immutable history event for audit trail."""
    from datetime import datetime, timezone

    conn.execute(
        """INSERT INTO memory_history
           (id, item_id, event, field, old_value, new_value, reason,
            actor, session, source, commit_sha, request_id, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            item_id,
            event,
            field,
            old_value,
            new_value,
            reason,
            actor,
            session,
            source,
            commit,
            request_id,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def get_item(conn: turso.Connection, item_id: str) -> MemoryItem | None:
    row = conn.execute(
        f"SELECT {ITEM_COLS} FROM memory_items WHERE id = ? AND status != 'deleted'",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_item(row)


def update_item_row(
    conn: turso.Connection,
    item_id: str,
    fields: dict[str, Any],
) -> None:
    if not fields:
        return
    if "statement" in fields:
        fields = {**fields, "statement_normalized": _normalize_statement(fields["statement"])}
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


def soft_delete(conn: turso.Connection, item_id: str) -> None:
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
    conn: turso.Connection,
    type_: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    sort: str = "updated_at",
    limit: int = 50,
    include_children_tags: bool = False,
) -> list[MemoryItem]:
    if sort not in ("created_at", "updated_at", "importance"):
        sort = "updated_at"
    query = f"SELECT {ITEM_COLS} FROM memory_items WHERE status != 'deleted'"
    params: list[Any] = []
    if type_:
        query += " AND type = ?"
        params.append(type_)
    if status:
        query += " AND status = ?"
        params.append(status)
    if tags:
        if include_children_tags:
            # Expand tags to include children: auth → auth OR auth.% 
            conditions = []
            for tag in tags:
                conditions.append("json_each.value = ?")
                params.append(tag)
                conditions.append("json_each.value LIKE ?")
                params.append(f"{tag}.%")
            query += f" AND EXISTS (SELECT 1 FROM json_each(tags) WHERE {' OR '.join(conditions)})"
        else:
            placeholders = ",".join("?" for _ in tags)
            query += f" AND EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value IN ({placeholders}))"
            params.extend(tags)
    query += f" ORDER BY {sort} DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_row_to_item(row) for row in rows]


_FTS_TOKEN_RE = re.compile(r"[\w.-]+")


def _sanitize_fts_query(query: str) -> str:
    """Strip FTS5 operator characters (: " ( ) * etc.) so user input can't break fts_match.
    Sane multi-word queries pass through unchanged (words joined by spaces)."""
    return " ".join(_FTS_TOKEN_RE.findall(query))


def _escape_fts_query(query: str) -> str:
    """Fully escaped form: every token becomes a quoted phrase, ANDed together."""
    tokens = _FTS_TOKEN_RE.findall(query)
    return " AND ".join(f'"{t}"' for t in tokens)


def search_fts(
    conn: turso.Connection,
    query: str,
    types: list[str] | None = None,
    tags: list[str] | None = None,
    include_stale: bool = False,
    limit: int = 20,
) -> list[MemoryItem]:
    sql = f"""
        SELECT {ITEM_COLS_M}, fts_score(m.title, m.statement, m.details, m.tags) AS score
        FROM memory_items m
        WHERE fts_match(m.title, m.statement, m.details, m.tags, ?)
          AND m.status != 'deleted'
    """
    params: list[Any] = [query]
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
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)
    sanitized = _sanitize_fts_query(query)
    if not sanitized:
        return []
    try:
        rows = conn.execute(sql, [sanitized] + params[1:]).fetchall()
    except Exception:
        escaped = _escape_fts_query(query)
        if not escaped or escaped == sanitized:
            raise
        rows = conn.execute(sql, [escaped] + params[1:]).fetchall()
    return [_row_to_item(row) for row in rows]


def get_overlapping_items(
    conn: turso.Connection,
    type_: str,
    path: str,
    start_line: int,
    end_line: int,
) -> list[MemoryItem]:
    """Find active items of the same type with overlapping evidence ranges."""
    rows = conn.execute(
        f"""SELECT {ITEM_COLS} FROM memory_items
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


CONFLICT_COLUMNS = [
    "id", "item_a", "item_b", "claim_a", "claim_b", "condition",
    "resolution_options", "recommended", "created_at",
]
CONFLICT_COL_IDX = {name: i for i, name in enumerate(CONFLICT_COLUMNS)}
# Explicit column list, safe prefix even on migrated DBs (resolved_at/resolution
# are appended at the end). NEVER use SELECT * on conflicts.
CONFLICT_COLS = ", ".join(CONFLICT_COLUMNS)


def insert_conflict(conn: turso.Connection, conflict: Conflict) -> None:
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


def _conflict_from_row(row) -> Conflict:
    return Conflict(
        itemA=row[CONFLICT_COL_IDX["item_a"]],
        itemB=row[CONFLICT_COL_IDX["item_b"]],
        claimA=row[CONFLICT_COL_IDX["claim_a"]],
        claimB=row[CONFLICT_COL_IDX["claim_b"]],
        condition=row[CONFLICT_COL_IDX["condition"]],
        resolutionOptions=json.loads(row[CONFLICT_COL_IDX["resolution_options"]]),
        recommended=row[CONFLICT_COL_IDX["recommended"]],
    )


def get_conflicts_for_item(conn: turso.Connection, item_id: str) -> list[Conflict]:
    """Retrieve all conflicts involving a given item."""
    rows = conn.execute(
        f"SELECT {CONFLICT_COLS} FROM conflicts WHERE item_a = ? OR item_b = ?",
        (item_id, item_id),
    ).fetchall()
    return [_conflict_from_row(row) for row in rows]


def get_open_conflicts(conn: turso.Connection) -> list[Conflict]:
    """Unresolved conflicts: what the context compiler should surface."""
    rows = conn.execute(
        f"SELECT {CONFLICT_COLS} FROM conflicts WHERE resolved_at IS NULL"
    ).fetchall()
    return [_conflict_from_row(row) for row in rows]


def get_all_conflicts(conn: turso.Connection) -> list[Conflict]:
    """All stored conflicts, including resolved ones (audit/export)."""
    rows = conn.execute(f"SELECT {CONFLICT_COLS} FROM conflicts").fetchall()
    return [_conflict_from_row(row) for row in rows]


def resolve_conflict(
    conn: turso.Connection, conflict_id: str, resolution: str
) -> dict | None:
    """Mark a conflict as resolved (§45)."""
    from datetime import datetime, timezone

    row = conn.execute(
        "SELECT id FROM conflicts WHERE id = ?", (conflict_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE conflicts SET resolved_at = ?, resolution = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), resolution, conflict_id),
    )
    return {"id": conflict_id, "resolution": resolution, "resolved": True}


def get_all_items(conn: turso.Connection) -> list[MemoryItem]:
    """Return all non-deleted memory items."""
    rows = conn.execute(
        f"SELECT {ITEM_COLS} FROM memory_items WHERE status != 'deleted'"
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def import_items(conn: turso.Connection, items: list[dict]) -> dict:
    """Import memory items, skipping duplicates by ID (including soft-deleted rows).
    One bad item never aborts the import; it is counted as skipped."""
    imported = 0
    skipped = 0
    for item_dict in items:
        try:
            row = conn.execute(
                "SELECT id FROM memory_items WHERE id = ?",
                (item_dict["id"],),
            ).fetchone()
            if row is not None:
                skipped += 1
                continue
            item = MemoryItem.model_validate(item_dict)
            insert_item(conn, item)
            imported += 1
        except Exception:
            skipped += 1
    return {"imported": imported, "skipped": skipped}


def find_by_title(conn: turso.Connection, title: str) -> MemoryItem | None:
    """Find an active memory item by exact title match."""
    row = conn.execute(
        f"SELECT {ITEM_COLS} FROM memory_items WHERE title = ? AND status != 'deleted' LIMIT 1",
        (title,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_item(row)


def find_by_statement_normalized(
    conn: turso.Connection, statement: str
) -> MemoryItem | None:
    """Find a non-deleted item whose normalized statement matches (density dedup)."""
    row = conn.execute(
        f"SELECT {ITEM_COLS} FROM memory_items "
        "WHERE statement_normalized = ? AND status != 'deleted' LIMIT 1",
        (_normalize_statement(statement),),
    ).fetchone()
    return _row_to_item(row) if row is not None else None


def upsert_locator(
    conn: turso.Connection,
    item_id: str,
    path: str,
    subject: str | None = None,
    symbol: str | None = None,
) -> None:
    """Index an implementation memory by (path, subject) for O(1) lookup."""
    conn.execute(
        "INSERT OR REPLACE INTO memory_locators (item_id, path, subject, symbol) "
        "VALUES (?, ?, ?, ?)",
        (item_id, path, subject, symbol),
    )


def find_locator_item(conn: turso.Connection, path: str, subject: str) -> str | None:
    """item_id for an implementation memory at (path, subject), or None."""
    row = conn.execute(
        "SELECT item_id FROM memory_locators WHERE path = ? AND subject = ? LIMIT 1",
        (path, subject),
    ).fetchone()
    return row[0] if row is not None else None


def find_locator_item_by_path(conn: turso.Connection, path: str) -> str | None:
    """item_id for the implementation memory at path (write path), or None."""
    row = conn.execute(
        "SELECT item_id FROM memory_locators WHERE path = ? LIMIT 1", (path,)
    ).fetchone()
    return row[0] if row is not None else None


def find_same_title_different_statement(
    conn: turso.Connection, title: str, statement: str, item_type: str, exclude_id: str
) -> list[MemoryItem]:
    """Find active items with same title but different statement (conceptual contradiction)."""
    rows = conn.execute(
        f"""SELECT {ITEM_COLS} FROM memory_items
           WHERE title = ? AND type = ? AND status = 'active'
           AND id != ? AND statement != ?""",
        (title, item_type, exclude_id, statement),
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def list_task_items(conn: turso.Connection, limit: int = 10) -> list[MemoryItem]:
    """Find items with any tag starting with 'task:'."""
    rows = conn.execute(
        f"""SELECT {ITEM_COLS} FROM memory_items
           WHERE status != 'deleted'
           AND EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value LIKE 'task:%')
           ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def list_command_items(conn: turso.Connection, limit: int = 20) -> list[MemoryItem]:
    """Find gotcha items with 'cmd:' tag prefix (command outcomes)."""
    rows = conn.execute(
        f"""SELECT {ITEM_COLS} FROM memory_items
           WHERE status != 'deleted' AND type = 'gotcha'
           AND EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value LIKE 'cmd:%')
           ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def init_project(project_dir: Path) -> dict:
    """Initialize .totem/ directory, append agent config files, return status."""
    import shutil

    totem_dir = project_dir / ".totem"
    db_path = totem_dir / "totem.db"
    already_existed = db_path.exists()
    totem_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    init_db(conn)
    conn.close()

    agent_config_dir = Path(__file__).parent / "agent_config"
    copied = []

    if agent_config_dir.exists():
        agents_src = agent_config_dir / "AGENTS.md"
        skill_src = agent_config_dir / "SKILL.md"

        # opencode: append to ~/.config/opencode/AGENTS.md, create SKILL.md
        opencode_dir = Path.home() / ".config" / "opencode"
        if agents_src.exists():
            opencode_agents = opencode_dir / "AGENTS.md"
            opencode_dir.mkdir(parents=True, exist_ok=True)
            src_content = agents_src.read_text()
            if opencode_agents.exists():
                dst_content = opencode_agents.read_text()
                if "## totem Memory System" not in dst_content:
                    opencode_agents.write_text(dst_content.rstrip() + "\n\n" + src_content)
                    copied.append("opencode: AGENTS.md (appended)")
                else:
                    copied.append("opencode: AGENTS.md (already present)")
            else:
                shutil.copy2(agents_src, opencode_agents)
                copied.append("opencode: AGENTS.md (created)")
        if skill_src.exists():
            skills_dir = opencode_dir / "skills" / "totem"
            skills_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(skill_src, skills_dir / "SKILL.md")
            copied.append("opencode: skills/totem/SKILL.md")

        # Claude Code: append to project root AGENTS.md
        if agents_src.exists():
            claude_agents = project_dir / "AGENTS.md"
            src_content = agents_src.read_text()
            if claude_agents.exists():
                dst_content = claude_agents.read_text()
                if "## totem Memory System" not in dst_content:
                    claude_agents.write_text(dst_content.rstrip() + "\n\n" + src_content)
                    copied.append("claude: AGENTS.md (appended)")
                else:
                    copied.append("claude: AGENTS.md (already present)")
            else:
                shutil.copy2(agents_src, claude_agents)
                copied.append("claude: AGENTS.md (created)")

    return {
        "path": str(totem_dir),
        "db": str(db_path),
        "already_existed": already_existed,
        "agent_config_copied": copied or None,
    }


RELATION_KINDS = (
    "supersedes",
    "contradicts",
    "invalidates",
    "derived_from",
    "verified_by",
    "refines",
    "depends_on",
)


def _relation_reaches(
    conn: turso.Connection, start_id: str, target_id: str, kind: str, _seen: set | None = None
) -> bool:
    """True if following ``kind`` edges from start_id reaches target_id."""
    if _seen is None:
        _seen = set()
    if start_id == target_id:
        return True
    if start_id in _seen:
        return False
    _seen.add(start_id)
    rows = conn.execute(
        "SELECT to_id FROM memory_relations WHERE from_id = ? AND kind = ?",
        (start_id, kind),
    ).fetchall()
    return any(_relation_reaches(conn, r[0], target_id, kind, _seen) for r in rows)


def insert_relation(
    conn: turso.Connection, from_id: str, to_id: str, kind: str
) -> dict:
    """Create a typed relation between two memories.

    Idempotent for an existing (from, to, kind) triple. Rejects self-relations,
    dangling endpoints, and cycles for state-changing kinds (supersedes,
    invalidates). libSQL does not enforce foreign keys by default, so endpoints
    are validated here.
    """
    from datetime import datetime, timezone

    if kind not in RELATION_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(RELATION_KINDS)}")
    if from_id == to_id:
        raise ValueError("a memory cannot relate to itself")
    for endpoint in (from_id, to_id):
        row = conn.execute(
            "SELECT id FROM memory_items WHERE id = ? AND status != 'deleted'",
            (endpoint,),
        ).fetchone()
        if row is None:
            raise ValueError(f"relation endpoint not found: {endpoint}")
    existing = conn.execute(
        "SELECT id, from_id, to_id, kind FROM memory_relations "
        "WHERE from_id = ? AND to_id = ? AND kind = ?",
        (from_id, to_id, kind),
    ).fetchone()
    if existing is not None:
        return {
            "id": existing[0],
            "from_id": existing[1],
            "to_id": existing[2],
            "kind": existing[3],
        }
    if kind in ("supersedes", "invalidates") and _relation_reaches(conn, to_id, from_id, kind):
        raise ValueError(f"'{kind}' relation would create a cycle")
    relation_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO memory_relations (id, from_id, to_id, kind, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (relation_id, from_id, to_id, kind, datetime.now(timezone.utc).isoformat()),
    )
    return {"id": relation_id, "from_id": from_id, "to_id": to_id, "kind": kind}


def _relation_from_row(row) -> dict:
    return {
        "id": row[0],
        "from_id": row[1],
        "to_id": row[2],
        "kind": row[3],
        "created_at": row[4],
    }


def get_relations_for_item(conn: turso.Connection, item_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, from_id, to_id, kind, created_at FROM memory_relations "
        "WHERE from_id = ? OR to_id = ?",
        (item_id, item_id),
    ).fetchall()
    return [_relation_from_row(r) for r in rows]


def get_relations_for_items(conn: turso.Connection, item_ids: list[str]) -> list[dict]:
    if not item_ids:
        return []
    placeholders = ", ".join("?" for _ in item_ids)
    rows = conn.execute(
        f"SELECT id, from_id, to_id, kind, created_at FROM memory_relations "
        f"WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
        (*item_ids, *item_ids),
    ).fetchall()
    return [_relation_from_row(r) for r in rows]


def get_all_relations(conn: turso.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, from_id, to_id, kind, created_at FROM memory_relations"
    ).fetchall()
    return [_relation_from_row(r) for r in rows]


def get_history(conn: turso.Connection, item_id: str) -> list[dict]:
    """Immutable history events for one memory, oldest first."""
    rows = conn.execute(
        "SELECT id, item_id, event, field, old_value, new_value, reason, "
        "actor, session, source, commit_sha, request_id, timestamp "
        "FROM memory_history WHERE item_id = ? ORDER BY timestamp, id",
        (item_id,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "item_id": r[1],
            "event": r[2],
            "field": r[3],
            "old_value": r[4],
            "new_value": r[5],
            "reason": r[6],
            "actor": r[7],
            "session": r[8],
            "source": r[9],
            "commit": r[10],
            "request_id": r[11],
            "timestamp": r[12],
        }
        for r in rows
    ]


def get_operation(conn: turso.Connection, op_id: str) -> dict | None:
    """Stored result for an idempotent operation, or None."""
    row = conn.execute(
        "SELECT result FROM memory_operations WHERE op_id = ?", (op_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def put_operation(conn: turso.Connection, op_id: str, result) -> None:
    """Record an operation's result so a replay returns it instead of redoing."""
    from datetime import datetime, timezone

    conn.execute(
        "INSERT OR REPLACE INTO memory_operations (op_id, result, created_at) "
        "VALUES (?, ?, ?)",
        (op_id, json.dumps(result, default=str), datetime.now(timezone.utc).isoformat()),
    )
