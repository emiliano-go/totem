"""MCP server exposing totem tools (§43)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from mcp.server.fastmcp import FastMCP

from .context import engineering_context
from .db import connect, init_db
from .tools import (
    memory_create,
    memory_delete,
    memory_get,
    memory_list,
    memory_search,
    memory_update,
)

mcp = FastMCP("totem")


def _get_conn() -> sqlite3.Connection:
    conn = connect()
    init_db(conn)
    return conn


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
) -> str:
    """Create a new memory item.

    Args:
        type: One of: decision, invariant, gotcha, rejected_idea
        title: Short title for the memory
        statement: The factual claim being stored
        tags: At least one tag for categorization
        details: Optional additional details
        confidence: Confidence in this claim (0-1, default 1.0)
        importance: Importance of this claim (0-1, default 0.5)
        evidence: List of evidence objects with path, startLine, endLine, contentHash
        related_memory_ids: IDs of related memory items
        metadata: Extra metadata (invariant requires 'verificationMethod')
    """
    conn = _get_conn()
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
    except ValueError as e:
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def memory_get_tool(id: str, include_evidence: bool = True) -> str:
    """Retrieve a memory item by ID with staleness check.

    Args:
        id: The memory item ID
        include_evidence: Whether to check evidence staleness (default True)
    """
    conn = _get_conn()
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
    reason: str,
    title: str | None = None,
    statement: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    confidence: float | None = None,
    importance: float | None = None,
    evidence: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Update a memory item. Reason is required.

    Args:
        id: The memory item ID
        reason: Required reason for this update
        title: New title
        statement: New statement
        tags: New tags
        status: New status (active, potentially_stale, invalidated)
        confidence: New confidence (0-1)
        importance: New importance (0-1)
        evidence: New evidence list
        metadata: New metadata
    """
    conn = _get_conn()
    try:
        result = memory_update(
            conn,
            id=id,
            reason=reason,
            title=title,
            statement=statement,
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
    except ValueError as e:
        return f"Error: {e}"
    finally:
        conn.close()


@mcp.tool()
def memory_delete_tool(id: str, reason: str) -> str:
    """Soft-delete a memory item. Reason is required.

    Args:
        id: The memory item ID
        reason: Required reason for deletion
    """
    conn = _get_conn()
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
    limit: int = 50,
) -> str:
    """List memory items with optional filters.

    Args:
        type: Filter by type (decision, invariant, gotcha, rejected_idea)
        tags: Filter by tags (items must have at least one)
        status: Filter by status (active, potentially_stale, invalidated)
        limit: Maximum items to return (default 50)
    """
    conn = _get_conn()
    try:
        result = memory_list(conn, type=type, tags=tags, status=status, limit=limit)
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
) -> str:
    """Hybrid tag + full-text search.

    Args:
        query: Search query (full-text via SQLite FTS5)
        types: Filter by memory types
        tags: Filter by tags
        include_stale: Whether to include potentially stale items
        limit: Maximum results (default 20)
    """
    conn = _get_conn()
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
) -> str:
    """Assemble engineering context with §48 output ordering.

    Pipeline: tag match → score → sort → truncate → serialize.
    Conflicts and warnings are NEVER dropped for budget.

    Args:
        tags: Tags to match against
        task: Optional task description
        token_budget: Optional token budget for truncation
        types: Filter by memory types
        include_stale: Whether to include potentially stale items
    """
    conn = _get_conn()
    try:
        result = engineering_context(
            conn,
            tags=tags,
            task=task,
            token_budget=token_budget,
            types=types,
            include_stale=include_stale,
        )
        return json.dumps(result, indent=2)
    finally:
        conn.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
