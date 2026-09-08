"""MCP server exposing totem tools (§43)."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from .context import engineering_context
from .db import db_connection
from .db import list_task_items, list_command_items
from .tools import (
    memory_create,
    memory_delete,
    memory_export,
    memory_get,
    memory_import,
    memory_list,
    memory_recent,
    memory_search,
    memory_update,
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
        return f"Error: {e}"


@mcp.tool()
def memory_create_tool(
    type: str,
    title: str,
    statement: str,
    tags: list[str],
    details: str | None = None,
    confidence: float = 1.0,
    importance: float = 0.5,
    evidence: list[dict[str, Any]] | None = None,
    related_memory_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    project: str | None = None,
) -> str:
    """Create a new memory item.

    Args:
        type: One of: decision, invariant, gotcha, rejected_idea, assumption, open_question, ambiguity, contract, constraint, hypothesis, observation, bug, architecture, implementation
        title: Short title for the memory
        statement: The factual claim being stored
        tags: At least one tag for categorization
        details: Optional additional details
        confidence: 0 to 1, default 1.0
        importance: 0 to 1, default 0.5
        evidence: List of evidence objects with path, startLine, endLine, contentHash
        related_memory_ids: IDs of related memory items
        metadata: Extra metadata. Decision items accept 'rationale' (strongly recommended: explain WHY this decision was made, alternatives considered). Invariant items require 'verificationMethod' and 'condition'
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
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"


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
                return f"Error: Item {id} not found"
            return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


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
    project: str | None = None,
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
            )
            if result is None:
                return f"Error: Item {id} not found"
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"


@mcp.tool()
def memory_delete_tool(id: str, reason: str, project: str | None = None) -> str:
    """Soft-delete a memory item. Reason is required.

    Args:
        id: The memory item ID
        reason: Required reason for deletion
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = memory_delete(conn, id, reason)
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"


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
            return f"Error: {e}"


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
            return f"Error: {e}"


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
            return f"Error: {e}"


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
            return f"Error: {e}"


@mcp.tool()
def resolve_conflict_tool(conflict_id: str, resolution: str, project: str | None = None) -> str:
    """Mark a conflict as resolved.

    Args:
        conflict_id: The conflict ID
        resolution: Description of how the conflict was resolved (e.g. 'Kept existing: X')
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    with db_connection(project=project) as conn:
        try:
            result = resolve_conflict(conn, conflict_id, resolution)
            if result is None:
                return f"Error: Conflict {conflict_id} not found"
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"


@mcp.tool()
def memory_search_tool(
    query: str,
    types: list[str] | None = None,
    tags: list[str] | None = None,
    include_stale: bool = False,
    limit: int = 20,
    project: str | None = None,
) -> str:
    """Hybrid tag + full-text search.

    Args:
        query: Search query (full-text via Turso FTS5)
        types: Filter by memory types
        tags: Filter by tags
        include_stale: Whether to include potentially stale items
        limit: Maximum results (default 20)
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
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"


@mcp.tool()
def engineering_context_tool(
    tags: list[str],
    task: str | None = None,
    token_budget: int | None = None,
    types: list[str] | None = None,
    include_stale: bool = False,
    current_task: str | None = None,
    paths: list[str] | None = None,
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
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"


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
            return f"Error: {e}"


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
            return f"Error: {e}"


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
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"


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
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error: {e}"


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
        return f"Error: {e}"


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
    mcp.run()


if __name__ == "__main__":
    main()
