"""Turso storage layer for totem."""

from __future__ import annotations

import json
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import turso

from .models import Conflict, Evidence, MemoryItem, MemoryStatus, MemoryType

SCHEMA_VERSION = 3

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
    scope TEXT,
    evidence TEXT NOT NULL DEFAULT '[]',
    related_memory_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    verified_at TEXT,
    verified_commit TEXT,
    metadata TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);
"""

CREATE_FTS = """
CREATE INDEX IF NOT EXISTS memory_items_fts ON memory_items USING fts (title, statement, details, tags);
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
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT
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
    return Path.home() / ".local" / "share" / "totem" / "totem.db"


def connect(db_path: Path | None = None, project: str | None = None) -> turso.Connection:
    path = db_path or get_db_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = turso.connect(str(path), experimental_features="index_method")
    return conn


@contextmanager
def db_connection(project: str | None = None):
    """Context manager: connect, init schema, auto-init agent config on first use, auto-close."""
    conn = connect(project=project)
    init_db(conn)

    # Auto-init agent config files on first use (if .totem/ is new)
    from pathlib import Path
    project_dir = Path(project) if project else get_git_root()
    totem_db = project_dir / ".totem" / "totem.db"
    if not totem_db.exists():
        init_project(project_dir)

    try:
        yield conn
    finally:
        conn.close()


def init_db(conn: turso.Connection) -> None:
    conn.executescript(CREATE_TABLE)
    conn.executescript(CREATE_FTS)
    conn.executescript(CREATE_CONFLICTS)
    conn.executescript(CREATE_HISTORY)
    _migrate_conflicts(conn)
    _migrate_verified_commit(conn)
    _migrate_scope(conn)
    conn.commit()


def _migrate_conflicts(conn: turso.Connection) -> None:
    """Add resolved_at/resolution columns to existing conflicts tables."""
    for col in ("resolved_at TEXT", "resolution TEXT"):
        try:
            conn.execute(f"ALTER TABLE conflicts ADD COLUMN {col}")
        except Exception:
            pass  # column already exists


def _migrate_verified_commit(conn: turso.Connection) -> None:
    """Add verified_commit column to existing memory_items tables."""
    try:
        conn.execute("ALTER TABLE memory_items ADD COLUMN verified_commit TEXT")
    except Exception:
        pass  # column already exists


def _migrate_scope(conn: turso.Connection) -> None:
    """Add scope column to existing memory_items tables."""
    try:
        conn.execute("ALTER TABLE memory_items ADD COLUMN scope TEXT")
    except Exception:
        pass  # column already exists


COLUMNS = [
    "id", "type", "title", "statement", "details", "tags", "status",
    "confidence", "importance", "evidence", "related_memory_ids",
    "created_at", "updated_at", "verified_at", "metadata",
    "schema_version", "verified_commit", "scope",
]
COL_IDX = {name: i for i, name in enumerate(COLUMNS)}


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
        metadata=json.loads(row[r["metadata"]]) if row[r["metadata"]] else None,
    )


def insert_item(conn: turso.Connection, item: MemoryItem) -> None:
    conn.execute(
        """INSERT INTO memory_items
           (id, type, title, statement, details, tags, status, confidence,
            importance, scope, evidence, related_memory_ids, created_at, updated_at,
            verified_at, verified_commit, metadata, schema_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            item.scope,
            json.dumps([e.model_dump(by_alias=True) for e in item.evidence]),
            json.dumps(item.related_memory_ids),
            item.created_at,
            item.updated_at,
            item.verified_at,
            item.verified_commit,
            json.dumps(item.metadata) if item.metadata else None,
            SCHEMA_VERSION,
        ),
    )
    conn.commit()


def insert_history(
    conn: turso.Connection,
    item_id: str,
    event: str,
    field: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    reason: str | None = None,
) -> None:
    """Append an immutable history event for audit trail."""
    from datetime import datetime, timezone

    conn.execute(
        """INSERT INTO memory_history
           (id, item_id, event, field, old_value, new_value, reason, timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            item_id,
            event,
            field,
            old_value,
            new_value,
            reason,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def expand_tags(tags: list[str]) -> list[str]:
    """Expand tags to include hierarchical children. auth → [auth, auth.*, auth.*.*]."""
    expanded = set()
    for tag in tags:
        expanded.add(tag)
        # Add wildcard pattern for children
        expanded.add(f"{tag}.%")
    return list(expanded)


def get_item(conn: turso.Connection, item_id: str) -> MemoryItem | None:
    row = conn.execute(
        "SELECT * FROM memory_items WHERE id = ? AND status != 'deleted'",
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
    query = "SELECT * FROM memory_items WHERE status != 'deleted'"
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


def search_fts(
    conn: turso.Connection,
    query: str,
    types: list[str] | None = None,
    tags: list[str] | None = None,
    include_stale: bool = False,
    limit: int = 20,
) -> list[MemoryItem]:
    sql = """
        SELECT m.*, fts_score(m.title, m.statement, m.details, m.tags) AS score
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
    rows = conn.execute(sql, params).fetchall()
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


CONFLICT_COLUMNS = [
    "id", "item_a", "item_b", "claim_a", "claim_b", "condition",
    "resolution_options", "recommended", "created_at",
]
CONFLICT_COL_IDX = {name: i for i, name in enumerate(CONFLICT_COLUMNS)}


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
    conn.commit()


def get_conflicts_for_item(conn: turso.Connection, item_id: str) -> list[Conflict]:
    """Retrieve all conflicts involving a given item."""
    rows = conn.execute(
        "SELECT * FROM conflicts WHERE item_a = ? OR item_b = ?",
        (item_id, item_id),
    ).fetchall()
    return [
        Conflict(
            itemA=row[CONFLICT_COL_IDX["item_a"]],
            itemB=row[CONFLICT_COL_IDX["item_b"]],
            claimA=row[CONFLICT_COL_IDX["claim_a"]],
            claimB=row[CONFLICT_COL_IDX["claim_b"]],
            condition=row[CONFLICT_COL_IDX["condition"]],
            resolutionOptions=json.loads(row[CONFLICT_COL_IDX["resolution_options"]]),
            recommended=row[CONFLICT_COL_IDX["recommended"]],
        )
        for row in rows
    ]


def get_all_conflicts(conn: turso.Connection) -> list[Conflict]:
    """Retrieve all stored conflicts."""
    rows = conn.execute("SELECT * FROM conflicts").fetchall()
    return [
        Conflict(
            itemA=row[CONFLICT_COL_IDX["item_a"]],
            itemB=row[CONFLICT_COL_IDX["item_b"]],
            claimA=row[CONFLICT_COL_IDX["claim_a"]],
            claimB=row[CONFLICT_COL_IDX["claim_b"]],
            condition=row[CONFLICT_COL_IDX["condition"]],
            resolutionOptions=json.loads(row[CONFLICT_COL_IDX["resolution_options"]]),
            recommended=row[CONFLICT_COL_IDX["recommended"]],
        )
        for row in rows
    ]


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
    conn.commit()
    return {"id": conflict_id, "resolution": resolution, "resolved": True}


def get_all_items(conn: turso.Connection) -> list[MemoryItem]:
    """Return all non-deleted memory items."""
    rows = conn.execute(
        "SELECT * FROM memory_items WHERE status != 'deleted'"
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def import_items(conn: turso.Connection, items: list[dict]) -> dict:
    """Import memory items, skipping duplicates by ID."""
    imported = 0
    skipped = 0
    for item_dict in items:
        existing = get_item(conn, item_dict["id"])
        if existing is not None:
            skipped += 1
            continue
        item = MemoryItem.model_validate(item_dict)
        insert_item(conn, item)
        imported += 1
    return {"imported": imported, "skipped": skipped}


def find_by_title(conn: turso.Connection, title: str) -> MemoryItem | None:
    """Find an active memory item by exact title match."""
    row = conn.execute(
        "SELECT * FROM memory_items WHERE title = ? AND status != 'deleted' LIMIT 1",
        (title,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_item(row)


def find_same_title_different_statement(
    conn: turso.Connection, title: str, statement: str, item_type: str, exclude_id: str
) -> list[MemoryItem]:
    """Find active items with same title but different statement (conceptual contradiction)."""
    rows = conn.execute(
        """SELECT * FROM memory_items
           WHERE title = ? AND type = ? AND status = 'active'
           AND id != ? AND statement != ?""",
        (title, item_type, exclude_id, statement),
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def list_task_items(conn: turso.Connection, limit: int = 10) -> list[MemoryItem]:
    """Find items with any tag starting with 'task:'."""
    rows = conn.execute(
        """SELECT * FROM memory_items
           WHERE status != 'deleted'
           AND EXISTS (SELECT 1 FROM json_each(tags) WHERE json_each.value LIKE 'task:%')
           ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def list_command_items(conn: turso.Connection, limit: int = 20) -> list[MemoryItem]:
    """Find gotcha items with 'cmd:' tag prefix (command outcomes)."""
    rows = conn.execute(
        """SELECT * FROM memory_items
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
