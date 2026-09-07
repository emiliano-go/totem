"""Click CLI for totem."""

from __future__ import annotations

import json
import sqlite3

import click

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


def _get_conn() -> sqlite3.Connection:
    conn = connect()
    init_db(conn)
    return conn


@click.group()
@click.version_option(package_name="totem-mcp")
def cli() -> None:
    """totem: persistent memory for engineering agents."""


@cli.command()
@click.option("--type", "mem_type", required=True, type=click.Choice(["decision", "invariant", "gotcha", "rejected_idea"]))
@click.option("--title", required=True)
@click.option("--statement", required=True)
@click.option("--tags", required=True, help="Comma-separated tags")
@click.option("--details", default=None)
@click.option("--confidence", default=1.0, type=float)
@click.option("--importance", default=0.5, type=float)
@click.option("--evidence", default=None, help="JSON array of evidence objects")
@click.option("--metadata", default=None, help="JSON object of extra metadata")
@click.option("--related", default=None, help="Comma-separated related memory IDs")
def create(
    mem_type: str,
    title: str,
    statement: str,
    tags: str,
    details: str | None,
    confidence: float,
    importance: float,
    evidence: str | None,
    metadata: str | None,
    related: str | None,
) -> None:
    """Create a new memory item."""
    conn = _get_conn()
    try:
        result = memory_create(
            conn,
            type=mem_type,
            title=title,
            statement=statement,
            tags=[t.strip() for t in tags.split(",")],
            details=details,
            confidence=confidence,
            importance=importance,
            evidence=json.loads(evidence) if evidence else None,
            metadata=json.loads(metadata) if metadata else None,
            related_memory_ids=[r.strip() for r in related.split(",")] if related else None,
        )
        click.echo(json.dumps(result, indent=2))
    except ValueError as e:
        raise click.ClickException(str(e))
    finally:
        conn.close()


@cli.command()
@click.argument("item_id")
@click.option("--no-evidence", is_flag=True, help="Skip evidence staleness check")
def get(item_id: str, no_evidence: bool) -> None:
    """Retrieve a memory item by ID."""
    conn = _get_conn()
    try:
        result = memory_get(conn, item_id, include_evidence=not no_evidence)
        if result is None:
            raise click.ClickException(f"Item {item_id} not found")
        click.echo(json.dumps(result, indent=2))
    finally:
        conn.close()


@cli.command()
@click.argument("item_id")
@click.option("--reason", default="maintenance", help="Reason for update (strongly recommended for audit trail)")
@click.option("--title", default=None)
@click.option("--statement", default=None)
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--status", default=None, type=click.Choice(["active", "potentially_stale", "invalidated"]))
@click.option("--confidence", default=None, type=float)
@click.option("--importance", default=None, type=float)
@click.option("--evidence", default=None, help="JSON array of evidence objects")
@click.option("--metadata", default=None, help="JSON object of extra metadata")
def update(
    item_id: str,
    reason: str,
    title: str | None,
    statement: str | None,
    tags: str | None,
    status: str | None,
    confidence: float | None,
    importance: float | None,
    evidence: str | None,
    metadata: str | None,
) -> None:
    """Update a memory item. Reason is required."""
    conn = _get_conn()
    try:
        result = memory_update(
            conn,
            id=item_id,
            reason=reason,
            title=title,
            statement=statement,
            tags=[t.strip() for t in tags.split(",")] if tags else None,
            status=status,
            confidence=confidence,
            importance=importance,
            evidence=json.loads(evidence) if evidence else None,
            metadata=json.loads(metadata) if metadata else None,
        )
        if result is None:
            raise click.ClickException(f"Item {item_id} not found")
        click.echo(json.dumps(result, indent=2))
    except ValueError as e:
        raise click.ClickException(str(e))
    finally:
        conn.close()


@cli.command()
@click.argument("item_id")
@click.option("--reason", required=True, help="Required reason for deletion (§3)")
def delete(item_id: str, reason: str) -> None:
    """Soft-delete a memory item."""
    conn = _get_conn()
    try:
        result = memory_delete(conn, item_id, reason)
        click.echo(json.dumps(result, indent=2))
    finally:
        conn.close()


@cli.command("list")
@click.option("--type", "mem_type", default=None, type=click.Choice(["decision", "invariant", "gotcha", "rejected_idea"]))
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--status", default=None, type=click.Choice(["active", "potentially_stale", "invalidated"]))
@click.option("--sort", default="updated_at", type=click.Choice(["created_at", "updated_at", "importance"]), help="Sort field")
@click.option("--limit", default=50, type=int)
def list_cmd(mem_type: str | None, tags: str | None, status: str | None, sort: str, limit: int) -> None:
    """List memory items with optional filters."""
    conn = _get_conn()
    try:
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        result = memory_list(conn, type=mem_type, tags=tag_list, status=status, sort=sort, limit=limit)
        click.echo(json.dumps(result, indent=2))
    finally:
        conn.close()


@cli.command()
@click.option("--query", required=True)
@click.option("--types", default=None, help="Comma-separated memory types")
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--include-stale", is_flag=True, default=False)
@click.option("--limit", default=20, type=int)
def search(
    query: str,
    types: str | None,
    tags: str | None,
    include_stale: bool,
    limit: int,
) -> None:
    """Hybrid tag + full-text search."""
    conn = _get_conn()
    try:
        type_list = [t.strip() for t in types.split(",")] if types else None
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        result = memory_search(
            conn,
            query=query,
            types=type_list,
            tags=tag_list,
            include_stale=include_stale,
            limit=limit,
        )
        click.echo(json.dumps(result, indent=2))
    finally:
        conn.close()


@cli.command()
@click.option("--tags", required=True, help="Comma-separated tags")
@click.option("--task", default=None, help="Task description")
@click.option("--budget", default=None, type=int, help="Token budget")
@click.option("--types", default=None, help="Comma-separated memory types")
@click.option("--include-stale", is_flag=True, default=False)
def context(
    tags: str,
    task: str | None,
    budget: int | None,
    types: str | None,
    include_stale: bool,
) -> None:
    """Assemble engineering context (§48 output ordering)."""
    conn = _get_conn()
    try:
        tag_list = [t.strip() for t in tags.split(",")]
        type_list = [t.strip() for t in types.split(",")] if types else None
        result = engineering_context(
            conn,
            tags=tag_list,
            task=task,
            token_budget=budget,
            types=type_list,
            include_stale=include_stale,
        )
        click.echo(json.dumps(result, indent=2))
    finally:
        conn.close()


def main() -> None:
    cli()


def mcp_main() -> None:
    """Entry point for MCP server."""
    from .server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
