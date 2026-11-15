"""High-level tool functions for totem (§43)."""

from __future__ import annotations

import functools
import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import turso

from .conflicts import detect_conflicts
from .file_summary import summarize_file
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
    get_all_entities,
    get_entities_for_item,
    get_items_for_entity,
    get_history,
    get_relations_for_item,
    get_all_conflicts,
    insert_relation,
    link_entity,
    upsert_entity,
    get_all_items,
    get_check,
    get_item,
    get_head_commit,
    get_overlapping_items,
    import_items as db_import_items,
    init_db,
    insert_conflict,
    insert_history,
    insert_item,
    record_check,
    record_feedback,
    list_feedback,
    list_items,
    resolve_conflict as db_resolve_conflict,
    soft_delete,
    update_item_row,
    verify_history,
)
from .hashing import check_staleness, hash_content, hash_symbol, verification_fresh
from .ranking import cosine
from .paths import EvidencePathError, contained_path, project_root
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


def _content_digest(root: Path, files: list[str], per_file: int = 2_000_000) -> str:
    """Digest the observed bytes of changed files, bounded per file."""
    digest = hashlib.sha256()
    for rel in files[:200]:
        digest.update(rel.encode("utf-8", "replace"))
        digest.update(b"\0")
        try:
            with open(root / rel, "rb") as handle:
                digest.update(handle.read(per_file))
        except OSError:
            digest.update(b"<missing>")
        digest.update(b"\0")
    return digest.hexdigest()


def _git_change_snapshot(root: Path | None) -> dict:
    """Files git observes as changed, plus a bounded content digest.

    The commit gate otherwise trusts the agent's declaration. This records what
    actually moved, so a claimed write that changed nothing (or changed other
    files) is visible in history.
    """
    if root is None:
        return {}
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "-z"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    files: list[str] = []
    for entry in result.stdout.split("\0"):
        if not entry or len(entry) < 4:
            continue
        name = entry[3:]
        if " -> " in name:
            name = name.split(" -> ", 1)[1]
        files.append(name)
    files = sorted(set(files))
    return {
        "actualChangedFiles": files,
        "diffDigest": _content_digest(root, files),
    }


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
    valid_from: str | None = None,
    valid_to: str | None = None,
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
    metadata = dict(metadata or {})
    metadata.setdefault("memoryClass", _memory_class(mem_type.value, metadata))

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
        valid_from=valid_from or None,
        valid_to=valid_to or None,
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
    _link_item_entities(conn, item.id, {**metadata, "tags": tags})
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
                root=project_root(conn),
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
    valid_from: str | None = None,
    valid_to: str | None = None,
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
        # Reaching bug state "verified" is a verification: stamp it.
        if item.type == MemoryType.BUG and metadata.get("state") == "verified":
            fields["verified_at"] = fields["updated_at"]
            fields["verified_commit"] = get_head_commit()
    if valid_from is not None:
        changes.append(("valid_from", item.valid_from, valid_from or None))
        fields["valid_from"] = valid_from or None
    if valid_to is not None:
        changes.append(("valid_to", item.valid_to, valid_to or None))
        fields["valid_to"] = valid_to or None

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
    check_id: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> dict | None:
    """Record that a memory was checked: sets verified_at and verified_commit.

    Evidence-derived verification is the strong path: pass ``check_id`` from
    :func:`memory_check_record` and the check must exist, belong to this item,
    have exited 0, and match the item's current evidence digest. Recording an
    assertion without a check still works for compatibility, but is marked
    ``verificationMode="assertion"`` so the context compiler and calibration
    model can weight it below an observed check.

    ``commit`` defaults to HEAD. An optional ``verified_by_id`` links the
    verification artifact via a ``verified_by`` relation.
    """
    item = get_item(conn, id)
    if item is None:
        return None
    if confidence is not None and not (0 <= confidence <= 1):
        raise ValueError("confidence must be in [0, 1]")

    warnings: list[str] = []
    mode = "assertion"
    check = None
    if check_id:
        check = get_check(conn, check_id)
        if check is None:
            raise ValueError(f"check not found: {check_id}")
        if check["itemId"] != id:
            raise ValueError(f"check {check_id} does not belong to {id}")
        if int(check["exitCode"]) != 0:
            raise ValueError(
                f"check {check_id} did not pass (exit {check['exitCode']})"
            )
        current = _evidence_digest(item)
        if check.get("evidenceHash") and check["evidenceHash"] != current:
            raise ValueError(
                "check was recorded against different evidence; re-run it"
            )
        mode = "check"
    else:
        warnings.append(
            "assertion-only verification: record an observed check with "
            "memory_check_record and pass check_id for evidence-derived trust"
        )

    commit = commit or get_head_commit()
    now = _now()
    meta = dict(item.metadata or {})
    meta["verificationMode"] = mode
    if check_id:
        meta["lastCheckId"] = check_id
    fields = {
        "verified_at": now,
        "verified_commit": commit,
        "updated_at": now,
        "metadata": json.dumps(meta),
    }
    if confidence is not None:
        fields["confidence"] = confidence
    update_item_row(conn, id, fields)
    insert_history(
        conn, id, "verified", field="verificationMethod",
        new_value=(f"check:{check_id}" if check_id else method),
        reason="memory_verify", source="memory_verify", actor=actor,
        session=session, request_id=request_id, commit=commit,
    )
    if verified_by_id:
        if get_item(conn, verified_by_id) is None:
            raise ValueError(f"verified_by_id not found: {verified_by_id}")
        insert_relation(conn, id, verified_by_id, "verified_by")
    updated = get_item(conn, id)
    if updated is None:
        return None
    result = updated.model_dump(by_alias=True)
    result["verificationMode"] = mode
    if check is not None:
        result["check"] = check
    if warnings:
        result["warnings"] = warnings
    return result


def _evidence_digest(item: MemoryItem) -> str:
    payload = [e.model_dump(by_alias=True) for e in item.evidence]
    return hash_content(json.dumps(payload, sort_keys=True, separators=(",", ":")))


@atomic
@idempotent
def memory_check_record(
    conn: turso.Connection,
    id: str,
    name: str,
    command: str | None = None,
    cwd: str | None = None,
    exit_code: int = 0,
    output: str | None = None,
    output_hash: str | None = None,
    evidence_hash: str | None = None,
    commit: str | None = None,
    ran_at: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> dict:
    """Record one observed check result against a memory item.

    The check is the evidence a later ``memory_verify(check_id=...)`` consumes.
    ``exit_code`` and the hashes are recorded as given: this layer never invents
    a verdict, it makes one attributable.
    """
    item = get_item(conn, id)
    if item is None:
        raise ValueError(f"item not found: {id}")
    if not name:
        raise ValueError("check name is required")
    if output and not output_hash:
        output_hash = hash_content(output[-20_000:])
    if evidence_hash is None:
        evidence_hash = _evidence_digest(item)
    record = record_check(
        conn,
        item_id=id,
        name=name,
        command=command,
        cwd=cwd,
        exit_code=exit_code,
        output_hash=output_hash,
        evidence_hash=evidence_hash,
        commit=commit or get_head_commit(),
        ran_at=ran_at,
    )
    insert_history(
        conn, id, "checked", field="name", new_value=name,
        reason=f"exit {exit_code}", source="memory_check_record", actor=actor,
        session=session, request_id=request_id, commit=commit,
    )
    return record


def memory_audit(conn: turso.Connection) -> dict:
    """Verify the history hash chain and each item's recorded state digest."""
    return verify_history(conn)


def memory_quality(conn: turso.Connection) -> dict:
    """Report whether the corpus is durable knowledge or activity noise."""
    items = get_all_items(conn)
    file_state = [item for item in items if _is_file_state_item(item)]
    durable = [item for item in items if not _is_file_state_item(item)]
    conflict_total = conn.execute("SELECT COUNT(*) FROM conflicts").fetchone()[0]
    conflict_open = conn.execute(
        "SELECT COUNT(*) FROM conflicts WHERE resolved_at IS NULL"
    ).fetchone()[0]
    duplicate_titles = sum(
        count - 1 for count in Counter(item.title for item in items).values() if count > 1
    )

    total_evidence = 0
    stale_evidence = 0
    stale_items: set[str] = set()
    cache: dict = {}
    root = project_root(conn)
    for item in items:
        for evidence in item.evidence:
            total_evidence += 1
            try:
                stale = check_staleness(
                    Path(evidence.path),
                    evidence.start_line,
                    evidence.end_line,
                    evidence.content_hash,
                    symbol=evidence.symbol,
                    blob_hash=evidence.blob_hash,
                    symbol_hash=evidence.symbol_hash,
                    root=root,
                    cache=cache,
                )
            except Exception:
                stale = True
            if stale:
                stale_evidence += 1
                stale_items.add(item.id)

    def count(table: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0

    digest = sorted(
        durable,
        key=lambda item: (item.importance, item.updated_at),
        reverse=True,
    )[:5]
    return {
        "total": len(items),
        "byType": dict(Counter(item.type.value for item in items)),
        "byClass": dict(Counter(
            _memory_class(item.type.value, item.metadata) for item in items
        )),
        "fileState": len(file_state),
        "durable": len(durable),
        "signalRatio": round(len(durable) / len(items), 3) if items else 0.0,
        "provenance": {
            "assertedBy": sum(1 for item in items if item.asserted_by),
            "scope": sum(1 for item in items if item.scope),
            "withEvidence": sum(1 for item in items if item.evidence),
        },
        "staleEvidence": {
            "evidence": stale_evidence,
            "total": total_evidence,
            "items": len(stale_items),
            "ratio": round(stale_evidence / total_evidence, 3) if total_evidence else 0.0,
        },
        "conflicts": {
            "open": int(conflict_open),
            "resolved": int(conflict_total - conflict_open),
            "total": int(conflict_total),
        },
        "duplicateTitles": duplicate_titles,
        "relations": count("memory_relations"),
        "checks": count("memory_checks"),
        "feedback": count("memory_feedback"),
        "embeddings": count("memory_embeddings"),
        "entities": count("memory_entities"),
        "digest": [
            {"type": item.type.value, "title": item.title}
            for item in digest
        ],
    }


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
        if item is None or verification_fresh(item, root=project_root(conn)):
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
    valid_from: str | None = None,
    valid_to: str | None = None,
    source_episode: str | None = None,
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
    relation = insert_relation(
        conn, from_id, to_id, kind,
        valid_from=valid_from, valid_to=valid_to, source_episode=source_episode,
    )
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
        "AND verified_at IS NULL "
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


EXPORT_FORMAT_VERSION = 2


def memory_export(conn: turso.Connection) -> dict:
    """Export memories, conflicts, and relations as the archival format.

    JSON is the portability boundary; the underlying DB is replaceable.
    """
    from .db import SCHEMA_VERSION

    items = get_all_items(conn)
    conflicts = get_all_conflicts(conn)
    relations = get_all_relations(conn)
    checks = [
        {
            "id": row[0], "itemId": row[1], "name": row[2], "command": row[3],
            "cwd": row[4], "exitCode": row[5], "outputHash": row[6],
            "evidenceHash": row[7], "commit": row[8], "ranAt": row[9],
        }
        for row in conn.execute(
            "SELECT id, item_id, name, command, cwd, exit_code, output_hash, "
            "evidence_hash, commit_sha, ran_at FROM memory_checks"
        ).fetchall()
    ]
    return {
        "format_version": EXPORT_FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "exported_at": _now(),
        "items": [item.model_dump(by_alias=True) for item in items],
        "conflicts": [c.model_dump(by_alias=True) for c in conflicts],
        "relations": relations,
        "checks": checks,
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
    checks_raw = data.get("checks", []) if format_version >= 2 else []
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

    valid_checks: list[dict] = []
    for i, raw in enumerate(checks_raw):
        if not isinstance(raw, dict) or not all(
            raw.get(key) for key in ("id", "itemId", "name")
        ):
            errors.append(
                {"section": "checks", "index": i,
                 "error": "id, itemId, and name are required"}
            )
        else:
            valid_checks.append(raw)

    def _report(imported, skipped, r_imp, r_skip, c_imp, c_skip,
                ch_imp=0, ch_skip=0, aborted=False):
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
            "checks_imported": ch_imp,
            "checks_skipped": ch_skip,
            "errors": errors,
        }

    if mode == "strict" and errors:
        return _report(0, len(items_raw), 0, len(relations_raw), 0, len(conflicts_raw), aborted=True)

    if dry_run:
        return _report(
            len(valid_items), len(items_raw) - len(valid_items),
            len(valid_relations), len(relations_raw) - len(valid_relations),
            len(valid_conflicts), len(conflicts_raw) - len(valid_conflicts),
            0, len(checks_raw),
        )

    if mode == "replace":
        conn.execute("DELETE FROM memory_relations")
        conn.execute("DELETE FROM conflicts")
        conn.execute("DELETE FROM memory_history")
        conn.execute("DELETE FROM memory_checks")
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
    existing_checks = {
        row[0] for row in conn.execute("SELECT id FROM memory_checks").fetchall()
    }
    checks_imported = 0
    checks_skipped = len(checks_raw) - len(valid_checks)
    for check in valid_checks:
        if check["id"] in existing_checks or check["itemId"] not in known:
            checks_skipped += 1
            continue
        conn.execute(
            "INSERT INTO memory_checks "
            "(id, item_id, name, command, cwd, exit_code, output_hash, "
            "evidence_hash, commit_sha, ran_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                check["id"], check["itemId"], check["name"], check.get("command"),
                check.get("cwd"), int(check.get("exitCode") or 0),
                check.get("outputHash"), check.get("evidenceHash"),
                check.get("commit"), check.get("ranAt") or _now(), _now(),
            ),
        )
        existing_checks.add(check["id"])
        checks_imported += 1
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
        checks_imported, checks_skipped,
    )


def memory_search(
    conn: turso.Connection,
    query: str,
    types: list[str] | None = None,
    tags: list[str] | None = None,
    include_stale: bool = False,
    limit: int = 20,
    as_of: str | None = None,
    include_expired: bool = False,
    include_file_state: bool = False,
) -> list[dict]:
    """Hybrid lexical and dense search over the whole memory corpus."""
    from .context import _parse_as_of
    from .retrieval import build_candidates

    candidates, _meta = build_candidates(
        conn, query, tags, None,
        limit=max(limit * 4, 50),
        include_stale=include_stale,
        include_expired=include_expired,
        as_of=_parse_as_of(as_of),
    )
    tag_set = set(tags or [])
    ranked = []
    for candidate in candidates:
        item = candidate.item
        if types and item.type.value not in types:
            continue
        if tag_set and not (tag_set & set(item.tags)):
            continue
        if not include_file_state and _is_file_state_item(item):
            continue
        score = (
            0.50 * candidate.fused
            + 0.20 * candidate.lexical
            + 0.15 * candidate.dense
            + 0.10 * candidate.tag
            + 0.05 * candidate.path
        )
        ranked.append((score, candidate))
    ranked.sort(key=lambda pair: pair[0], reverse=True)

    results = []
    for score, candidate in ranked[:limit]:
        item = candidate.item
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
                root=project_root(conn),
            ):
                warnings.append(f"Evidence stale: {ev.path}:{ev.start_line}-{ev.end_line}")
        d = item.model_dump(by_alias=True)
        d["score"] = round(score, 4)
        d["reasons"] = list(candidate.reasons)
        if warnings:
            d["warnings"] = warnings
        results.append(d)
    return results


def _is_file_state_item(item) -> bool:
    metadata = item.metadata or {}
    if metadata.get("fileKind") == "file_tldr":
        return True
    return (
        item.type.value == "implementation"
        and item.title.startswith("File:")
        and item.statement.startswith(("Read ", "Patched ", "Changed "))
    )


def _memory_class(type_value: str, metadata: dict | None = None) -> str:
    """One of operational, episodic, procedural, core, semantic."""
    metadata = metadata or {}
    explicit = str(metadata.get("memoryClass") or "").strip().lower()
    if explicit in {"operational", "episodic", "procedural", "core", "semantic"}:
        return explicit
    kind = str(metadata.get("kind") or "").lower()
    if metadata.get("fileKind") == "file_tldr" or kind == "file_tldr":
        return "operational"
    if kind == "session_summary":
        return "episodic"
    if kind == "workflow":
        return "procedural"
    if kind in ("core_profile", "profile"):
        return "core"
    return "semantic"


def _entity_candidates(metadata: dict | None) -> list[tuple[str, str]]:
    metadata = metadata or {}
    found: list[tuple[str, str]] = []
    path = metadata.get("path")
    if path:
        found.append(("file", str(path)))
    subject = metadata.get("subject")
    if subject:
        found.append(("symbol", str(subject)))
    component = metadata.get("component")
    if component:
        found.append(("component", str(component)))
    for tag in metadata.get("tags") or []:
        text = str(tag)
        for prefix, kind in (
            ("path:", "file"), ("symbol:", "symbol"), ("rule:", "rule"),
            ("pr:", "pull_request"), ("package:", "package"),
            ("architecture:", "component"),
        ):
            if text.startswith(prefix) and text[len(prefix):].strip():
                found.append((kind, text[len(prefix):].strip()))
    return list(dict.fromkeys(found))


def _link_item_entities(
    conn: turso.Connection, item_id: str, metadata: dict | None
) -> list[str]:
    linked: list[str] = []
    for kind, name in _entity_candidates(metadata):
        entity_id = upsert_entity(conn, kind, name)
        link_entity(conn, item_id, entity_id)
        linked.append(entity_id)
    return linked


_NEGATIONS = {"not", "never", "no", "cannot", "can't", "without", "deprecated"}


def _polarity(text: str) -> int:
    tokens = set(re.findall(r"[a-z']+", (text or "").lower()))
    return -1 if tokens & _NEGATIONS else 1


@atomic
@idempotent
def memory_feedback(
    conn: turso.Connection,
    id: str,
    signal: str,
    query: str | None = None,
    tags: list[str] | None = None,
    weight: float = 1.0,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> dict:
    """Record whether a memory was useful, wrong, irrelevant, or stale."""
    item = get_item(conn, id)
    if item is None:
        return {"error": f"Item {id} not found"}
    record = record_feedback(
        conn, id, signal, query=query, tags=tags, weight=weight
    )
    insert_history(
        conn, id, "feedback", field="signal", new_value=signal,
        reason=f"feedback weight {weight}", source="memory_feedback",
        actor=actor, session=session, request_id=request_id,
    )
    return record


def memory_feedback_list(
    conn: turso.Connection,
    id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    return list_feedback(conn, item_id=id, limit=limit)


def memory_train_models(
    conn: turso.Connection,
    min_examples: int = 24,
) -> dict:
    """Fit the reranker and calibrator from recorded feedback."""
    from .learning import train_models

    return train_models(conn, min_examples=min_examples)


@atomic
def memory_consolidate(
    conn: turso.Connection,
    apply: bool = False,
    threshold: float = 0.92,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
) -> dict:
    """Cluster near-duplicate memories and optionally supersede duplicates."""
    from .retrieval import get_encoder_bundle

    if not (0.5 <= threshold <= 1.0):
        raise ValueError("threshold must be in [0.5, 1.0]")
    items = [
        item for item in get_all_items(conn)
        if item.status.value in ("active", "potentially_stale")
    ]
    if len(items) < 2:
        return {"dryRun": not apply, "clusters": [], "count": 0}

    bundle = get_encoder_bundle(conn, items)
    vectors = bundle.vectors
    clusters: list[dict] = []
    for item in items:
        vector = vectors.get(item.id)
        if not vector:
            continue
        best_index = None
        best_similarity = 0.0
        for index, cluster in enumerate(clusters):
            similarity = max(
                (
                    cosine(vector, vectors.get(member_id, []))
                    for member_id in cluster["memberIds"]
                ),
                default=0.0,
            )
            if similarity > best_similarity:
                best_similarity = similarity
                best_index = index
        if best_index is not None and best_similarity >= threshold:
            clusters[best_index]["memberIds"].append(item.id)
            clusters[best_index]["similarity"][item.id] = round(best_similarity, 4)
        else:
            clusters.append({
                "memberIds": [item.id],
                "similarity": {item.id: 1.0},
            })

    by_id = {item.id: item for item in items}
    report = []
    for cluster in clusters:
        members = [
            by_id[item_id] for item_id in cluster["memberIds"] if item_id in by_id
        ]
        if len(members) < 2:
            continue
        canonical = max(
            members,
            key=lambda item: (
                1 if item.verified_at else 0,
                item.importance,
                item.confidence,
                item.updated_at,
            ),
        )
        duplicates = [item for item in members if item.id != canonical.id]
        contradiction = any(
            _polarity(canonical.statement) != _polarity(item.statement)
            for item in duplicates
        )
        if apply:
            for duplicate in duplicates:
                update_item_row(
                    conn, duplicate.id,
                    {
                        "status": MemoryStatus.SUPERSEDED.value,
                        "updated_at": _now(),
                    },
                )
                try:
                    insert_relation(conn, duplicate.id, canonical.id, "refines")
                except turso.IntegrityError:
                    pass
                insert_history(
                    conn, duplicate.id, "consolidated",
                    reason=f"near-duplicate of {canonical.id}",
                    source="memory_consolidate", actor=actor,
                    session=session, request_id=request_id,
                )
        report.append({
            "canonical": canonical.id,
            "duplicates": [item.id for item in duplicates],
            "maxSimilarity": max(
                (cluster["similarity"].get(item.id, 0.0) for item in duplicates),
                default=1.0,
            ),
            "contradictionCandidate": contradiction,
        })

    return {
        "dryRun": not apply,
        "threshold": threshold,
        "clusters": report,
        "count": len(report),
        "backend": bundle.backend,
    }


def totem_init(project: str | None = None) -> dict:
    """Initialize totem for a project: create .totem/, ensure DB exists, return status."""
    from .db import init_project, get_db_path

    db_path = get_db_path(project)
    project_dir = db_path.parent.parent
    return init_project(project_dir)


@atomic
def memory_file_context(
    conn: turso.Connection,
    path: str,
    reason: str | None = None,
    statement: str | None = None,
    details: str | None = None,
    tags: list[str] | None = None,
    refresh: bool = True,
    title: str | None = None,
) -> dict:
    """Return and refresh one file's current-state TLDR.

    The memory is keyed by project-relative path and content hash. An unchanged
    file returns the cached TLDR with ``fresh: true`` and performs no write.
    """
    root = project_root(conn)
    try:
        file_path = contained_path(path, root)
    except EvidencePathError as e:
        return {"error": str(e)}
    if not file_path.is_file():
        return {"error": f"File not found: {path}"}
    try:
        text = file_path.read_text(encoding="utf-8")
    except (PermissionError, UnicodeDecodeError) as e:
        return {"error": f"Cannot read {path}: {e}"}

    try:
        rel_path = file_path.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        rel_path = file_path.as_posix()
    summary_text = text[:1_000_000]
    summary = summarize_file(rel_path, summary_text)
    blob_hash = hash_content(text)
    content_hash = hash_content(text)

    existing_id = (
        find_locator_item_by_path(conn, rel_path)
        or find_locator_item_by_path(conn, file_path.as_posix())
        or find_locator_item_by_path(conn, path)
    )
    existing = get_item(conn, existing_id) if existing_id else None
    existing_meta = (existing.metadata or {}) if existing else {}
    is_file_state = existing_meta.get("fileKind") == "file_tldr"
    fresh = bool(existing and is_file_state and existing_meta.get("blobHash") == blob_hash)

    symbols = [symbol.to_dict() for symbol in summary.symbols]
    tldr = (statement or summary.tldr).strip()[:800]
    if details is None:
        lines = []
        for symbol in summary.symbols:
            line = f"{symbol.kind} {symbol.signature or symbol.name}"
            if symbol.detail:
                line += f": {symbol.detail}"
            lines.append(line)
        details = "\n".join(lines)[:2000] or None
    item_title = (title or f"File: {rel_path}")[:200]
    item_tags = _normalize_tags(list(dict.fromkeys(
        ["file-tldr", summary.language, *(tags or [])]
    )))
    metadata = {
        "kind": "module",
        "fileKind": "file_tldr",
        "memoryClass": "operational",
        "subject": rel_path,
        "path": rel_path,
        "blobHash": blob_hash,
        "contentHash": content_hash,
        "language": summary.language,
        "lineCount": summary.line_count,
        "byteCount": summary.byte_count,
        "symbols": symbols,
        **({"reason": reason} if reason else {}),
    }

    result = {
        "path": rel_path,
        "title": item_title,
        "tldr": tldr,
        "language": summary.language,
        "symbols": symbols,
        "lineCount": summary.line_count,
        "byteCount": summary.byte_count,
        "blobHash": blob_hash,
        "fresh": fresh,
        "changed": bool(existing and not fresh),
        "created": existing is None,
    }
    if fresh or (existing is not None and not refresh):
        result.update({
            "id": existing.id,
            "tldr": existing.statement if fresh else tldr,
            "updatedAt": existing.updated_at,
        })
        return result

    evidence = Evidence(
        path=rel_path,
        startLine=1,
        endLine=max(1, text.count("\n") + 1),
        contentHash=content_hash,
        kind="source",
        capturedAt=_now(),
        blobHash=blob_hash,
    )
    if existing is not None:
        update_item_row(
            conn,
            existing.id,
            {
                "title": item_title,
                "statement": tldr,
                "details": details,
                "tags": json.dumps(item_tags),
                "evidence": json.dumps([evidence.model_dump(by_alias=True)]),
                "metadata": json.dumps(metadata),
                "importance": 0.4,
                "confidence": 0.9,
                "asserted_by": "source",
                "updated_at": _now(),
            },
        )
        upsert_locator(conn, existing.id, rel_path, "file_tldr")
        _link_item_entities(conn, existing.id, metadata)
        insert_history(
            conn, existing.id, "file_context_refreshed",
            reason=reason or "file content changed",
            source="memory_file_context",
        )
        result.update({"id": existing.id, "updatedAt": _now()})
        return result

    item = MemoryItem(
        type=MemoryType.IMPLEMENTATION,
        title=item_title,
        statement=tldr,
        details=details,
        tags=item_tags,
        evidence=[evidence],
        metadata=metadata,
        asserted_by="source",
        confidence=0.9,
        importance=0.4,
    )
    insert_item(conn, item)
    _link_item_entities(conn, item.id, metadata)
    try:
        upsert_locator(conn, item.id, rel_path, "file_tldr")
    except turso.IntegrityError:
        conn.execute("DELETE FROM memory_items WHERE id = ?", (item.id,))
        return memory_file_context(
            conn, path, reason=reason, statement=statement,
            details=details, tags=tags, refresh=refresh, title=title,
        )
    insert_history(
        conn, item.id, "file_context_created",
        reason=reason or "initial file TLDR", source="memory_file_context",
    )
    result.update({"id": item.id, "updatedAt": _now()})
    return result


@atomic
def memory_session_summary(
    conn: turso.Connection,
    session_id: str,
    summary: str,
    files: list[str] | None = None,
    commit: str | None = None,
    title: str | None = None,
    reason: str | None = None,
) -> dict:
    """Create or update one compact summary for a work session."""
    text = _clean_summary(summary)[:800]
    if not text:
        raise ValueError("summary is required")
    paths = list(dict.fromkeys(
        str(path).replace("\\", "/") for path in (files or []) if str(path).strip()
    ))
    commit = commit or get_head_commit()
    details = "\n".join(f"- {path}" for path in paths[:80])[:2000] or None
    item_title = (title or f"Session: {text[:80]}")[:200]
    metadata = {
        "observation": text,
        "kind": "session_summary",
        "memoryClass": "episodic",
        "sessionId": session_id,
        "files": paths,
        **({"commit": commit} if commit else {}),
        **({"reason": reason} if reason else {}),
    }
    existing = None
    if session_id:
        row = conn.execute(
            "SELECT id FROM memory_items "
            "WHERE status != 'deleted' "
            "AND json_extract(metadata, '$.kind') = 'session_summary' "
            "AND json_extract(metadata, '$.sessionId') = ? "
            "ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        existing = get_item(conn, row[0]) if row else None

    if existing is not None:
        update_item_row(
            conn,
            existing.id,
            {
                "title": item_title,
                "statement": text,
                "details": details,
                "metadata": json.dumps(metadata),
                "updated_at": _now(),
            },
        )
        insert_history(
            conn, existing.id, "session_summary_updated",
            reason=reason or "session progressed", source="memory_session_summary",
        )
        _link_item_entities(conn, existing.id, metadata)
        linked = _link_session_files(conn, existing.id, paths)
        return {
            "id": existing.id, "action": "updated", "sessionId": session_id,
            "files": paths, "commit": commit, "linked": linked,
        }

    item = MemoryItem(
        type=MemoryType.OBSERVATION,
        title=item_title,
        statement=text,
        details=details,
        tags=["session-summary", "work-log"],
        metadata=metadata,
        asserted_by="agent",
        confidence=0.8,
        importance=0.5,
    )
    insert_item(conn, item)
    _link_item_entities(conn, item.id, metadata)
    insert_history(
        conn, item.id, "session_summary_created",
        reason=reason or "session summary", source="memory_session_summary",
    )
    linked = _link_session_files(conn, item.id, paths)
    return {
        "id": item.id, "action": "created", "sessionId": session_id,
        "files": paths, "commit": commit, "linked": linked,
    }


def _link_session_files(
    conn: turso.Connection, session_id: str, paths: list[str]
) -> int:
    """Link one session summary to the file-state memories it touched."""
    linked = 0
    root = project_root(conn)
    for raw in paths[:80]:
        normalized = raw.replace("\\", "/")
        if root is not None and Path(raw).is_absolute():
            try:
                normalized = Path(raw).resolve().relative_to(
                    Path(root).resolve()
                ).as_posix()
            except ValueError:
                pass
        file_id = (
            find_locator_item_by_path(conn, normalized)
            or find_locator_item_by_path(conn, raw)
        )
        if not file_id or file_id == session_id:
            continue
        try:
            insert_relation(conn, session_id, file_id, "touches")
            linked += 1
        except turso.IntegrityError:
            pass
    return linked


@atomic
def memory_workflow(
    conn: turso.Connection,
    name: str,
    steps: list[str],
    preconditions: list[str] | None = None,
    verification: list[str] | None = None,
    failure_modes: list[str] | None = None,
    tags: list[str] | None = None,
    summary: str | None = None,
    scope: str = "project",
    asserted_by: str = "agent",
) -> dict:
    """Create or update one reusable procedure."""
    clean_steps = [str(step).strip() for step in steps if str(step).strip()]
    if not name.strip() or not clean_steps:
        raise ValueError("workflow name and at least one step are required")
    text = summary or f"{name}: " + " -> ".join(clean_steps[:6])
    metadata = {
        "kind": "workflow",
        "memoryClass": "procedural",
        "name": name.strip(),
        "component": f"workflow:{name.strip()}",
        "responsibility": text[:500],
        "steps": clean_steps[:40],
        "preconditions": preconditions or [],
        "verification": verification or [],
        "failureModes": failure_modes or [],
    }
    row = conn.execute(
        "SELECT id FROM memory_items WHERE status != 'deleted' "
        "AND json_extract(metadata, '$.kind') = 'workflow' "
        "AND json_extract(metadata, '$.name') = ? LIMIT 1",
        (name.strip(),),
    ).fetchone()
    if row is not None:
        update_item_row(
            conn, row[0],
            {
                "title": f"Workflow: {name.strip()}"[:200],
                "statement": text[:2000],
                "details": "\n".join(f"- {step}" for step in clean_steps)[:2000],
                "metadata": json.dumps(metadata),
                "updated_at": _now(),
            },
        )
        _link_item_entities(conn, row[0], metadata)
        insert_history(conn, row[0], "workflow_updated", source="memory_workflow")
        return {"id": row[0], "action": "updated", "name": name.strip()}
    created = memory_create(
        conn,
        type="architecture",
        title=f"Workflow: {name.strip()}",
        statement=text,
        tags=["workflow", *(tags or [])],
        details="\n".join(f"- {step}" for step in clean_steps),
        metadata=metadata,
        asserted_by=asserted_by,
        scope=scope,
        importance=0.6,
    )
    return {"id": created["id"], "action": "created", "name": name.strip()}


@atomic
def memory_profile(
    conn: turso.Connection,
    section: str,
    content: str,
    source: str = "user",
) -> dict:
    """Create or update one always-on core profile block."""
    text = " ".join(content.split())[:2000]
    if not section.strip() or not text:
        raise ValueError("profile section and content are required")
    metadata = {
        "kind": "core_profile",
        "memoryClass": "core",
        "observation": text,
        "section": section.strip(),
        "subject": section.strip(),
    }
    row = conn.execute(
        "SELECT id FROM memory_items WHERE status != 'deleted' "
        "AND json_extract(metadata, '$.kind') = 'core_profile' "
        "AND json_extract(metadata, '$.section') = ? LIMIT 1",
        (section.strip(),),
    ).fetchone()
    if row is not None:
        update_item_row(
            conn, row[0],
            {
                "statement": text,
                "metadata": json.dumps(metadata),
                "updated_at": _now(),
            },
        )
        insert_history(conn, row[0], "profile_updated", source="memory_profile")
        return {"id": row[0], "action": "updated", "section": section.strip()}
    created = memory_create(
        conn,
        type="observation",
        title=f"Profile: {section.strip()}"[:200],
        statement=text,
        tags=["core-profile", f"profile:{section.strip()}"],
        metadata=metadata,
        asserted_by=source,
        scope="user",
        importance=0.9,
    )
    return {"id": created["id"], "action": "created", "section": section.strip()}


def memory_graph(
    conn: turso.Connection,
    entity: str | None = None,
    limit: int = 50,
) -> dict:
    """Return the entity graph around one name, or the whole graph summary."""
    limit = max(1, min(int(limit), 200))
    rows = conn.execute(
        "SELECT e.id, e.kind, e.name, COUNT(l.item_id) "
        "FROM memory_entities e LEFT JOIN memory_entity_links l ON l.entity_id = e.id "
        "GROUP BY e.id, e.kind, e.name ORDER BY e.kind, e.name LIMIT ?",
        (limit,),
    ).fetchall()
    entities = [
        {"id": row[0], "kind": row[1], "name": row[2], "items": int(row[3])}
        for row in rows
        if entity is None or entity.lower() in row[2].lower()
    ]
    if entity is not None and not entities:
        return {"entity": entity, "entities": [], "items": [], "relations": []}
    selected = entities[:limit]
    item_ids: set[str] = set()
    for node in selected:
        item_ids.update(get_items_for_entity(conn, node["id"]))
    items = []
    for item_id in list(item_ids)[:limit]:
        item = get_item(conn, item_id)
        if item is not None:
            items.append({
                "id": item.id,
                "type": item.type.value,
                "title": item.title,
                "memoryClass": _memory_class(item.type.value, item.metadata),
            })
    relations = [
        relation for relation in get_all_relations(conn)
        if relation["from_id"] in item_ids or relation["to_id"] in item_ids
    ][:limit]
    return {"entity": entity, "entities": selected, "items": items, "relations": relations}


@atomic
def memory_rollup(
    conn: turso.Connection,
    path_prefix: str = "",
    apply: bool = False,
    min_files: int = 2,
) -> dict:
    """Summarize file TLDRs into module and project-level memories."""
    items = get_all_items(conn)
    files = [
        item for item in items
        if _is_file_state_item(item)
        and str((item.metadata or {}).get("path") or "").startswith(path_prefix)
    ]
    groups: dict[str, list] = {}
    for item in files:
        rel = str(item.metadata.get("path"))
        parent = str(Path(rel).parent)
        groups.setdefault("." if parent in ("", ".") else parent, []).append(item)
    proposed = []
    for prefix, members in sorted(groups.items()):
        if len(members) < min_files:
            continue
        summaries = []
        for item in members[:8]:
            statement = item.statement.split(". ")[0].strip()
            summaries.append(f"{Path(item.metadata['path']).name}: {statement}")
        text = "; ".join(summaries)[:2000]
        proposed.append({"prefix": prefix, "fileCount": len(members), "summary": text})
    if apply:
        for proposal in proposed:
            _upsert_rollup(
                conn, proposal["prefix"], "module_summary",
                f"Module: {proposal['prefix']}",
                proposal["summary"],
                {"pathPrefix": proposal["prefix"], "fileCount": proposal["fileCount"]},
            )
        project_text = "; ".join(
            f"{proposal['prefix']} ({proposal['fileCount']} files)"
            for proposal in proposed[:20]
        ) or "No module summaries yet."
        _upsert_rollup(
            conn, "", "project_summary", "Project memory map",
            project_text,
            {"pathPrefix": "", "moduleCount": len(proposed)},
        )
    return {"apply": apply, "modules": proposed, "count": len(proposed)}


def _upsert_rollup(
    conn: turso.Connection,
    prefix: str,
    kind: str,
    title: str,
    statement: str,
    metadata: dict,
) -> None:
    meta = {
        "kind": kind,
        "memoryClass": "semantic",
        "subject": prefix or "project",
        "component": f"rollup:{prefix or 'project'}",
        "responsibility": statement[:500],
        **metadata,
    }
    row = conn.execute(
        "SELECT id FROM memory_items WHERE status != 'deleted' "
        "AND json_extract(metadata, '$.kind') = ? "
        "AND json_extract(metadata, '$.pathPrefix') = ? LIMIT 1",
        (kind, prefix),
    ).fetchone()
    if row is not None:
        update_item_row(
            conn, row[0],
            {
                "title": title[:200],
                "statement": statement[:2000],
                "metadata": json.dumps(meta),
                "updated_at": _now(),
            },
        )
        insert_history(conn, row[0], "rollup_updated", source="memory_rollup")
        return
    memory_create(
        conn,
        type="architecture",
        title=title,
        statement=statement,
        tags=["rollup", kind],
        metadata=meta,
        asserted_by="source",
        importance=0.6,
    )


def memory_heat(conn: turso.Connection, limit: int = 20) -> dict:
    """Rank memories by recency, importance, and observed access."""
    now = datetime.now(timezone.utc)
    scored = []
    for item in get_all_items(conn):
        try:
            updated = datetime.fromisoformat(item.updated_at.replace("Z", "+00:00"))
        except ValueError:
            updated = now
        age_days = max(0.0, (now - updated).total_seconds() / 86400)
        recency = pow(0.5, age_days / 30)
        access = 0.0
        row = conn.execute(
            "SELECT access_count, last_accessed_at FROM memory_items WHERE id = ?",
            (item.id,),
        ).fetchone()
        if row is not None and row[0]:
            access = min(1.0, float(row[0]) / 20.0)
        score = 0.55 * recency + 0.30 * item.importance + 0.15 * access
        scored.append({
            "id": item.id,
            "title": item.title,
            "memoryClass": _memory_class(item.type.value, item.metadata),
            "heat": round(score, 3),
            "ageDays": round(age_days, 1),
        })
    scored.sort(key=lambda entry: entry["heat"], reverse=True)
    return {"count": len(scored), "top": scored[:limit], "cold": scored[-limit:]}


@atomic
def memory_touch(conn: turso.Connection, ids: list[str]) -> dict:
    """Record that memories were read, feeding the heat score."""
    touched = 0
    now = _now()
    for item_id in list(dict.fromkeys(ids))[:100]:
        row = conn.execute(
            "SELECT id FROM memory_items WHERE id = ? AND status != 'deleted'",
            (item_id,),
        ).fetchone()
        if row is None:
            continue
        conn.execute(
            "UPDATE memory_items SET access_count = COALESCE(access_count, 0) + 1, "
            "last_accessed_at = ? WHERE id = ?",
            (now, item_id),
        )
        touched += 1
    return {"touched": touched, "ids": ids[:100]}


@atomic
def memory_decay(
    conn: turso.Connection,
    days: int = 180,
    dry_run: bool = True,
    min_importance: float = 0.5,
) -> dict:
    """Archive low-heat operational and episodic memories past their TTL."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    candidates = []
    for item in get_all_items(conn):
        if item.importance >= min_importance:
            continue
        if _memory_class(item.type.value, item.metadata) not in ("operational", "episodic"):
            continue
        try:
            updated = datetime.fromisoformat(item.updated_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if updated < cutoff:
            candidates.append(item)
    if not dry_run:
        for item in candidates:
            soft_delete(conn, item.id)
            insert_history(
                conn, item.id, "decayed", reason=f"older than {days} days",
                source="memory_decay",
            )
    return {
        "dry_run": dry_run,
        "days": days,
        "count": len(candidates),
        "ids": [item.id for item in candidates],
    }


@atomic
def memory_induce_workflow(
    conn: turso.Connection,
    session_id: str,
    apply: bool = False,
    name: str | None = None,
) -> dict:
    """Turn one session summary into a reusable workflow draft."""
    row = conn.execute(
        "SELECT id FROM memory_items WHERE status != 'deleted' "
        "AND json_extract(metadata, '$.kind') = 'session_summary' "
        "AND json_extract(metadata, '$.sessionId') = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"session summary not found: {session_id}")
    item = get_item(conn, row[0])
    metadata = item.metadata or {}
    files = [str(path) for path in metadata.get("files") or []]
    steps = [f"Inspect {path}" for path in files] + [
        "Apply the change",
        "Run the relevant tests",
        "Record the outcome",
    ]
    proposed = {
        "name": name or item.title.removeprefix("Session: ")[:80],
        "steps": steps,
        "summary": item.statement,
        "sessionId": session_id,
    }
    if not apply:
        return {"apply": False, "proposed": proposed}
    created = memory_workflow(
        conn, proposed["name"], steps,
        verification=["Relevant tests pass", "Audit remains clean"],
        summary=proposed["summary"],
        tags=[f"session:{session_id}"],
    )
    return {"apply": True, **created}


def memory_maintenance(conn: turso.Connection, apply: bool = False) -> dict:
    """Run the background consolidation cycle in one call."""
    return {
        "entities": memory_backfill_entities(conn),
        "revalidate": memory_revalidate(conn, dry_run=not apply),
        "rollup": memory_rollup(conn, apply=apply),
        "decay": memory_decay(conn, dry_run=not apply),
        "consolidate": memory_consolidate(conn, apply=apply),
    }


@atomic
def memory_backfill_entities(conn: turso.Connection) -> dict:
    """Link existing memories to entities without changing their statements."""
    linked = 0
    for item in get_all_items(conn):
        linked += len(_link_item_entities(
            conn, item.id, {**(item.metadata or {}), "tags": item.tags},
        ))
    return {"links": linked, "entities": len(get_all_entities(conn))}


def _clean_summary(text: str) -> str:
    return " ".join((text or "").split())


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
    try:
        file_path = contained_path(path, project_root(conn))
    except EvidencePathError as e:
        return {"error": str(e)}
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
    try:
        file_path = contained_path(path, project_root(conn))
    except EvidencePathError as e:
        return {"error": str(e)}
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
    change_snapshot = _git_change_snapshot(project_root(conn))

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
        meta.update(change_snapshot)
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
            **change_snapshot,
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
                **change_snapshot,
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
            **change_snapshot,
            "evidence": {"path": path, "startLine": actual_start, "endLine": actual_end, "contentHash": content_hash, "symbolHash": symbol_hash},
        }
