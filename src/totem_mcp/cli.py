"""Click CLI for totem."""

from __future__ import annotations

import json

import click
import turso

from .context import engineering_context
from .db import connect, init_db
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
)


def _get_conn(project: str | None = None) -> turso.Connection:
    conn = connect(project=project)
    init_db(conn)
    return conn


@click.group()
@click.version_option(package_name="totem-mcp")
@click.option("--project", default=None, help="Project root path (auto-detected from git if omitted)")
@click.pass_context
def cli(ctx: click.Context, project: str | None) -> None:
    """totem: persistent memory for engineering agents."""
    ctx.ensure_object(dict)
    ctx.obj["project"] = project


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
@click.pass_context
def create(
    ctx: click.Context,
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
    conn = _get_conn(project=ctx.obj.get("project"))
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
@click.pass_context
def get(ctx: click.Context, item_id: str, no_evidence: bool) -> None:
    """Retrieve a memory item by ID."""
    conn = _get_conn(project=ctx.obj.get("project"))
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
@click.pass_context
def update(
    ctx: click.Context,
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
    conn = _get_conn(project=ctx.obj.get("project"))
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
@click.pass_context
def delete(ctx: click.Context, item_id: str, reason: str) -> None:
    """Soft-delete a memory item."""
    conn = _get_conn(project=ctx.obj.get("project"))
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
@click.pass_context
def list_cmd(ctx: click.Context, mem_type: str | None, tags: str | None, status: str | None, sort: str, limit: int) -> None:
    """List memory items with optional filters."""
    conn = _get_conn(project=ctx.obj.get("project"))
    try:
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        result = memory_list(conn, type=mem_type, tags=tag_list, status=status, sort=sort, limit=limit)
        click.echo(json.dumps(result, indent=2))
    finally:
        conn.close()


@cli.command()
@click.option("--limit", default=5, type=int, help="Number of recent items (default 5)")
@click.pass_context
def recent(ctx: click.Context, limit: int) -> None:
    """List most recently created memories."""
    conn = _get_conn(project=ctx.obj.get("project"))
    try:
        result = memory_recent(conn, limit=limit)
        click.echo(json.dumps(result, indent=2))
    finally:
        conn.close()


@cli.command()
@click.argument("conflict_id")
@click.option("--resolution", required=True, help="How the conflict was resolved")
@click.pass_context
def resolve(ctx: click.Context, conflict_id: str, resolution: str) -> None:
    """Mark a conflict as resolved."""
    conn = _get_conn(project=ctx.obj.get("project"))
    try:
        result = resolve_conflict(conn, conflict_id, resolution)
        if result is None:
            raise click.ClickException(f"Conflict {conflict_id} not found")
        click.echo(json.dumps(result, indent=2))
    finally:
        conn.close()


@cli.command()
@click.option("--query", required=True)
@click.option("--types", default=None, help="Comma-separated memory types")
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--include-stale", is_flag=True, default=False)
@click.option("--limit", default=20, type=int)
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    types: str | None,
    tags: str | None,
    include_stale: bool,
    limit: int,
) -> None:
    """Hybrid tag + full-text search."""
    conn = _get_conn(project=ctx.obj.get("project"))
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
@click.pass_context
def context(
    ctx: click.Context,
    tags: str,
    task: str | None,
    budget: int | None,
    types: str | None,
    include_stale: bool,
) -> None:
    """Assemble engineering context (§48 output ordering)."""
    conn = _get_conn(project=ctx.obj.get("project"))
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


@cli.command()
@click.option("--output", "-o", default=None, help="Output file path (default: stdout)")
@click.pass_context
def export(ctx: click.Context, output: str | None) -> None:
    """Export all memories and conflicts as JSON."""
    conn = _get_conn(project=ctx.obj.get("project"))
    try:
        result = memory_export(conn)
        data = json.dumps(result, indent=2)
        if output:
            from pathlib import Path
            Path(output).write_text(data)
            click.echo(f"Exported to {output}")
        else:
            click.echo(data)
    finally:
        conn.close()


@cli.command()
@click.argument("file", type=click.Path(exists=True))
@click.pass_context
def import_cmd(ctx: click.Context, file: str) -> None:
    """Import memories from a JSON export file."""
    from pathlib import Path
    data = json.loads(Path(file).read_text())
    conn = _get_conn(project=ctx.obj.get("project"))
    try:
        result = memory_import(conn, data)
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
