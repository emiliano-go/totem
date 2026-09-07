"""High-level tool functions for srcmem (§43)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .conflicts import detect_conflicts
from .db import (
    get_item,
    get_overlapping_items,
    init_db,
    insert_item,
    list_items,
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
    conn: sqlite3.Connection,
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
    conn: sqlite3.Connection,
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
    conn: sqlite3.Connection,
    id: str,
    reason: str,
    title: str | None = None,
    statement: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    confidence: float | None = None,
    importance: float | None = None,
    evidence: list[dict] | None = None,
    metadata: dict | None = None,
) -> dict | None:
    """Update a memory item (§43 memory_update). reason is required."""
    if not reason:
        raise ValueError("reason is required for updates (§3)")

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
    print(f"[srcmem] Update {id}: {reason}")
    updated = get_item(conn, id)
    return updated.model_dump(by_alias=True) if updated else None
