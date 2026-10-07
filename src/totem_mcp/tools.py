"""High-level tool functions for totem (§43)."""

from __future__ import annotations

import functools
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import turso

from .conflicts import detect_conflicts
from .db import (
    atomic,
    get_operation,
    put_operation,
    find_by_statement_normalized,
    find_by_title,
    find_locator_item,
    find_locator_item_by_path,
    upsert_locator,
    get_all_relations,
    get_history,
    get_relations_for_item,
    get_all_conflicts,
    insert_relation,
    get_all_items,
    get_item,
    get_head_commit,
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
from .hashing import check_staleness, hash_content, hash_symbol, verification_fresh
from .models import (
    LIMITS,
    SCOPE_KINDS,
    Applicability,
    AssertedBy,
    Conflict,
    Evidence,
    MemoryItem,
    MemoryStatus,
    MemoryType,
    default_confidence,
    validate_status_transition,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SYMBOL_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|func|fn|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
)


def _guess_symbol(lines: list[str], start_line: int) -> str | None:
    """Nearest enclosing def/class above the range; best-effort, language-light."""
    for i in range(start_line - 1, max(-1, start_line - 200), -1):
        match = _SYMBOL_RE.match(lines[i])
        if match:
            return match.group(1)
    return None


def _normalize_tags(tags: list[str]) -> list[str]:
    """Normalize tags: lowercase, trim, spaces → hyphens."""
    return [t.strip().lower().replace(" ", "-") for t in tags if t.strip()]


def idempotent(fn):
    """Replay a write by ``operation_id``: return the stored result, no redo."""
    @functools.wraps(fn)
    def wrapper(conn, *args, **kwargs):
        op_id = kwargs.get("operation_id")
        if op_id:
            existing = get_operation(conn, op_id)
            if existing is not None:
                return {**existing, "replayed": True} if isinstance(existing, dict) else existing
        result = fn(conn, *args, **kwargs)
        if op_id:
            put_operation(conn, op_id, result)
        return result

    return wrapper


@atomic
@idempotent
def memory_create(
    conn: turso.Connection,
    type: str,
    title: str,
    statement: str,
    tags: list[str],
    details: str | None = None,
    confidence: float | None = None,
    importance: float = 0.5,
    evidence: list[dict] | None = None,
    related_memory_ids: list[str] | None = None,
    metadata: dict | None = None,
    asserted_by: str | None = None,
    applicability: str | None = None,
    scope: str | None = None,
    supersedes_id: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> dict:
    """Create a new memory item (§43 memory_create).

    ``confidence`` defaults from provenance (asserted_by): test 0.95, user/
    source/git/doc 0.9, runtime 0.7, agent 0.6; hypotheses cap at 0.4.
    ``asserted_by`` is who asserted the claim, not proof it is true; pass an
    explicit ``confidence`` to reach 1.0.
    """
    if not type or not title or not statement:
        raise ValueError("type, title, and statement are required")
    if not tags:
        raise ValueError("At least one tag is required")
    if importance is not None and not (0 <= importance <= 1):
        raise ValueError("importance must in [0, 1]")

    asserted_by = (asserted_by or AssertedBy.AGENT.value).strip().lower()
    if asserted_by not in {e.value for e in AssertedBy}:
        raise ValueError(
            "asserted_by must be one of: " + ", ".join(e.value for e in AssertedBy)
        )
    if applicability is not None:
        applicability = applicability.strip().lower()
        if applicability not in {e.value for e in Applicability}:
            raise ValueError(
                "applicability must be one of: " + ", ".join(e.value for e in Applicability)
            )
    if confidence is None:
        confidence = default_confidence(asserted_by)
        if MemoryType(type) == MemoryType.HYPOTHESIS:
            confidence = min(confidence, 0.4)
    if not (0 <= confidence <= 1):
        raise ValueError("confidence must be in [0, 1]")

    if scope is not None:
        scope = scope.strip()
        if not scope:
            scope = None
        elif scope.startswith("{"):
            try:
                data = json.loads(scope)
            except ValueError as exc:
                raise ValueError(f"scope JSON is invalid: {exc}") from exc
            if not isinstance(data, dict) or data.get("kind") not in SCOPE_KINDS:
                raise ValueError(
                    "scope JSON must be {'kind': one of " + ", ".join(SCOPE_KINDS) + "}"
                )
        elif scope not in SCOPE_KINDS:
            raise ValueError("scope must be one of: " + ", ".join(SCOPE_KINDS))

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
        asserted_by=asserted_by,
        applicability=applicability,
        scope=scope,
    )

    superseded = None
    if supersedes_id:
        superseded = get_item(conn, supersedes_id)
        if superseded is None:
            raise ValueError(f"supersedes_id not found: {supersedes_id}")

    # Density: an identical claim updates rather than duplicates (indexed lookup).
    existing_item = find_by_statement_normalized(conn, statement)
    if existing_item is not None:
        return {
            "id": existing_item.id,
            "status": existing_item.status.value,
            "duplicate": True,
            "warnings": [
                f"Identical statement already stored as {existing_item.id}; "
                "update it instead of creating a duplicate."
            ],
        }

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
    insert_history(
        conn, item.id, "created", source="memory_create",
        actor=actor, session=session, request_id=request_id,
    )
    if superseded is not None:
        insert_relation(conn, item.id, superseded.id, "supersedes")
        update_item_row(
            conn,
            superseded.id,
            {"status": MemoryStatus.SUPERSEDED.value, "updated_at": _now()},
        )
        insert_history(
            conn, superseded.id, "superseded", reason=f"superseded by {item.id}",
            source="memory_create", actor=actor, session=session, request_id=request_id,
        )

    warnings = []
    for c in conflicts:
        warnings.append(
            f"Conflict with {c.item_b}: {c.claim_a} vs {c.claim_b}: {c.condition}"
        )
    if dedup_warning:
        warnings.append(dedup_warning)
    if len(statement.strip()) < 20:
        warnings.append(
            "Statement is very short; prefer a self-contained, durable claim."
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
                Path(ev.path),
                ev.start_line,
                ev.end_line,
                ev.content_hash,
                symbol=ev.symbol,
                blob_hash=ev.blob_hash,
                symbol_hash=ev.symbol_hash,
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
        if stale_evidence and item.verified_at:
            # TMS retraction: the justification (evidence) changed, so the
            # verification no longer holds. Drop back to provenance confidence.
            update_item_row(
                conn,
                id,
                {
                    "verified_at": None,
                    "verified_commit": None,
                    "confidence": default_confidence(item.asserted_by),
                    "updated_at": _now(),
                },
            )
            insert_history(
                conn, id, "verification_voided", reason="evidence changed",
                source="memory_get",
            )
            item.verified_at = None
            item.verified_commit = None
            item.confidence = default_confidence(item.asserted_by)
            warnings.append("Verification voided: evidence changed since verified")

    result = item.model_dump(by_alias=True)
    if warnings:
        result["warnings"] = warnings
    return result


@atomic
@idempotent
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
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
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

    # Reconstruct the resulting item and validate it exactly like create/import
    # do, so update cannot produce a state create would reject.
    resulting = item.model_dump(by_alias=True)
    if title is not None:
        resulting["title"] = title
    if statement is not None:
        resulting["statement"] = statement
    if details is not None:
        resulting["details"] = details
    if tags is not None:
        resulting["tags"] = tags
    if confidence is not None:
        resulting["confidence"] = confidence
    if importance is not None:
        resulting["importance"] = importance
    if evidence is not None:
        resulting["evidence"] = [e.model_dump(by_alias=True) for e in parsed]
    if metadata is not None:
        resulting["metadata"] = metadata
    if status is not None:
        new_status = MemoryStatus(status)
        validate_status_transition(item.status, new_status)
        resulting["status"] = new_status
    MemoryItem.model_validate(resulting)

    update_item_row(conn, id, fields)
    insert_history(
        conn, id, "updated", reason=reason, source="memory_update",
        actor=actor, session=session, request_id=request_id,
    )
    for field_name, old_val, new_val in changes:
        insert_history(conn, id, "updated", field=field_name, old_value=old_val, new_value=new_val, reason=reason, source="memory_update", actor=actor, session=session, request_id=request_id)
    updated = get_item(conn, id)
    return updated.model_dump(by_alias=True) if updated else None


@atomic
@idempotent
def memory_verify(
    conn: turso.Connection,
    id: str,
    method: str | None = None,
    commit: str | None = None,
    confidence: float = 1.0,
    verified_by_id: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> dict | None:
    """Record that a memory was checked: sets verified_at and verified_commit.

    Verification, not provenance, is what earns top trust, so confidence is
    raised to ``confidence`` (default 1.0). ``commit`` defaults to HEAD. An
    optional ``verified_by_id`` links the verification artifact (a test result,
    log, etc.) via a ``verified_by`` relation. Returns None if not found.
    """
    item = get_item(conn, id)
    if item is None:
        return None
    if confidence is not None and not (0 <= confidence <= 1):
        raise ValueError("confidence must be in [0, 1]")

    commit = commit or get_head_commit()
    now = _now()
    fields = {"verified_at": now, "verified_commit": commit, "updated_at": now}
    if confidence is not None:
        fields["confidence"] = confidence
    update_item_row(conn, id, fields)
    insert_history(
        conn, id, "verified", field="verificationMethod", new_value=method,
        reason="memory_verify", source="memory_verify", actor=actor,
        session=session, request_id=request_id, commit=commit,
    )
    if verified_by_id:
        if get_item(conn, verified_by_id) is None:
            raise ValueError(f"verified_by_id not found: {verified_by_id}")
        insert_relation(conn, id, verified_by_id, "verified_by")
    updated = get_item(conn, id)
    return updated.model_dump(by_alias=True) if updated else None


@atomic
def memory_revalidate(
    conn: turso.Connection,
    dry_run: bool = True,
    actor: str | None = None,
) -> dict:
    """Void verifications whose evidence has since changed.

    Scans every verified item; any whose evidence is stale is retracted
    (verified_at/verified_commit cleared, confidence reset to provenance
    default). Dry-run is the default and reports candidates only.
    """
    rows = conn.execute(
        "SELECT id FROM memory_items "
        "WHERE verified_at IS NOT NULL AND status != 'deleted'"
    ).fetchall()
    voided: list[str] = []
    for (item_id,) in rows:
        item = get_item(conn, item_id)
        if item is None or verification_fresh(item):
            continue
        voided.append(item_id)
        if not dry_run:
            update_item_row(
                conn,
                item_id,
                {
                    "verified_at": None,
                    "verified_commit": None,
                    "confidence": default_confidence(item.asserted_by),
                    "updated_at": _now(),
                },
            )
            insert_history(
                conn, item_id, "verification_voided", reason="evidence changed",
                source="memory_revalidate", actor=actor,
            )
    return {"dry_run": dry_run, "voided": voided, "count": len(voided)}


@atomic
@idempotent
def memory_delete(
    conn: turso.Connection,
    id: str,
    reason: str,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> dict:
    """Soft-delete a memory item (§43 memory_delete). reason is required."""
    if not reason:
        raise ValueError("reason is required for deletion (§3)")
    item = get_item(conn, id)
    if item is None:
        return {"error": f"Item {id} not found"}
    soft_delete(conn, id)
    insert_history(
        conn, id, "deleted", reason=reason, source="memory_delete",
        actor=actor, session=session, request_id=request_id,
    )
    return {"id": id, "status": "deleted"}


@atomic
@idempotent
def memory_relate(
    conn: turso.Connection,
    from_id: str,
    to_id: str,
    kind: str,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> dict:
    """Create a typed relation between two memories (supersedes, contradicts,
    invalidates, derived_from, verified_by, refines, depends_on)."""
    if get_item(conn, from_id) is None:
        raise ValueError(f"from_id not found: {from_id}")
    if get_item(conn, to_id) is None:
        raise ValueError(f"to_id not found: {to_id}")
    relation = insert_relation(conn, from_id, to_id, kind)
    insert_history(
        conn, from_id, "related", field=kind, new_value=to_id,
        source="memory_relate", actor=actor, session=session, request_id=request_id,
    )
    if kind == "supersedes":
        update_item_row(
            conn, to_id, {"status": MemoryStatus.SUPERSEDED.value, "updated_at": _now()}
        )
        insert_history(
            conn, to_id, "superseded", reason=f"superseded by {from_id}",
            source="memory_relate", actor=actor, session=session, request_id=request_id,
        )
    elif kind == "invalidates":
        update_item_row(
            conn, to_id, {"status": MemoryStatus.INVALIDATED.value, "updated_at": _now()}
        )
        insert_history(
            conn, to_id, "invalidated", reason=f"invalidated by {from_id}",
            source="memory_relate", actor=actor, session=session, request_id=request_id,
        )
    return relation


def memory_relations(conn: turso.Connection, id: str) -> list[dict]:
    """All relations involving one memory."""
    return get_relations_for_item(conn, id)


def memory_history(conn: turso.Connection, id: str) -> list[dict]:
    """The immutable timeline of one memory (created/updated/deleted events)."""
    return get_history(conn, id)


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


@atomic
@idempotent
def resolve_conflict(
    conn: turso.Connection,
    conflict_id: str,
    resolution: str,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> dict | None:
    """Mark a conflict as resolved (§45); recorded in the items' history."""
    row = conn.execute(
        "SELECT item_a, item_b FROM conflicts WHERE id = ?", (conflict_id,)
    ).fetchone()
    result = db_resolve_conflict(conn, conflict_id, resolution)
    if result is not None and row is not None:
        for item_id in {row[0], row[1]}:
            insert_history(
                conn, item_id, "conflict_resolved", new_value=resolution,
                source="resolve_conflict", actor=actor, session=session,
                request_id=request_id,
            )
    return result


@atomic
def memory_gc(
    conn: turso.Connection,
    retention_days: int = 90,
    dry_run: bool = True,
    actor: str | None = None,
) -> dict:
    """Purge terminal-state memories older than ``retention_days``.

    Eligible items are ``superseded``/``invalidated``/``resolved``, untouched for
    the retention window, and not referenced by any relation. Dry-run is the
    default: it reports candidates and changes nothing.
    """
    if retention_days < 0:
        raise ValueError("retention_days must be >= 0")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    rows = conn.execute(
        "SELECT id FROM memory_items "
        "WHERE status IN ('superseded', 'invalidated', 'resolved') "
        "AND updated_at < ? "
        "AND id NOT IN (SELECT to_id FROM memory_relations) "
        "ORDER BY updated_at",
        (cutoff,),
    ).fetchall()
    ids = [r[0] for r in rows]
    if not dry_run:
        for item_id in ids:
            soft_delete(conn, item_id)
            insert_history(
                conn, item_id, "gc_deleted", reason="memory_gc",
                source="memory_gc", actor=actor,
            )
    return {
        "dry_run": dry_run,
        "count": len(ids),
        "deleted": 0 if dry_run else len(ids),
        "candidates": ids,
    }


EXPORT_FORMAT_VERSION = 1


def memory_export(conn: turso.Connection) -> dict:
    """Export memories, conflicts, and relations as the archival format.

    JSON is the portability boundary; the underlying DB is replaceable.
    """
    from .db import SCHEMA_VERSION

    items = get_all_items(conn)
    conflicts = get_all_conflicts(conn)
    relations = get_all_relations(conn)
    return {
        "format_version": EXPORT_FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": _now(),
        "items": [item.model_dump(by_alias=True) for item in items],
        "conflicts": [c.model_dump(by_alias=True) for c in conflicts],
        "relations": relations,
    }


@atomic
def memory_import(
    conn: turso.Connection,
    data: dict,
    mode: str = "normal",
    dry_run: bool = False,
) -> dict:
    """Import an export dict.

    Modes: ``normal`` (skip invalid records, import the rest), ``strict``
    (abort with no mutation if anything is invalid), ``replace`` (clear existing
    data first, then import). ``dry_run`` validates and reports without writing.
    Always returns a complete report with per-record errors.
    """
    if mode not in ("normal", "strict", "replace"):
        raise ValueError("mode must be normal, strict, or replace")
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError("invalid export: 'items' is required")
    if len(json.dumps(data)) > LIMITS["import_bytes"]:
        raise ValueError(f"import exceeds {LIMITS['import_bytes']} bytes")
    format_version = int(data.get("format_version") or 0)
    if format_version > EXPORT_FORMAT_VERSION:
        raise ValueError(
            f"export format {format_version} is newer than supported "
            f"{EXPORT_FORMAT_VERSION}; upgrade totem"
        )

    items_raw = data.get("items", [])
    relations_raw = data.get("relations", []) if format_version >= 1 else []
    conflicts_raw = data.get("conflicts", [])
    errors: list[dict] = []

    # Validation phase: parse everything before any mutation.
    valid_items: list[MemoryItem] = []
    for i, raw in enumerate(items_raw):
        try:
            valid_items.append(MemoryItem.model_validate(raw))
        except Exception as e:
            errors.append(
                {"section": "items", "index": i, "id": (raw or {}).get("id"), "error": str(e)}
            )

    valid_relations: list[tuple[str, str, str]] = []
    for i, raw in enumerate(relations_raw):
        key = (raw.get("from_id"), raw.get("to_id"), raw.get("kind"))
        if not all(key):
            errors.append(
                {"section": "relations", "index": i, "error": "from_id, to_id, kind required"}
            )
        else:
            valid_relations.append(key)

    valid_conflicts: list[Conflict] = []
    for i, raw in enumerate(conflicts_raw):
        try:
            valid_conflicts.append(Conflict.model_validate(raw))
        except Exception as e:
            errors.append({"section": "conflicts", "index": i, "error": str(e)})

    def _report(imported, skipped, r_imp, r_skip, c_imp, c_skip, aborted=False):
        return {
            "mode": mode,
            "dry_run": dry_run,
            "aborted": aborted,
            "items_imported": imported,
            "items_skipped": skipped,
            "relations_imported": r_imp,
            "relations_skipped": r_skip,
            "conflicts_imported": c_imp,
            "conflicts_skipped": c_skip,
            "errors": errors,
        }

    if mode == "strict" and errors:
        return _report(0, len(items_raw), 0, len(relations_raw), 0, len(conflicts_raw), aborted=True)

    if dry_run:
        return _report(
            len(valid_items), len(items_raw) - len(valid_items),
            len(valid_relations), len(relations_raw) - len(valid_relations),
            len(valid_conflicts), len(conflicts_raw) - len(valid_conflicts),
        )

    if mode == "replace":
        conn.execute("DELETE FROM memory_relations")
        conn.execute("DELETE FROM conflicts")
        conn.execute("DELETE FROM memory_history")
        conn.execute("DELETE FROM memory_items")

    # Apply items
    existing_ids = {item.id for item in get_all_items(conn)}
    items_imported = 0
    items_skipped = len(items_raw) - len(valid_items)
    for item in valid_items:
        if item.id in existing_ids:
            items_skipped += 1
            continue
        insert_item(conn, item)
        insert_history(conn, item.id, "created", reason="import", source="import")
        existing_ids.add(item.id)
        items_imported += 1

    known = {item.id for item in get_all_items(conn)}
    existing_relations = {
        (r["from_id"], r["to_id"], r["kind"]) for r in get_all_relations(conn)
    }
    relations_imported = 0
    relations_skipped = len(relations_raw) - len(valid_relations)
    for key in valid_relations:
        if key in existing_relations or key[0] not in known or key[1] not in known:
            relations_skipped += 1
            continue
        try:
            insert_relation(conn, key[0], key[1], key[2])
            existing_relations.add(key)
            relations_imported += 1
        except Exception as e:
            relations_skipped += 1
            errors.append({"section": "relations", "error": str(e)})

    existing_conflicts = {
        (c.item_a, c.item_b, c.claim_a, c.claim_b) for c in get_all_conflicts(conn)
    }
    conflicts_imported = 0
    conflicts_skipped = len(conflicts_raw) - len(valid_conflicts)
    for conflict in valid_conflicts:
        key = (conflict.item_a, conflict.item_b, conflict.claim_a, conflict.claim_b)
        if key in existing_conflicts:
            conflicts_skipped += 1
            continue
        try:
            insert_conflict(conn, conflict)
            existing_conflicts.add(key)
            conflicts_imported += 1
        except Exception as e:
            conflicts_skipped += 1
            errors.append({"section": "conflicts", "error": str(e)})

    return _report(
        items_imported, items_skipped,
        relations_imported, relations_skipped,
        conflicts_imported, conflicts_skipped,
    )


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
            if check_staleness(
                Path(ev.path),
                ev.start_line,
                ev.end_line,
                ev.content_hash,
                symbol=ev.symbol,
                blob_hash=ev.blob_hash,
                symbol_hash=ev.symbol_hash,
            ):
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


@atomic
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
    symbol: str | None = None,
) -> dict:
    """Register facts learned from reading a file.

    Facts are keyed by (path, subject): the same file can hold several distinct
    memories; the same fact updates in place. Evidence stores a whole-file blob
    hash plus an optional symbol so moved code is not reported stale.
    """
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
    blob_hash = hash_content(content)
    symbol = symbol or _guess_symbol(lines, actual_start)
    symbol_hash = hash_symbol(file_path, symbol, text=content) if symbol else None

    # Locate the existing implementation memory via the locator index (no scan).
    existing_id = find_locator_item(conn, path, subject)
    existing = get_item(conn, existing_id) if existing_id else None

    evidence = Evidence(
        path=path,
        startLine=actual_start,
        endLine=actual_end,
        contentHash=content_hash,
        kind="source",
        capturedAt=_now(),
        symbol=symbol,
        symbolHash=symbol_hash,
        blobHash=blob_hash,
    )

    if title is None:
        title = f"File: {file_path.name} — {subject}" if subject else f"File: {file_path.name}"
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
        meta["blobHash"] = blob_hash
        if symbol:
            meta["symbol"] = symbol
        if symbol_hash:
            meta["symbolHash"] = symbol_hash
        update_fields["metadata"] = json.dumps(meta)
        update_item_row(conn, existing.id, update_fields)
        upsert_locator(conn, existing.id, path, subject, symbol)
        insert_history(conn, existing.id, "updated", reason="register_file_read", source="register_file_read")
        return {
            "id": existing.id,
            "action": "updated",
            "statement": statement,
            "evidence": {
                "path": path,
                "startLine": actual_start,
                "endLine": actual_end,
                "contentHash": content_hash,
                "blobHash": blob_hash,
                "symbol": symbol,
                "symbolHash": symbol_hash,
            },
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
                "blobHash": blob_hash,
                **({"symbol": symbol} if symbol else {}),
                **({"symbolHash": symbol_hash} if symbol_hash else {}),
            },
        )
        insert_item(conn, item)
        try:
            upsert_locator(conn, item.id, path, subject, symbol)
        except turso.IntegrityError:
            # Lost a cross-process race for (path, subject): drop our orphan and
            # let the retry take the update path against the winner.
            conn.execute("DELETE FROM memory_items WHERE id = ?", (item.id,))
            return register_file_read(
                conn,
                path=path,
                statement=statement,
                subject=subject,
                kind=kind,
                tags=tags,
                start_line=start_line,
                end_line=end_line,
                title=title,
                details=details,
                symbol=symbol,
            )
        insert_history(conn, item.id, "created", reason="register_file_read", source="register_file_read")
        return {
            "id": item.id,
            "action": "created",
            "statement": statement,
            "evidence": {
                "path": path,
                "startLine": actual_start,
                "endLine": actual_end,
                "contentHash": content_hash,
                "blobHash": blob_hash,
                "symbol": symbol,
                "symbolHash": symbol_hash,
            },
        }


@atomic
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
    symbol: str | None = None,
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
    blob_hash = hash_content(content)
    symbol_hash = hash_symbol(file_path, symbol, text=content) if symbol else None

    # Locate the existing implementation memory via the locator index (no scan).
    existing_id = find_locator_item_by_path(conn, path)
    existing = get_item(conn, existing_id) if existing_id else None

    evidence = Evidence(
        path=path,
        startLine=actual_start,
        endLine=actual_end,
        contentHash=content_hash,
        kind="source",
        capturedAt=_now(),
        symbol=symbol,
        symbolHash=symbol_hash,
        blobHash=blob_hash,
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
        meta["blobHash"] = blob_hash
        if symbol:
            meta["symbol"] = symbol
        if symbol_hash:
            meta["symbolHash"] = symbol_hash
        if start_line is not None:
            meta["startLine"] = actual_start
        if end_line is not None:
            meta["endLine"] = actual_end
        update_fields["metadata"] = json.dumps(meta)
        update_item_row(conn, existing.id, update_fields)
        upsert_locator(conn, existing.id, path, path, symbol)
        insert_history(conn, existing.id, "updated", reason=f"register_file_write: {reason}", source="register_file_write")
        return {
            "id": existing.id,
            "action": "updated",
            "statement": statement,
            "reason": reason,
            "evidence": {"path": path, "startLine": actual_start, "endLine": actual_end, "contentHash": content_hash, "symbolHash": symbol_hash},
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
                **({"symbolHash": symbol_hash} if symbol_hash else {}),
            },
        )
        insert_item(conn, item)
        try:
            upsert_locator(conn, item.id, path, path, symbol)
        except turso.IntegrityError:
            # Lost a cross-process race for this path: drop our orphan and let
            # the retry update the winner.
            conn.execute("DELETE FROM memory_items WHERE id = ?", (item.id,))
            return register_file_write(
                conn,
                path=path,
                statement=statement,
                reason=reason,
                tags=tags,
                start_line=start_line,
                end_line=end_line,
                title=title,
                details=details,
                symbol=symbol,
            )
        insert_history(conn, item.id, "created", reason=f"register_file_write: {reason}", source="register_file_write")
        return {
            "id": item.id,
            "action": "created",
            "statement": statement,
            "reason": reason,
            "evidence": {"path": path, "startLine": actual_start, "endLine": actual_end, "contentHash": content_hash, "symbolHash": symbol_hash},
        }
