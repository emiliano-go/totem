"""High-level tool functions for totem (§43)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import turso

from .conflicts import detect_conflicts
from .db import (
    find_by_title,
    get_all_conflicts,
    get_all_items,
    get_item,
    get_overlapping_items,
    import_items as db_import_items,
    init_db,
    insert_conflict,
    insert_history,
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


def _normalize_tags(tags: list[str]) -> list[str]:
    """Normalize tags: lowercase, trim, spaces → hyphens."""
    return [t.strip().lower().replace(" ", "-") for t in tags if t.strip()]


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

    tags = _normalize_tags(tags)
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

    # Dedup: warn if title already exists
    existing = find_by_title(conn, title)
    dedup_warning = None
    if existing:
        dedup_warning = (
            f"Memory with title '{title}' already exists (id={existing.id}, "
            f"type={existing.type.value}). Consider updating instead."
        )

    insert_item(conn, item)
    insert_history(conn, item.id, "created")

    warnings = []
    for c in conflicts:
        warnings.append(
            f"Conflict with {c.item_b}: {c.claim_a} vs {c.claim_b}: {c.condition}"
        )
    if dedup_warning:
        warnings.append(dedup_warning)

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
    details: str | None = None,
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
    changes: list[tuple[str, str | None, str | None]] = []  # (field, old, new)
    if title is not None:
        changes.append(("title", item.title, title))
        fields["title"] = title
    if statement is not None:
        changes.append(("statement", item.statement, statement))
        fields["statement"] = statement
    if details is not None:
        changes.append(("details", item.details, details))
        fields["details"] = details
    if tags is not None:
        if not tags:
            raise ValueError("At least one tag is required")
        tags = _normalize_tags(tags)
        old_tags = json.dumps(item.tags) if item.tags else "[]"
        changes.append(("tags", old_tags, json.dumps(tags)))
        fields["tags"] = json.dumps(tags)
    if status is not None:
        changes.append(("status", item.status.value if item.status else None, status))
        fields["status"] = status
    if confidence is not None:
        if not (0 <= confidence <= 1):
            raise ValueError("confidence must be in [0, 1]")
        changes.append(("confidence", str(item.confidence), str(confidence)))
        fields["confidence"] = confidence
    if importance is not None:
        if not (0 <= importance <= 1):
            raise ValueError("importance must be in [0, 1]")
        changes.append(("importance", str(item.importance), str(importance)))
        fields["importance"] = importance
    if evidence is not None:
        parsed = [Evidence.model_validate(e) for e in evidence]
        old_evidence = json.dumps([e.model_dump(by_alias=True) for e in item.evidence]) if item.evidence else "[]"
        new_evidence = json.dumps([e.model_dump(by_alias=True) for e in parsed])
        changes.append(("evidence", old_evidence, new_evidence))
        fields["evidence"] = new_evidence
    if metadata is not None:
        old_meta = json.dumps(item.metadata) if item.metadata else None
        # Bug state machine enforcement: validate transitions on update
        if item.type == MemoryType.BUG and metadata.get("state"):
            old_state = (item.metadata or {}).get("state")
            new_state = metadata["state"]
            if old_state and old_state != new_state:
                from .models import _BUG_TRANSITIONS
                allowed = _BUG_TRANSITIONS.get(old_state, set())
                if new_state not in allowed:
                    raise ValueError(
                        f"Bug state transition '{old_state}' → '{new_state}' not allowed. "
                        f"Valid transitions: {old_state} → {', '.join(sorted(allowed)) or '(none)'}"
                    )
        changes.append(("metadata", old_meta, json.dumps(metadata)))
        fields["metadata"] = json.dumps(metadata)

    update_item_row(conn, id, fields)
    insert_history(conn, id, "updated", reason=reason)
    for field_name, old_val, new_val in changes:
        insert_history(conn, id, "updated", field=field_name, old_value=old_val, new_value=new_val, reason=reason)
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
    insert_history(conn, id, "deleted", reason=reason)
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
    conflicts = get_all_conflicts(conn)
    return {
        "schema_version": SCHEMA_VERSION,
        "exported_at": _now(),
        "items": [item.model_dump(by_alias=True) for item in items],
        "conflicts": [c.model_dump(by_alias=True) for c in conflicts],
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


def totem_init(project: str | None = None) -> dict:
    """Initialize totem for a project: create .totem/, ensure DB exists, return status."""
    from .db import init_project, get_db_path

    db_path = get_db_path(project)
    project_dir = db_path.parent.parent
    return init_project(project_dir)


def register_file_read(
    conn: turso.Connection,
    path: str,
    statement: str,
    subject: str,
    kind: str,
    tags: list[str],
    start_line: int | None = None,
    end_line: int | None = None,
    title: str | None = None,
    details: str | None = None,
) -> dict:
    """Register facts learned from reading a file. Updates existing memory for same path, or creates new."""
    file_path = Path(path)
    if not file_path.is_file():
        return {"error": f"File not found: {path}"}

    # Read file and compute hash
    try:
        content = file_path.read_text(encoding="utf-8")
    except (PermissionError, UnicodeDecodeError) as e:
        return {"error": f"Cannot read {path}: {e}"}

    lines = content.splitlines(keepends=True)
    actual_start = start_line if start_line and start_line >= 1 else 1
    actual_end = end_line if end_line and end_line <= len(lines) else len(lines)
    if actual_start > actual_end:
        return {"error": f"Invalid line range: {start_line}-{end_line}"}

    range_content = "".join(lines[actual_start - 1 : actual_end])
    content_hash = hash_content(range_content)

    # Search for existing implementation memory with same path
    existing = None
    all_items = get_all_items(conn)
    for item in all_items:
        if item.type != MemoryType.IMPLEMENTATION:
            continue
        meta = item.metadata or {}
        if meta.get("path") == path:
            existing = item
            break

    evidence = Evidence(
        path=path,
        startLine=actual_start,
        endLine=actual_end,
        contentHash=content_hash,
        kind="source",
        capturedAt=_now(),
    )

    if title is None:
        title = f"File: {file_path.name}"
        title_provided = False
    else:
        title_provided = True

    tags = _normalize_tags(tags)

    if existing:
        # Update existing: refresh statement, evidence, hash
        update_fields = {
            "statement": statement,
            "evidence": json.dumps([evidence.model_dump(by_alias=True)]),
            "tags": json.dumps(tags),
            "updated_at": _now(),
        }
        if title_provided:
            update_fields["title"] = title
        if details is not None:
            update_fields["details"] = details
        # Update metadata
        meta = existing.metadata or {}
        meta["subject"] = subject
        meta["kind"] = kind
        meta["path"] = path
        if start_line is not None:
            meta["startLine"] = actual_start
        if end_line is not None:
            meta["endLine"] = actual_end
        meta["contentHash"] = content_hash
        update_fields["metadata"] = json.dumps(meta)
        update_item_row(conn, existing.id, update_fields)
        insert_history(conn, existing.id, "updated", reason="register_file_read")
        return {
            "id": existing.id,
            "action": "updated",
            "statement": statement,
            "evidence": {"path": path, "startLine": actual_start, "endLine": actual_end, "contentHash": content_hash},
        }
    else:
        # Create new
        item = MemoryItem(
            type=MemoryType.IMPLEMENTATION,
            title=title,
            statement=statement,
            details=details,
            tags=tags,
            evidence=[evidence],
            metadata={
                "subject": subject,
                "kind": kind,
                "path": path,
                **({"startLine": actual_start} if start_line is not None else {}),
                **({"endLine": actual_end} if end_line is not None else {}),
                "contentHash": content_hash,
            },
        )
        insert_item(conn, item)
        insert_history(conn, item.id, "created", reason="register_file_read")
        return {
            "id": item.id,
            "action": "created",
            "statement": statement,
            "evidence": {"path": path, "startLine": actual_start, "endLine": actual_end, "contentHash": content_hash},
        }


def register_file_write(
    conn: turso.Connection,
    path: str,
    statement: str,
    reason: str,
    tags: list[str],
    start_line: int | None = None,
    end_line: int | None = None,
    title: str | None = None,
    details: str | None = None,
) -> dict:
    """Register a file write/modification. Updates existing memory for same path, or creates new."""
    file_path = Path(path)
    if not file_path.is_file():
        return {"error": f"File not found: {path}"}

    # Read file and compute hash
    try:
        content = file_path.read_text(encoding="utf-8")
    except (PermissionError, UnicodeDecodeError) as e:
        return {"error": f"Cannot read {path}: {e}"}

    lines = content.splitlines(keepends=True)
    actual_start = start_line if start_line and start_line >= 1 else 1
    actual_end = end_line if end_line and end_line <= len(lines) else len(lines)
    if actual_start > actual_end:
        return {"error": f"Invalid line range: {start_line}-{end_line}"}

    range_content = "".join(lines[actual_start - 1 : actual_end])
    content_hash = hash_content(range_content)

    # Search for existing implementation memory with same path
    existing = None
    all_items = get_all_items(conn)
    for item in all_items:
        if item.type != MemoryType.IMPLEMENTATION:
            continue
        meta = item.metadata or {}
        if meta.get("path") == path:
            existing = item
            break

    evidence = Evidence(
        path=path,
        startLine=actual_start,
        endLine=actual_end,
        contentHash=content_hash,
        kind="source",
        capturedAt=_now(),
    )

    if title is None:
        title = f"File: {file_path.name}"
        title_provided = False
    else:
        title_provided = True

    tags = _normalize_tags(tags)

    if existing:
        # Update existing: refresh statement, evidence, hash, append change record
        update_fields = {
            "statement": statement,
            "evidence": json.dumps([evidence.model_dump(by_alias=True)]),
            "tags": json.dumps(tags),
            "updated_at": _now(),
        }
        if title_provided:
            update_fields["title"] = title
        if details is not None:
            update_fields["details"] = details
        meta = existing.metadata or {}
        meta["subject"] = path
        meta["kind"] = "module"
        meta["path"] = path
        meta["changeType"] = "write"
        meta["reason"] = reason
        meta["contentHash"] = content_hash
        if start_line is not None:
            meta["startLine"] = actual_start
        if end_line is not None:
            meta["endLine"] = actual_end
        update_fields["metadata"] = json.dumps(meta)
        update_item_row(conn, existing.id, update_fields)
        insert_history(conn, existing.id, "updated", reason=f"register_file_write: {reason}")
        return {
            "id": existing.id,
            "action": "updated",
            "statement": statement,
            "reason": reason,
            "evidence": {"path": path, "startLine": actual_start, "endLine": actual_end, "contentHash": content_hash},
        }
    else:
        # Create new
        item = MemoryItem(
            type=MemoryType.IMPLEMENTATION,
            title=title,
            statement=statement,
            details=details,
            tags=tags,
            evidence=[evidence],
            metadata={
                "subject": path,
                "kind": "module",
                "path": path,
                "changeType": "write",
                "reason": reason,
                "contentHash": content_hash,
                **({"startLine": actual_start} if start_line is not None else {}),
                **({"endLine": actual_end} if end_line is not None else {}),
            },
        )
        insert_item(conn, item)
        insert_history(conn, item.id, "created", reason=f"register_file_write: {reason}")
        return {
            "id": item.id,
            "action": "created",
            "statement": statement,
            "reason": reason,
            "evidence": {"path": path, "startLine": actual_start, "endLine": actual_end, "contentHash": content_hash},
        }
