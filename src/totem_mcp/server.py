"""MCP server exposing totem tools (§43)."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .calibration import validate_and_promote
from .context import engineering_context
from .db import db_connection
from .db import get_all_items, list_task_items, list_command_items
from .embeddings import status as embedding_status
from .errors import error_response, not_found
from .eval_bench import auto_cases, evaluate, gate, load_cases
from .hardening import harden_registry, tool_manifest
from .tools import (
    memory_audit,
    memory_quality,
    memory_check_record,
    memory_consolidate,
    memory_create,
    memory_feedback,
    memory_feedback_list,
    memory_file_context,
    memory_history,
    memory_relate,
    memory_relations,
    memory_session_summary,
    memory_delete,
    memory_export,
    memory_gc,
    memory_get,
    memory_import,
    memory_list,
    memory_recent,
    memory_revalidate,
    memory_search,
    memory_train_models,
    memory_update,
    memory_verify,
    register_file_read,
    register_file_write,
    resolve_conflict,
    totem_init,
)

mcp = FastMCP("totem")


@mcp.tool()
def totem_init_tool(project: str | None = None) -> str:
    """Initialize totem for a project. Creates .totem/ directory and DB if missing.

    Args:
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    try:
        result = totem_init(project=project)
        return json.dumps(result, indent=2)
    except Exception as e:
        return error_response(e)


@mcp.tool()
def memory_create_tool(
    type: str,
    title: str,
    statement: str,
    tags: list[str],
    details: str | None = None,
    confidence: float | None = None,
    importance: float = 0.5,
    evidence: list[dict[str, Any]] | None = None,
    related_memory_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    asserted_by: str | None = None,
    applicability: str | None = None,
    scope: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    supersedes_id: str | None = None,
    project: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> str:
    """Create a new memory item.

    Args:
        type: One of: decision, invariant, gotcha, rejected_idea, assumption, open_question, ambiguity, contract, constraint, hypothesis, observation, bug, architecture, implementation
        title: Short title for the memory
        statement: The factual claim being stored
        tags: At least one tag for categorization
        details: Optional additional details
        confidence: 0 to 1; derived from asserted_by when omitted (user 1.0, test 0.95, source/git/doc 0.9, runtime 0.7, agent 0.6; hypotheses cap at 0.4)
        importance: 0 to 1, default 0.5
        evidence: List of evidence objects with path, startLine, endLine, contentHash
        related_memory_ids: IDs of related memory items
        metadata: Extra metadata. Decision items accept 'rationale' (strongly recommended: explain WHY this decision was made, alternatives considered). Invariant items require 'verificationMethod' and 'condition'
        asserted_by: Who established the claim: user, test, source, git, doc, runtime, agent (default agent)
        applicability: current, legacy, deprecated, planned (orthogonal to status)
        scope: user, project, path, task — or JSON {"kind": "...", "value": "..."}
        valid_from: ISO timestamp when the claim starts being true
        valid_to: ISO timestamp when the claim stops being true
        supersedes_id: ID of a memory this one replaces (marks it superseded)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_create(
                conn,
                type=type,
                title=title,
                statement=statement,
                tags=tags,
                details=details,
                confidence=confidence,
                importance=importance,
                evidence=evidence,
                related_memory_ids=related_memory_ids,
                metadata=metadata,
                asserted_by=asserted_by,
                applicability=applicability,
                scope=scope,
                valid_from=valid_from,
                valid_to=valid_to,
                supersedes_id=supersedes_id,
                actor=actor,
                session=session,
                request_id=request_id,
                operation_id=operation_id,
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_relate_tool(
    from_id: str,
    to_id: str,
    kind: str,
    project: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> str:
    """Create a typed relation between two memories.

    Args:
        from_id: Source memory ID
        to_id: Target memory ID
        kind: supersedes, contradicts, invalidates, derived_from, verified_by, refines, depends_on (supersedes/invalidates update the target status)
        project: Optional project root path.
    """
    with db_connection(project=project) as conn:
        try:
            return json.dumps(
                memory_relate(
                    conn, from_id, to_id, kind,
                    actor=actor, session=session, request_id=request_id, operation_id=operation_id,
                ),
                indent=2,
            )
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_relations_tool(id: str, project: str | None = None) -> str:
    """List the typed relations involving a memory."""
    with db_connection(project=project) as conn:
        try:
            return json.dumps(memory_relations(conn, id), indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_history_tool(id: str, project: str | None = None) -> str:
    """Show the immutable timeline of a memory: created, updated fields, deleted."""
    with db_connection(project=project) as conn:
        try:
            return json.dumps(memory_history(conn, id), indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_get_tool(id: str, include_evidence: bool = True, project: str | None = None) -> str:
    """Retrieve a memory item by ID with staleness check.

    Args:
        id: The memory item ID
        include_evidence: Whether to check evidence staleness (default True)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    try:
        with db_connection(project=project) as conn:
            result = memory_get(conn, id, include_evidence=include_evidence)
            if result is None:
                return not_found(f"Item {id} not found")
            return json.dumps(result, indent=2)
    except Exception as e:
        return error_response(e)


@mcp.tool()
def memory_update_tool(
    id: str,
    reason: str | None = None,
    title: str | None = None,
    statement: str | None = None,
    details: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    confidence: float | None = None,
    importance: float | None = None,
    evidence: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    project: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> str:
    """Update a memory item. Reason is optional but strongly recommended.

    Args:
        id: The memory item ID
        reason: Why this update was made (strongly recommended for audit trail; defaults to 'maintenance')
        title: New title
        statement: New statement
        details: New details
        tags: New tags
        status: New status (active, potentially_stale, invalidated, resolved, superseded)
        confidence: New confidence (0-1)
        importance: New importance (0-1)
        evidence: New evidence list
        metadata: New metadata
        valid_from: New validity start (empty string clears it)
        valid_to: New validity end (empty string clears it)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_update(
                conn,
                id=id,
                reason=reason,
                title=title,
                statement=statement,
                details=details,
                tags=tags,
                status=status,
                confidence=confidence,
                importance=importance,
                evidence=evidence,
                metadata=metadata,
                valid_from=valid_from,
                valid_to=valid_to,
                actor=actor,
                session=session,
                request_id=request_id,
                operation_id=operation_id,
            )
            if result is None:
                return not_found(f"Item {id} not found")
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_delete_tool(
    id: str,
    reason: str,
    project: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> str:
    """Soft-delete a memory item. Reason is required.

    Args:
        id: The memory item ID
        reason: Required reason for deletion
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_delete(
                conn, id, reason,
                actor=actor, session=session, request_id=request_id, operation_id=operation_id,
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_verify_tool(
    id: str,
    method: str | None = None,
    commit: str | None = None,
    confidence: float = 1.0,
    verified_by_id: str | None = None,
    check_id: str | None = None,
    project: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> str:
    """Record that a memory was verified: sets verified_at and verified_commit.

    Verification, not provenance, is what earns confidence 1.0. Pass
    ``check_id`` from ``memory_check_record_tool`` for evidence-derived
    verification; without it the verification is recorded as an assertion.

    Args:
        id: The memory item ID to mark verified
        method: How it was verified (stored in history)
        commit: Git commit verified at (defaults to HEAD)
        confidence: Confidence to set (default 1.0)
        verified_by_id: Optional memory ID holding the verification artifact
        check_id: Recorded check ID that justifies the verification
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_verify(
                conn, id, method=method, commit=commit, confidence=confidence,
                verified_by_id=verified_by_id, check_id=check_id,
                actor=actor, session=session,
                request_id=request_id, operation_id=operation_id,
            )
            if result is None:
                return not_found(f"Item {id} not found")
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_check_record_tool(
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
    project: str | None = None,
) -> str:
    """Record an observed check (command, exit code, output hash) for a memory.

    Args:
        id: The memory item the check supports
        name: Short check name, e.g. "pytest tests/test_auth.py"
        command: The command that was run
        cwd: Working directory the command ran in
        exit_code: Observed exit code (0 is a pass)
        output: Optional captured output (hashed, not stored verbatim)
        output_hash: Pre-computed SHA256 of the output
        evidence_hash: Evidence digest the check ran against (defaults to now)
        commit: Git commit the check ran at (defaults to HEAD)
        ran_at: ISO timestamp of the run
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_check_record(
                conn, id, name, command=command, cwd=cwd, exit_code=exit_code,
                output=output, output_hash=output_hash,
                evidence_hash=evidence_hash, commit=commit, ran_at=ran_at,
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_audit_tool(project: str | None = None) -> str:
    """Verify the history hash chain and each item's recorded state digest."""
    with db_connection(project=project) as conn:
        try:
            return json.dumps(memory_audit(conn), indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_quality_tool(project: str | None = None) -> str:
    """Report signal ratio, provenance, stale evidence, conflicts, and digest quality."""
    with db_connection(project=project) as conn:
        try:
            return json.dumps(memory_quality(conn), indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def totem_manifest_tool() -> str:
    """Return the stable digest of the registered tool surface."""
    return json.dumps(tool_manifest(mcp._tool_manager._tools), indent=2)


@mcp.tool()
def totem_embedding_status_tool() -> str:
    """Report the configured retrieval encoder and its fallback state."""
    return json.dumps(embedding_status(), indent=2)


@mcp.tool()
def totem_eval_tool(
    cases_file: str | None = None,
    k: int = 10,
    baseline_file: str | None = None,
    tolerance: float = 0.02,
    p95_ms: float = 2000.0,
    project: str | None = None,
) -> str:
    """Evaluate context ranking on a golden case file (or an auto smoke set).

    Args:
        cases_file: JSON file of cases; omitted means derive cases from memories
        k: Cutoff for recall@k
        baseline_file: Optional prior report to gate against
        tolerance: Allowed recall/MRR regression
        p95_ms: Latency budget for the gate
        project: Optional project root path
    """
    with db_connection(project=project) as conn:
        try:
            cases = (
                load_cases(cases_file) if cases_file
                else auto_cases(get_all_items(conn))
            )
            report = evaluate(
                cases,
                lambda case: engineering_context(
                    conn, tags=case.tags, task=case.task or None,
                    paths=case.paths or None,
                ),
                k=k,
            )
            if baseline_file:
                import json as _json
                baseline = _json.loads(open(baseline_file, encoding="utf-8").read())
                report["gate"] = gate(baseline, report, tolerance, p95_ms)
            return json.dumps(report, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def totem_validate_models_tool(
    min_test: int = 20,
    tolerance: float = 0.1,
    project: str | None = None,
) -> str:
    """Gate the calibrated trust head on a temporal held-out slice."""
    with db_connection(project=project) as conn:
        try:
            return json.dumps(
                validate_and_promote(conn, min_test=min_test, tolerance=tolerance),
                indent=2,
            )
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_revalidate_tool(
    dry_run: bool = True,
    project: str | None = None,
    actor: str | None = None,
) -> str:
    """Void verifications whose evidence changed since they were made.

    Args:
        dry_run: Report candidates without changing them (default True)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_revalidate(conn, dry_run=dry_run, actor=actor)
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_gc_tool(
    retention_days: int = 90,
    dry_run: bool = True,
    project: str | None = None,
    actor: str | None = None,
) -> str:
    """Purge terminal-state memories older than a retention window.

    Only superseded/invalidated/resolved items untouched for ``retention_days``
    and not referenced by any relation are eligible. Defaults to dry-run.

    Args:
        retention_days: Age threshold in days (default 90)
        dry_run: Report candidates without deleting (default True)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_gc(
                conn, retention_days=retention_days, dry_run=dry_run, actor=actor,
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_list_tool(
    type: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    sort: str | None = None,
    limit: int = 50,
    project: str | None = None,
) -> str:
    """List memory items with optional filters.

    Args:
        type: Filter by type (decision, invariant, gotcha, rejected_idea)
        tags: Filter by tags (items must have at least one)
        status: Filter by status (active, potentially_stale, invalidated)
        sort: Sort by 'created_at', 'updated_at' (default), or 'importance'
        limit: Maximum items to return (default 50)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_list(conn, type=type, tags=tags, status=status, sort=sort or "updated_at", limit=limit)
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_recent_tool(limit: int = 5, project: str | None = None) -> str:
    """List most recently created memories.

    Args:
        limit: Maximum items to return (default 5)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_recent(conn, limit=limit)
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_tasks_tool(limit: int = 10, project: str | None = None) -> str:
    """List in-progress task memories (tagged with task:*).

    Args:
        limit: Maximum items to return (default 10)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            items = list_task_items(conn, limit=limit)
            return json.dumps([item.model_dump(by_alias=True) for item in items], indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_commands_tool(limit: int = 20, project: str | None = None) -> str:
    """List command outcome memories (gotchas tagged with cmd:*).

    Args:
        limit: Maximum items to return (default 20)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            items = list_command_items(conn, limit=limit)
            return json.dumps([item.model_dump(by_alias=True) for item in items], indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def resolve_conflict_tool(
    conflict_id: str,
    resolution: str,
    project: str | None = None,
    actor: str | None = None,
    session: str | None = None,
    request_id: str | None = None,
    operation_id: str | None = None,
) -> str:
    """Mark a conflict as resolved.

    Args:
        conflict_id: The conflict ID
        resolution: Description of how the conflict was resolved (e.g. 'Kept existing: X')
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = resolve_conflict(
                conn, conflict_id, resolution,
                actor=actor, session=session, request_id=request_id, operation_id=operation_id,
            )
            if result is None:
                return not_found(f"Conflict {conflict_id} not found")
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_search_tool(
    query: str,
    types: list[str] | None = None,
    tags: list[str] | None = None,
    include_stale: bool = False,
    limit: int = 20,
    as_of: str | None = None,
    include_expired: bool = False,
    include_file_state: bool = False,
    project: str | None = None,
) -> str:
    """Hybrid tag + full-text search.

    Args:
        query: Search query (full-text via Turso FTS5)
        types: Filter by memory types
        tags: Filter by tags
        include_stale: Whether to include potentially stale items
        limit: Maximum results (default 20)
        as_of: Evaluate temporal validity at this ISO timestamp
        include_expired: Include memories outside their validity window
        include_file_state: Include file-state TLDRs in generic search results
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_search(
                conn,
                query=query,
                types=types,
                tags=tags,
                include_stale=include_stale,
                limit=limit,
                as_of=as_of,
                include_expired=include_expired,
                include_file_state=include_file_state,
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_feedback_tool(
    id: str,
    signal: str,
    query: str | None = None,
    tags: list[str] | None = None,
    weight: float = 1.0,
    project: str | None = None,
) -> str:
    """Record whether a memory was useful, wrong, irrelevant, or stale.

    Args:
        id: Memory item id
        signal: useful, wrong, irrelevant, verified, or stale
        query: Task or query the item was considered for
        tags: Tags supplied with that query
        weight: Relative weight in [0, 10] (default 1)
        project: Optional project root path
    """
    with db_connection(project=project) as conn:
        try:
            return json.dumps(
                memory_feedback(
                    conn, id, signal, query=query, tags=tags, weight=weight
                ),
                indent=2,
            )
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_feedback_list_tool(
    id: str | None = None,
    limit: int = 200,
    project: str | None = None,
) -> str:
    """List recorded retrieval feedback."""
    with db_connection(project=project) as conn:
        try:
            return json.dumps(
                memory_feedback_list(conn, id=id, limit=limit), indent=2
            )
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_train_models_tool(
    min_examples: int = 24,
    project: str | None = None,
) -> str:
    """Fit the reranker and calibrator from recorded feedback."""
    with db_connection(project=project) as conn:
        try:
            return json.dumps(
                memory_train_models(conn, min_examples=min_examples), indent=2
            )
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_consolidate_tool(
    apply: bool = False,
    threshold: float = 0.92,
    project: str | None = None,
) -> str:
    """Cluster near-duplicate memories and optionally supersede duplicates.

    Args:
        apply: Apply the consolidation (default is a dry run)
        threshold: Cosine similarity needed to join a cluster (default 0.92)
        project: Optional project root path
    """
    with db_connection(project=project) as conn:
        try:
            return json.dumps(
                memory_consolidate(conn, apply=apply, threshold=threshold),
                indent=2,
            )
        except Exception as e:
            return error_response(e)


@mcp.tool()
def engineering_context_tool(
    tags: list[str],
    task: str | None = None,
    token_budget: int | None = None,
    types: list[str] | None = None,
    include_stale: bool = False,
    current_task: str | None = None,
    paths: list[str] | None = None,
    max_item_chars: int | None = None,
    as_of: str | None = None,
    include_expired: bool = False,
    project: str | None = None,
) -> str:
    """Assemble engineering context with output ordering.

    Pipeline: tag match -> score -> sort -> truncate -> serialize.
    Conflicts and warnings are NEVER dropped for budget.

    Args:
        tags: Tags to match against
        task: Optional task description (shown in output header)
        token_budget: Optional token budget for truncation
        types: Filter by memory types
        include_stale: Whether to include potentially stale items
        current_task: Description of what you're working on right now. Boosts scoring for memories relevant to this task.
        max_item_chars: Cap a single item so it cannot dominate the budget
        as_of: Evaluate temporal validity at this ISO timestamp
        include_expired: Include memories outside their validity window
        paths: Filter items whose evidence matches these file paths
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = engineering_context(
                conn,
                tags=tags,
                task=task,
                token_budget=token_budget,
                types=types,
                include_stale=include_stale,
                current_task=current_task,
                paths=paths,
                max_item_chars=max_item_chars,
                as_of=as_of,
                include_expired=include_expired,
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_export_tool(project: str | None = None) -> str:
    """Export all memories and conflicts as portable JSON.

    Args:
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_export(conn)
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_import_tool(data: dict[str, Any], project: str | None = None) -> str:
    """Import memories from a previously exported dict. Skips duplicate IDs.

    Args:
        data: The export dict containing 'items' and optionally 'conflicts'
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_import(conn, data)
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_file_context_tool(
    path: str,
    reason: str | None = None,
    statement: str | None = None,
    details: str | None = None,
    tags: list[str] | None = None,
    refresh: bool = True,
    title: str | None = None,
    project: str | None = None,
) -> str:
    """Return and refresh a file's current-state TLDR, keyed by content hash.

    Use this after reading a file to store what the file now does, or before a
    read to check whether a cached TLDR is still fresh. An unchanged file
    returns ``fresh: true`` without writing.

    Args:
        path: File path, relative to the project or absolute
        reason: Why the file state was captured
        statement: Optional override for the generated TLDR
        details: Optional additional details
        tags: Extra tags
        refresh: Update the stored TLDR when the content changed
        title: Optional title override
        project: Optional project root path
    """
    with db_connection(project=project) as conn:
        try:
            return json.dumps(
                memory_file_context(
                    conn, path=path, reason=reason, statement=statement,
                    details=details, tags=tags, refresh=refresh, title=title,
                ),
                indent=2,
            )
        except Exception as e:
            return error_response(e)


@mcp.tool()
def memory_session_summary_tool(
    session_id: str,
    summary: str,
    files: list[str] | None = None,
    commit: str | None = None,
    title: str | None = None,
    reason: str | None = None,
    project: str | None = None,
) -> str:
    """Create or update one compact summary for a work session.

    Args:
        session_id: Stable session id; repeat calls update the same record
        summary: One or two sentences describing what changed and why
        files: Changed file paths
        commit: Git commit, defaults to HEAD
        title: Optional title override
        reason: Optional reason for the update
        project: Optional project root path
    """
    with db_connection(project=project) as conn:
        try:
            return json.dumps(
                memory_session_summary(
                    conn, session_id=session_id, summary=summary,
                    files=files, commit=commit, title=title, reason=reason,
                ),
                indent=2,
            )
        except Exception as e:
            return error_response(e)


@mcp.tool()
def register_file_read_tool(
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
    project: str | None = None,
) -> str:
    """Register facts learned from reading a file. Auto-hashes content, updates existing or creates new.

    Call this after reading a file to store what you learned. Prevents re-reading
    the same file in future sessions.

    Args:
        path: File path (e.g. 'src/auth.py')
        statement: What you learned (e.g. 'getUser() returns User | null, takes user_id: int')
        subject: What the fact is about (e.g. 'getUser()')
        kind: What kind of code fact (api/function/module/type/config/schema)
        tags: Tags for categorization
        start_line: Optional line range start (auto-detected if omitted)
        end_line: Optional line range end (auto-detected if omitted)
        title: Optional title (defaults to 'File: {filename}')
        details: Optional additional details
        symbol: Optional symbol the fact is about (e.g. 'UserService.authenticate'); makes evidence survive line moves
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = register_file_read(
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
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


@mcp.tool()
def register_file_write_tool(
    path: str,
    statement: str,
    reason: str,
    tags: list[str],
    start_line: int | None = None,
    end_line: int | None = None,
    title: str | None = None,
    details: str | None = None,
    symbol: str | None = None,
    project: str | None = None,
) -> str:
    """Register a file write/modification. Auto-hashes content, updates existing or creates new.

    Call this after editing or writing a file to document what changed and why.
    The commit-gate hook will block all other tools until you register the write.

    Args:
        path: File path (e.g. 'src/auth.py')
        statement: What changed (e.g. 'Added input validation to getUser()')
        reason: Why the change was made (e.g. 'Fix: getUser() crashed on null input')
        tags: Tags for categorization
        start_line: Optional line range start
        end_line: Optional line range end
        title: Optional title (defaults to 'File: {filename}')
        details: Optional additional details
        symbol: Optional symbol the change touches
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = register_file_write(
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
            return json.dumps(result, indent=2)
        except Exception as e:
            return error_response(e)


# --- Typed wrapper tools ---


def _createTyped(type_: str, title: str, statement: str, tags: list[str],
                 details: str | None = None, evidence: list[dict[str, Any]] | None = None,
                 related_memory_ids: list[str] | None = None,
                 metadata: dict[str, Any] | None = None,
                 project: str | None = None) -> str:
    """Generic typed create wrapper."""
    try:
        with db_connection(project=project) as conn:
            result = memory_create(conn, type=type_, title=title, statement=statement,
                                   tags=tags, details=details, evidence=evidence,
                                   related_memory_ids=related_memory_ids, metadata=metadata)
            return json.dumps(result, indent=2)
    except Exception as e:
        return error_response(e)


@mcp.tool()
def decision_create(title: str, statement: str, tags: list[str],
                    rationale: str, alternatives: list[dict[str, str]] | None = None,
                    details: str | None = None, project: str | None = None) -> str:
    """Create a decision memory. Always provide rationale explaining WHY."""
    meta: dict[str, Any] = {"rationale": rationale}
    if alternatives:
        meta["alternatives"] = alternatives
    return _createTyped("decision", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def invariant_create(title: str, statement: str, tags: list[str],
                     verification_method: str, condition: str,
                     violation_behavior: str | None = None,
                     details: str | None = None, project: str | None = None) -> str:
    """Create an invariant memory. Requires verificationMethod and condition."""
    meta: dict[str, Any] = {"verificationMethod": verification_method, "condition": condition}
    if violation_behavior:
        meta["violationBehavior"] = violation_behavior
    return _createTyped("invariant", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def gotcha_create(title: str, statement: str, tags: list[str],
                  details: str | None = None, trigger: str | None = None,
                  project: str | None = None) -> str:
    """Create a gotcha memory for non-obvious behaviors."""
    meta: dict[str, Any] = {}
    if trigger:
        meta["trigger"] = trigger
    return _createTyped("gotcha", title, statement, tags, details=details,
                        metadata=meta or None, project=project)


@mcp.tool()
def rejected_idea_create(title: str, statement: str, tags: list[str],
                         proposal: str, reason_rejected: str,
                         replacement: str | None = None,
                         details: str | None = None,
                         project: str | None = None) -> str:
    """Create a rejected idea memory. Prevents re-proposing dead ends."""
    meta: dict[str, Any] = {"proposal": proposal, "reasonRejected": reason_rejected}
    if replacement:
        meta["replacement"] = replacement
    return _createTyped("rejected_idea", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def assumption_create(title: str, statement: str, tags: list[str],
                      claim_category: str, basis: str,
                      verification_needed: bool | None = None,
                      details: str | None = None,
                      project: str | None = None) -> str:
    """Create an assumption memory. classify: fact/assumption/hypothesis/guarantee."""
    meta: dict[str, Any] = {"claimCategory": claim_category, "basis": basis}
    if verification_needed is not None:
        meta["verificationNeeded"] = verification_needed
    return _createTyped("assumption", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def open_question_create(title: str, statement: str, tags: list[str],
                         question: str, impact: str, blocking: bool,
                         possible_answers: list[str] | None = None,
                         details: str | None = None,
                         project: str | None = None) -> str:
    """Create an open question memory. impact: low/medium/high/critical."""
    meta: dict[str, Any] = {"question": question, "impact": impact, "blocking": blocking}
    if possible_answers:
        meta["possibleAnswers"] = possible_answers
    return _createTyped("open_question", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def ambiguity_create(title: str, statement: str, tags: list[str],
                     question: str, interpretations: list[dict[str, str]],
                     impact: str, resolution: str | None = None,
                     details: str | None = None,
                     project: str | None = None) -> str:
    """Create an ambiguity memory. impact: low/medium/high/critical. high/critical = blocking."""
    meta: dict[str, Any] = {"question": question, "interpretations": interpretations, "impact": impact}
    if resolution:
        meta["resolution"] = resolution
    return _createTyped("ambiguity", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def flag_ambiguity(title: str, question: str, tags: list[str],
                   impact: str, interpretations: list[dict[str, str]] | None = None,
                   details: str | None = None,
                   project: str | None = None) -> str:
    """Flag an ambiguity. Convenience wrapper for ambiguity_create. impact: low/medium/high/critical."""
    if not interpretations:
        interpretations = [{"id": "1", "description": "To be determined"}]
    meta: dict[str, Any] = {"question": question, "interpretations": interpretations, "impact": impact}
    return _createTyped("ambiguity", title, question, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def contract_create(title: str, statement: str, tags: list[str],
                    subject: str, inputs: str | None = None,
                    outputs: str | None = None, errors: str | None = None,
                    side_effects: str | None = None, compatibility: str | None = None,
                    details: str | None = None,
                    project: str | None = None) -> str:
    """Create a contract memory for observable behavior of functions/APIs."""
    meta: dict[str, Any] = {"subject": subject}
    if inputs:
        meta["inputs"] = inputs
    if outputs:
        meta["outputs"] = outputs
    if errors:
        meta["errors"] = errors
    if side_effects:
        meta["sideEffects"] = side_effects
    if compatibility:
        meta["compatibility"] = compatibility
    return _createTyped("contract", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def constraint_create(title: str, statement: str, tags: list[str],
                      constraint: str, scope: str | None = None,
                      severity: str | None = None,
                      details: str | None = None,
                      project: str | None = None) -> str:
    """Create a constraint memory for implementation restrictions."""
    meta: dict[str, Any] = {"constraint": constraint}
    if scope:
        meta["scope"] = scope
    if severity:
        meta["severity"] = severity
    return _createTyped("constraint", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def hypothesis_create(title: str, statement: str, tags: list[str],
                      hypothesis: str, evidence_for: list[str] | None = None,
                      evidence_against: list[str] | None = None,
                      confidence: float | None = None,
                      verification_plan: str | None = None,
                      details: str | None = None,
                      project: str | None = None) -> str:
    """Create a hypothesis memory. Never auto-promote to invariant/decision."""
    meta: dict[str, Any] = {"hypothesis": hypothesis}
    if evidence_for:
        meta["evidenceFor"] = evidence_for
    if evidence_against:
        meta["evidenceAgainst"] = evidence_against
    if confidence is not None:
        meta["confidence"] = confidence
    if verification_plan:
        meta["verificationPlan"] = verification_plan
    return _createTyped("hypothesis", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def observation_create(title: str, statement: str, tags: list[str],
                       observation: str, context: str | None = None,
                       confidence: float | None = None,
                       details: str | None = None,
                       project: str | None = None) -> str:
    """Create an observation memory. Never auto-promote to invariant/decision."""
    meta: dict[str, Any] = {"observation": observation}
    if context:
        meta["context"] = context
    if confidence is not None:
        meta["confidence"] = confidence
    return _createTyped("observation", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def bug_create(title: str, statement: str, tags: list[str],
               symptom: str, severity: str,
               expected: str | None = None, actual: str | None = None,
               reproduction: str | None = None,
               suspected_cause: str | None = None,
               details: str | None = None,
               project: str | None = None) -> str:
    """Create a bug memory. State machine: open→confirmed→fixed→verified."""
    meta: dict[str, Any] = {"symptom": symptom, "severity": severity, "state": "open"}
    if expected:
        meta["expected"] = expected
    if actual:
        meta["actual"] = actual
    if reproduction:
        meta["reproduction"] = reproduction
    if suspected_cause:
        meta["suspectedCause"] = suspected_cause
    return _createTyped("bug", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def architecture_create(title: str, statement: str, tags: list[str],
                        component: str, responsibility: str,
                        dependencies: list[str] | None = None,
                        owns: list[str] | None = None,
                        communicates_with: list[str] | None = None,
                        source_paths: list[str] | None = None,
                        details: str | None = None,
                        project: str | None = None) -> str:
    """Create an architecture memory for component structure mapping."""
    meta: dict[str, Any] = {"component": component, "responsibility": responsibility}
    if dependencies:
        meta["dependencies"] = dependencies
    if owns:
        meta["owns"] = owns
    if communicates_with:
        meta["communicatesWith"] = communicates_with
    if source_paths:
        meta["sourcePaths"] = source_paths
    return _createTyped("architecture", title, statement, tags, details=details,
                        metadata=meta, project=project)


@mcp.tool()
def implementation_create(title: str, statement: str, tags: list[str],
                          subject: str, kind: str, path: str,
                          start_line: int | None = None,
                          end_line: int | None = None,
                          content_hash: str | None = None,
                          summary: str | None = None,
                          details: str | None = None,
                          project: str | None = None) -> str:
    """Create an implementation memory for codebase facts (API, function, module, type, config, schema)."""
    meta: dict[str, Any] = {"subject": subject, "kind": kind, "path": path}
    if start_line is not None:
        meta["startLine"] = start_line
    if end_line is not None:
        meta["endLine"] = end_line
    if content_hash:
        meta["contentHash"] = content_hash
    if summary:
        meta["summary"] = summary
    return _createTyped("implementation", title, statement, tags, details=details,
                        metadata=meta, project=project)


def main() -> None:
    harden_registry(mcp._tool_manager._tools)
    mcp.run()


if __name__ == "__main__":
    main()
