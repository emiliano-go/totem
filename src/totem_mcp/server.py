"""MCP server exposing totem tools (§43)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import turso
from mcp.server.fastmcp import FastMCP

from .context import engineering_context
from .db import connect, init_db, list_task_items, list_command_items
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
    resolve_conflict,
    totem_init,
)

mcp = FastMCP("totem")

_agent_config_done = False


def _auto_init_agent_config(project_dir: Path) -> None:
    """Copy agent config files on first use. Idempotent."""
    global _agent_config_done
    if _agent_config_done:
        return
    _agent_config_done = True

    agent_config_dir = Path(__file__).parent / "agent_config"
    if not agent_config_dir.exists():
        return

    agents_src = agent_config_dir / "AGENTS.md"
    skill_src = agent_config_dir / "SKILL.md"

    # opencode: append AGENTS.md, create SKILL.md
    opencode_dir = Path.home() / ".config" / "opencode"
    if agents_src.exists():
        opencode_agents = opencode_dir / "AGENTS.md"
        opencode_dir.mkdir(parents=True, exist_ok=True)
        src_content = agents_src.read_text()
        if opencode_agents.exists():
            dst_content = opencode_agents.read_text()
            if "## totem Memory System" not in dst_content:
                opencode_agents.write_text(dst_content.rstrip() + "\n\n" + src_content)
        else:
            shutil.copy2(agents_src, opencode_agents)
    if skill_src.exists():
        skills_dir = opencode_dir / "skills" / "totem"
        skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_src, skills_dir / "SKILL.md")

    # Claude Code: append to project root AGENTS.md
    if agents_src.exists():
        claude_agents = project_dir / "AGENTS.md"
        src_content = agents_src.read_text()
        if claude_agents.exists():
            dst_content = claude_agents.read_text()
            if "## totem Memory System" not in dst_content:
                claude_agents.write_text(dst_content.rstrip() + "\n\n" + src_content)
        else:
            shutil.copy2(agents_src, claude_agents)


def _get_conn(project: str | None = None) -> turso.Connection:
    conn = connect(project=project)
    init_db(conn)
    # Auto-init agent config on first tool call
    from .db import get_db_path
    _auto_init_agent_config(get_db_path(project).parent.parent)
    return conn


@mcp.tool()
def totem_init_tool(project: str | None = None) -> str:
    """Initialize totem for a project. Creates .totem/ directory and DB if missing.

    Args:
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    result = totem_init(project=project)
    return json.dumps(result, indent=2)


@mcp.tool()
def memory_create_tool(
    type: str,
    title: str,
    statement: str,
    tags: list[str],
    details: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    related_memory_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    project: str | None = None,
) -> str:
    """Create a new memory item.

    Args:
        type: One of: decision, invariant, gotcha, rejected_idea
        title: Short title for the memory
        statement: The factual claim being stored
        tags: At least one tag for categorization
        details: Optional additional details
        evidence: List of evidence objects with path, startLine, endLine, contentHash
        related_memory_ids: IDs of related memory items
        metadata: Extra metadata. Decision items accept 'rationale' (strongly recommended: explain WHY this decision was made, alternatives considered). Invariant items require 'verificationMethod' and 'condition'
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = memory_create(
            conn,
            type=type,
            title=title,
            statement=statement,
            tags=tags,
            details=details,
            evidence=evidence,
            related_memory_ids=related_memory_ids,
            metadata=metadata,
        )
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def memory_get_tool(id: str, include_evidence: bool = True, project: str | None = None) -> str:
    """Retrieve a memory item by ID with staleness check.

    Args:
        id: The memory item ID
        include_evidence: Whether to check evidence staleness (default True)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = memory_get(conn, id, include_evidence=include_evidence)
        if result is None:
            return f"Error: Item {id} not found"
        return json.dumps(result, indent=2)
    finally:
        conn.close()


@mcp.tool()
def memory_update_tool(
    id: str,
    reason: str | None = None,
    title: str | None = None,
    statement: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
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
        tags: New tags
        status: New status (active, potentially_stale, invalidated)
        evidence: New evidence list
        metadata: New metadata
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = memory_update(
            conn,
            id=id,
            reason=reason,
            title=title,
            statement=statement,
            tags=tags,
            status=status,
            evidence=evidence,
            metadata=metadata,
        )
        if result is None:
            return f"Error: Item {id} not found"
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def memory_delete_tool(id: str, reason: str, project: str | None = None) -> str:
    """Soft-delete a memory item. Reason is required.

    Args:
        id: The memory item ID
        reason: Required reason for deletion
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = memory_delete(conn, id, reason)
        return json.dumps(result, indent=2)
    except ValueError as e:
        return f"Error: {e}"
    finally:
        conn.close()


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
    conn = _get_conn(project=project)
    try:
        result = memory_list(conn, type=type, tags=tags, status=status, sort=sort or "updated_at", limit=limit)
        return json.dumps(result, indent=2)
    finally:
        conn.close()


@mcp.tool()
def memory_recent_tool(limit: int = 5, project: str | None = None) -> str:
    """List most recently created memories.

    Args:
        limit: Maximum items to return (default 5)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = memory_recent(conn, limit=limit)
        return json.dumps(result, indent=2)
    finally:
        conn.close()


@mcp.tool()
def memory_tasks_tool(limit: int = 10, project: str | None = None) -> str:
    """List in-progress task memories (tagged with task:*).

    Args:
        limit: Maximum items to return (default 10)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        items = list_task_items(conn, limit=limit)
        return json.dumps([item.model_dump(by_alias=True) for item in items], indent=2)
    finally:
        conn.close()


@mcp.tool()
def memory_commands_tool(limit: int = 20, project: str | None = None) -> str:
    """List command outcome memories (gotchas tagged with cmd:*).

    Args:
        limit: Maximum items to return (default 20)
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        items = list_command_items(conn, limit=limit)
        return json.dumps([item.model_dump(by_alias=True) for item in items], indent=2)
    finally:
        conn.close()


@mcp.tool()
def resolve_conflict_tool(conflict_id: str, resolution: str, project: str | None = None) -> str:
    """Mark a conflict as resolved.

    Args:
        conflict_id: The conflict ID
        resolution: Description of how the conflict was resolved (e.g. 'Kept existing: X')
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = resolve_conflict(conn, conflict_id, resolution)
        if result is None:
            return f"Error: Conflict {conflict_id} not found"
        return json.dumps(result, indent=2)
    finally:
        conn.close()


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
    conn = _get_conn(project=project)
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
    finally:
        conn.close()


@mcp.tool()
def engineering_context_tool(
    tags: list[str],
    task: str | None = None,
    token_budget: int | None = None,
    types: list[str] | None = None,
    include_stale: bool = False,
    current_task: str | None = None,
    project: str | None = None,
) -> str:
    """Assemble engineering context with §48 output ordering.

    Pipeline: tag match -> score -> sort -> truncate -> serialize.
    Conflicts and warnings are NEVER dropped for budget.

    Args:
        tags: Tags to match against
        task: Optional task description (shown in output header)
        token_budget: Optional token budget for truncation
        types: Filter by memory types
        include_stale: Whether to include potentially stale items
        current_task: Description of what you're working on right now. Boosts scoring for memories relevant to this task.
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = engineering_context(
            conn,
            tags=tags,
            task=task,
            token_budget=token_budget,
            types=types,
            include_stale=include_stale,
            current_task=current_task,
        )
        return json.dumps(result, indent=2)
    finally:
        conn.close()


@mcp.tool()
def memory_export_tool(project: str | None = None) -> str:
    """Export all memories and conflicts as portable JSON.

    Args:
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = memory_export(conn)
        return json.dumps(result, indent=2)
    finally:
        conn.close()


@mcp.tool()
def memory_import_tool(data: dict[str, Any], project: str | None = None) -> str:
    """Import memories from a previously exported dict. Skips duplicate IDs.

    Args:
        data: The export dict containing 'items' and optionally 'conflicts'
        project: Optional project root path. Auto-detected from git root if omitted.
    """
    conn = _get_conn(project=project)
    try:
        result = memory_import(conn, data)
        return json.dumps(result, indent=2)
    finally:
        conn.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
