"""High-level tool functions for totem (§43)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import turso

from .conflicts import detect_conflicts
from .db import (
    get_all_conflict_rows,
    get_all_conflicts,
    get_all_items,
    get_item,
    get_overlapping_items,
    import_items as db_import_items,
    init_db,
    insert_conflict,
    insert_item,
    list_items,
    resolve_conflict as db_resolve_conflict,
    search_fts,
    soft_delete,
    update_item_row,
)
from .hashing import check_staleness, hash_content
from .models import (
    Conflict,
    Evidence,
    MemoryItem,
    MemoryStatus,
    MemoryType,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def memory_create(
    conn: turso.Connection,
    type: str,
    title: str,
    statement: str,
    tags: list[str],
    details: str | None = None,
    confidence: float = 1.0,
    importance: float = 0.5,
    evidence: list[dict] | None = None,
    related_memory_ids: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Create a new memory item (§43 memory_create)."""
    if not type or not title or not statement:
        raise ValueError("type, title, and statement are required")
    if not tags:
        raise ValueError("At least one tag is required")
    if not (0 <= confidence <= 1):
        raise ValueError("confidence must be in [0, 1]")
    if not (0 <= importance <= 1):
        raise ValueError("importance must in [0, 1]")

    mem_type = MemoryType(type)
    if mem_type == MemoryType.INVARIANT:
        if not metadata or "verificationMethod" not in metadata:
            raise ValueError(
                "Invariant items require 'verificationMethod' in metadata (§41)"
            )

    if mem_type == MemoryType.DECISION:
        if not metadata:
            metadata = {}
        if "rationale" not in metadata:
            metadata["rationale"] = "see statement"

    parsed_evidence = [Evidence.model_validate(e) for e in (evidence or [])]

    item = MemoryItem(
        type=mem_type,
        title=title,
        statement=statement,
        details=details,
        tags=tags,
        confidence=confidence,
        importance=importance,
        evidence=parsed_evidence,
        related_memory_ids=related_memory_ids or [],
        metadata=metadata,
    )

    conflicts = detect_conflicts(conn, item)
    insert_item(conn, item)

    warnings = []
    for c in conflicts:
        warnings.append(
            f"Conflict with {c.item_b}: {c.claim_a} vs {c.claim_b}: {c.condition}"
        )

    return {
        "id": item.id,
        "status": item.status.value,
        "warnings": warnings or None,
        "conflicts": [c.model_dump(by_alias=True) for c in conflicts] or None,
    }


def memory_get(
    conn: turso.Connection,
    id: str,
    include_evidence: bool = True,
) -> dict | None:
    """Retrieve a memory item with staleness check (§43 memory_get)."""
    item = get_item(conn, id)
    if item is None:
        return None

    warnings: list[str] = []
    if include_evidence:
        stale_evidence = []
        for ev in item.evidence:
            if check_staleness(
                Path(ev.path), ev.start_line, ev.end_line, ev.content_hash
            ):
                stale_evidence.append(ev)
                warnings.append(
                    f"Evidence stale: {ev.path}:{ev.start_line}-{ev.end_line}"
                )
        if stale_evidence and item.status == MemoryStatus.ACTIVE:
            update_item_row(
                conn,
                id,
                {
                    "status": MemoryStatus.POTENTIALLY_STALE.value,
                    "updated_at": _now(),
                },
            )
            item.status = MemoryStatus.POTENTIALLY_STALE

    result = item.model_dump(by_alias=True)
    if warnings:
        result["warnings"] = warnings
    return result


def memory_update(
    conn: turso.Connection,
    id: str,
    reason: str | None = None,
    title: str | None = None,
    statement: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    confidence: float | None = None,
    importance: float | None = None,
    evidence: list[dict] | None = None,
    metadata: dict | None = None,
) -> dict | None:
    """Update a memory item (§43 memory_update). reason is optional (defaults to 'maintenance')."""
    if not reason:
        reason = "maintenance"

    item = get_item(conn, id)
    if item is None:
        return None

    fields: dict = {"updated_at": _now()}
    if title is not None:
        fields["title"] = title
    if statement is not None:
        fields["statement"] = statement
    if tags is not None:
        if not tags:
            raise ValueError("At least one tag is required")
        fields["tags"] = str(tags) if isinstance(tags, str) else json.dumps(tags)
    if status is not None:
        fields["status"] = status
    if confidence is not None:
        if not (0 <= confidence <= 1):
            raise ValueError("confidence must be in [0, 1]")
        fields["confidence"] = confidence
    if importance is not None:
        if not (0 <= importance <= 1):
            raise ValueError("importance must be in [0, 1]")
        fields["importance"] = importance
    if evidence is not None:
        parsed = [Evidence.model_validate(e) for e in evidence]
        fields["evidence"] = json.dumps(
            [e.model_dump(by_alias=True) for e in parsed]
        )
    if metadata is not None:
        fields["metadata"] = json.dumps(metadata)

    update_item_row(conn, id, fields)
    print(f"[totem] Update {id}: {reason}")
    updated = get_item(conn, id)
    return updated.model_dump(by_alias=True) if updated else None


def memory_delete(conn: turso.Connection, id: str, reason: str) -> dict:
    """Soft-delete a memory item (§43 memory_delete). reason is required."""
    if not reason:
        raise ValueError("reason is required for deletion (§3)")
    item = get_item(conn, id)
    if item is None:
        return {"error": f"Item {id} not found"}
    soft_delete(conn, id)
    print(f"[totem] Deleted {id}: {reason}")
    return {"id": id, "status": "deleted"}


def memory_list(
    conn: turso.Connection,
    type: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    sort: str = "updated_at",
    limit: int = 50,
) -> list[dict]:
    """List memory items with optional filters (§43 memory_list)."""
    items = list_items(conn, type_=type, tags=tags, status=status, sort=sort, limit=limit)
    return [item.model_dump(by_alias=True) for item in items]


def memory_recent(
    conn: turso.Connection,
    limit: int = 5,
) -> list[dict]:
    """List most recently created memories (§43 memory_recent)."""
    return memory_list(conn, sort="created_at", limit=limit)


def resolve_conflict(
    conn: turso.Connection,
    conflict_id: str,
    resolution: str,
) -> dict | None:
    """Mark a conflict as resolved (§45)."""
    return db_resolve_conflict(conn, conflict_id, resolution)


def memory_export(conn: turso.Connection) -> dict:
    """Export all memories and conflicts as a portable dict."""
    from .db import SCHEMA_VERSION

    items = get_all_items(conn)
    conflicts = get_all_conflict_rows(conn)
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": _now(),
        "items": [item.model_dump(by_alias=True) for item in items],
        "conflicts": conflicts,
    }


def memory_import(conn: turso.Connection, data: dict) -> dict:
    """Import memories from an export dict. Skips duplicate IDs."""
    items = data.get("items", [])
    result = db_import_items(conn, items)
    return {
        "imported": result["imported"],
        "skipped": result["skipped"],
        "total_items": len(items),
    }


def memory_search(
    conn: turso.Connection,
    query: str,
    types: list[str] | None = None,
    tags: list[str] | None = None,
    include_stale: bool = False,
    limit: int = 20,
) -> list[dict]:
    """Hybrid tag + full-text search (§43 memory_search)."""
    items = search_fts(conn, query, types=types, tags=tags, include_stale=include_stale, limit=limit)
    results = []
    for item in items:
        warnings: list[str] = []
        for ev in item.evidence:
            if check_staleness(Path(ev.path), ev.start_line, ev.end_line, ev.content_hash):
                warnings.append(f"Evidence stale: {ev.path}:{ev.start_line}-{ev.end_line}")
        d = item.model_dump(by_alias=True)
        if warnings:
            d["warnings"] = warnings
        results.append(d)
    return results
