"""Click CLI for totem."""

from __future__ import annotations

import json

import click

from .context import engineering_context
from .db import db_connection
from .tools import (
    memory_audit,
    memory_check_record,
    memory_consolidate,
    memory_create,
    memory_delete,
    memory_export,
    memory_file_context,
    memory_gc,
    memory_get,
    memory_history,
    memory_import,
    memory_list,
    memory_recent,
    memory_revalidate,
    memory_search,
    memory_feedback,
    memory_train_models,
    memory_update,
    memory_verify,
    resolve_conflict,
    totem_init,
)


@click.group()
@click.version_option(package_name="totem-mcp")
@click.option("--project", default=None, help="Project root path (auto-detected from git if omitted)")
@click.pass_context
def cli(ctx: click.Context, project: str | None) -> None:
    """totem: persistent memory for engineering agents."""
    ctx.ensure_object(dict)
    ctx.obj["project"] = project


@cli.command()
@click.option("--type", "mem_type", required=True, type=click.Choice([
    "decision", "invariant", "gotcha", "rejected_idea",
    "assumption", "open_question", "ambiguity", "contract",
    "constraint", "hypothesis", "observation", "bug",
    "architecture", "implementation",
]))
@click.option("--title", required=True)
@click.option("--statement", required=True)
@click.option("--tags", required=True, help="Comma-separated tags")
@click.option("--details", default=None)
@click.option("--confidence", default=None, type=float, help="Defaults from --asserted-by provenance")
@click.option("--importance", default=0.5, type=float)
@click.option("--evidence", default=None, help="JSON array of evidence objects")
@click.option("--metadata", default=None, help="JSON object of extra metadata")
@click.option("--related", default=None, help="Comma-separated related memory IDs")
@click.option("--asserted-by", "asserted_by", default=None, type=click.Choice([
    "user", "test", "source", "git", "doc", "runtime", "agent",
]), help="Who asserted the claim (sets default confidence)")
@click.option("--valid-from", "valid_from", default=None, help="ISO timestamp when the claim starts being true")
@click.option("--valid-to", "valid_to", default=None, help="ISO timestamp when the claim stops being true")
@click.pass_context
def create(
    ctx: click.Context,
    mem_type: str,
    title: str,
    statement: str,
    tags: str,
    details: str | None,
    confidence: float | None,
    importance: float,
    evidence: str | None,
    metadata: str | None,
    related: str | None,
    asserted_by: str | None,
    valid_from: str | None,
    valid_to: str | None,
) -> None:
    """Create a new memory item."""
    with db_connection(project=ctx.obj.get("project")) as conn:
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
                asserted_by=asserted_by,
                valid_from=valid_from,
                valid_to=valid_to,
            )
            click.echo(json.dumps(result, indent=2))
        except ValueError as e:
            raise click.ClickException(str(e))


@cli.command()
@click.argument("item_id")
@click.option("--no-evidence", is_flag=True, help="Skip evidence staleness check")
@click.pass_context
def get(ctx: click.Context, item_id: str, no_evidence: bool) -> None:
    """Retrieve a memory item by ID."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_get(conn, item_id, include_evidence=not no_evidence)
        if result is None:
            raise click.ClickException(f"Item {item_id} not found")
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("item_id")
@click.option("--reason", default="maintenance", help="Reason for update (strongly recommended for audit trail)")
@click.option("--title", default=None)
@click.option("--statement", default=None)
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--status", default=None, type=click.Choice(["active", "potentially_stale", "invalidated", "resolved", "superseded"]))
@click.option("--confidence", default=None, type=float)
@click.option("--importance", default=None, type=float)
@click.option("--evidence", default=None, help="JSON array of evidence objects")
@click.option("--metadata", default=None, help="JSON object of extra metadata")
@click.option("--valid-from", "valid_from", default=None, help="ISO validity start (empty clears)")
@click.option("--valid-to", "valid_to", default=None, help="ISO validity end (empty clears)")
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
    valid_from: str | None,
    valid_to: str | None,
) -> None:
    """Update a memory item. Reason is required."""
    with db_connection(project=ctx.obj.get("project")) as conn:
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
                valid_from=valid_from,
                valid_to=valid_to,
            )
            if result is None:
                raise click.ClickException(f"Item {item_id} not found")
            click.echo(json.dumps(result, indent=2))
        except ValueError as e:
            raise click.ClickException(str(e))


@cli.command()
@click.argument("item_id")
@click.option("--reason", required=True, help="Required reason for deletion (§3)")
@click.pass_context
def delete(ctx: click.Context, item_id: str, reason: str) -> None:
    """Soft-delete a memory item."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_delete(conn, item_id, reason)
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("item_id")
@click.option("--method", default=None, help="How it was verified")
@click.option("--commit", default=None, help="Git commit verified at (default HEAD)")
@click.option("--confidence", default=1.0, type=float, help="Confidence to set (default 1.0)")
@click.option("--verified-by", "verified_by", default=None, help="Memory ID holding the verification artifact")
@click.option("--check-id", "check_id", default=None, help="Recorded check ID justifying the verification")
@click.pass_context
def verify(
    ctx: click.Context,
    item_id: str,
    method: str | None,
    commit: str | None,
    confidence: float,
    verified_by: str | None,
    check_id: str | None,
) -> None:
    """Record that a memory was verified."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_verify(
            conn, item_id, method=method, commit=commit,
            confidence=confidence, verified_by_id=verified_by, check_id=check_id,
        )
        if result is None:
            raise click.ClickException(f"Item {item_id} not found")
        click.echo(json.dumps(result, indent=2))


@cli.command("check")
@click.argument("item_id")
@click.option("--name", required=True, help="Short check name")
@click.option("--command", default=None, help="Command that was run")
@click.option("--cwd", default=None, help="Working directory of the run")
@click.option("--exit-code", default=0, type=int, help="Observed exit code (default 0)")
@click.option("--output", default=None, help="Captured output (hashed, not stored)")
@click.pass_context
def check_cmd(
    ctx: click.Context,
    item_id: str,
    name: str,
    command: str | None,
    cwd: str | None,
    exit_code: int,
    output: str | None,
) -> None:
    """Record an observed check for a memory item."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_check_record(
            conn, item_id, name, command=command, cwd=cwd,
            exit_code=exit_code, output=output,
        )
        click.echo(json.dumps(result, indent=2))


@cli.command("audit")
@click.pass_context
def audit(ctx: click.Context) -> None:
    """Verify the memory history hash chain."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_audit(conn)
        click.echo(json.dumps(result, indent=2))
        if not result["ok"]:
            raise click.exceptions.Exit(1)


@cli.command("feedback")
@click.argument("item_id")
@click.option("--signal", required=True, type=click.Choice(
    ["useful", "wrong", "irrelevant", "verified", "stale"]
))
@click.option("--query", default=None, help="Query or task the item was considered for")
@click.option("--tags", default=None, help="Comma-separated tags supplied with the query")
@click.option("--weight", default=1.0, type=float, help="Relative weight (0-10)")
@click.pass_context
def feedback_cmd(
    ctx: click.Context,
    item_id: str,
    signal: str,
    query: str | None,
    tags: str | None,
    weight: float,
) -> None:
    """Record retrieval feedback for a memory."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_feedback(
            conn, item_id, signal, query=query,
            tags=[t.strip() for t in tags.split(",")] if tags else None,
            weight=weight,
        )
        click.echo(json.dumps(result, indent=2))


@cli.command("consolidate")
@click.option("--apply", "apply_", is_flag=True, help="Apply the consolidation (default dry-run)")
@click.option("--threshold", default=0.92, type=float, help="Cosine similarity threshold")
@click.pass_context
def consolidate_cmd(
    ctx: click.Context, apply_: bool, threshold: float
) -> None:
    """Cluster near-duplicate memories and optionally supersede duplicates."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        click.echo(json.dumps(
            memory_consolidate(conn, apply=apply_, threshold=threshold),
            indent=2,
        ))


@cli.command("eval")
@click.option("--cases", "cases_file", default=None, type=click.Path(exists=True), help="Golden case JSON")
@click.option("--out", default=None, help="Write the report to this path")
@click.option("--baseline", default=None, type=click.Path(exists=True), help="Prior report to gate against")
@click.option("--k", default=10, type=int)
@click.option("--tolerance", default=0.02, type=float)
@click.option("--p95", "p95_ms", default=2000.0, type=float)
@click.pass_context
def eval_cmd(
    ctx: click.Context,
    cases_file: str | None,
    out: str | None,
    baseline: str | None,
    k: int,
    tolerance: float,
    p95_ms: float,
) -> None:
    """Evaluate context ranking (recall@k / MRR) and optionally gate a change."""
    from pathlib import Path

    from .context import engineering_context
    from .db import get_all_items
    from .eval_bench import auto_cases, evaluate, gate, load_cases

    with db_connection(project=ctx.obj.get("project")) as conn:
        cases = load_cases(cases_file) if cases_file else auto_cases(get_all_items(conn))
        report = evaluate(
            cases,
            lambda case: engineering_context(
                conn, tags=case.tags, task=case.task or None,
                paths=case.paths or None,
                as_of=case.as_of, include_expired=case.include_expired,
            ),
            k=k,
        )
        if baseline:
            report["gate"] = gate(
                json.loads(Path(baseline).read_text()), report, tolerance, p95_ms
            )
        text = json.dumps(report, indent=2)
        if out:
            Path(out).write_text(text)
            click.echo(f"Wrote {out}")
        else:
            click.echo(text)
        if report.get("gate") and not report["gate"]["ok"]:
            raise click.exceptions.Exit(1)


@cli.group("models")
def models_group() -> None:
    """Learned/advisory model maintenance."""


@models_group.command("validate")
@click.option("--min-test", default=20, type=int)
@click.option("--tolerance", default=0.1, type=float)
@click.pass_context
def models_validate(ctx: click.Context, min_test: int, tolerance: float) -> None:
    """Gate the calibrated trust head on held-out history."""
    from .calibration import validate_and_promote

    with db_connection(project=ctx.obj.get("project")) as conn:
        click.echo(json.dumps(
            validate_and_promote(conn, min_test=min_test, tolerance=tolerance),
            indent=2,
        ))


@models_group.command("train")
@click.option("--min-examples", default=24, type=int)
@click.pass_context
def models_train(ctx: click.Context, min_examples: int) -> None:
    """Fit the reranker and calibrator from feedback."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        click.echo(json.dumps(
            memory_train_models(conn, min_examples=min_examples), indent=2
        ))


@models_group.command("status")
def models_status() -> None:
    """Report the configured retrieval encoder."""
    from .embeddings import status

    click.echo(json.dumps(status(), indent=2))


@models_group.command("pull")
def models_pull() -> None:
    """Download and load the configured static encoder, then report status."""
    from .embeddings import get_static_encoder, status

    encoder = get_static_encoder()
    click.echo(json.dumps(status(), indent=2))
    if encoder is None:
        raise click.ClickException(
            "static encoder unavailable; install totem-mcp[embeddings] and "
            "check TOTEM_EMBEDDING_BACKEND/TOTEM_EMBEDDING_MODEL"
        )


@cli.command()
@click.option("--apply", "apply_", is_flag=True, help="Void stale verifications (default: dry-run)")
@click.pass_context
def revalidate(ctx: click.Context, apply_: bool) -> None:
    """Void verifications whose evidence changed."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_revalidate(conn, dry_run=not apply_)
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option("--retention-days", default=90, type=int, help="Age threshold in days (default 90)")
@click.option("--apply", "apply_", is_flag=True, help="Delete candidates (default: dry-run)")
@click.pass_context
def gc(ctx: click.Context, retention_days: int, apply_: bool) -> None:
    """Purge terminal-state memories past the retention window."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_gc(conn, retention_days=retention_days, dry_run=not apply_)
        click.echo(json.dumps(result, indent=2))


@cli.command("list")
@click.option("--type", "mem_type", default=None, type=click.Choice([
    "decision", "invariant", "gotcha", "rejected_idea",
    "assumption", "open_question", "ambiguity", "contract",
    "constraint", "hypothesis", "observation", "bug",
    "architecture", "implementation",
]))
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--status", default=None, type=click.Choice(["active", "potentially_stale", "invalidated", "resolved", "superseded"]))
@click.option("--sort", default="updated_at", type=click.Choice(["created_at", "updated_at", "importance"]), help="Sort field")
@click.option("--limit", default=50, type=int)
@click.pass_context
def list_cmd(ctx: click.Context, mem_type: str | None, tags: str | None, status: str | None, sort: str, limit: int) -> None:
    """List memory items with optional filters."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        result = memory_list(conn, type=mem_type, tags=tag_list, status=status, sort=sort, limit=limit)
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option("--limit", default=5, type=int, help="Number of recent items (default 5)")
@click.pass_context
def recent(ctx: click.Context, limit: int) -> None:
    """List most recently created memories."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_recent(conn, limit=limit)
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("conflict_id")
@click.option("--resolution", required=True, help="How the conflict was resolved")
@click.pass_context
def resolve(ctx: click.Context, conflict_id: str, resolution: str) -> None:
    """Mark a conflict as resolved."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = resolve_conflict(conn, conflict_id, resolution)
        if result is None:
            raise click.ClickException(f"Conflict {conflict_id} not found")
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option("--project", default=None, help="Project root path (auto-detected from git if omitted)")
def init(project: str | None) -> None:
    """Initialize totem for this project. Creates .totem/ directory and DB."""
    from pathlib import Path
    from .db import get_db_path, init_project

    db_path = get_db_path(project)
    project_dir = db_path.parent.parent
    result = init_project(project_dir)
    if result["already_existed"]:
        click.echo(f"Totem already initialized at {result['path']}")
    else:
        click.echo(f"Initialized totem at {result['path']}")


@cli.command()
@click.option("--limit", default=10, type=int, help="Number of task items (default 10)")
@click.pass_context
def tasks(ctx: click.Context, limit: int) -> None:
    """List in-progress task memories (tagged with task:*)."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        from .db import list_task_items
        items = list_task_items(conn, limit=limit)
        if not items:
            click.echo("No task memories found. Store one with tag 'task:<name>'.")
            return
        click.echo(json.dumps([item.model_dump(by_alias=True) for item in items], indent=2))


@cli.command()
@click.option("--limit", default=20, type=int, help="Number of command items (default 20)")
@click.pass_context
def commands(ctx: click.Context, limit: int) -> None:
    """List command outcome memories (gotchas tagged with cmd:*)."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        from .db import list_command_items
        items = list_command_items(conn, limit=limit)
        if not items:
            click.echo("No command memories found. Store one with tag 'cmd:<command>' and type gotcha.")
            return
        click.echo(json.dumps([item.model_dump(by_alias=True) for item in items], indent=2))


@cli.command()
@click.option("--query", required=True)
@click.option("--types", default=None, help="Comma-separated memory types")
@click.option("--tags", default=None, help="Comma-separated tags")
@click.option("--include-stale", is_flag=True, default=False)
@click.option("--limit", default=20, type=int)
@click.option("--as-of", "as_of", default=None, help="Evaluate temporal validity at this ISO timestamp")
@click.option("--include-expired", is_flag=True, default=False)
@click.option("--include-file-state", is_flag=True, default=False, help="Include file-state TLDRs")
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    types: str | None,
    tags: str | None,
    include_stale: bool,
    limit: int,
    as_of: str | None,
    include_expired: bool,
    include_file_state: bool,
) -> None:
    """Hybrid tag + full-text search."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        type_list = [t.strip() for t in types.split(",")] if types else None
        tag_list = [t.strip() for t in tags.split(",")] if tags else None
        result = memory_search(
            conn,
            query=query,
            types=type_list,
            tags=tag_list,
            include_stale=include_stale,
            limit=limit,
            as_of=as_of,
            include_expired=include_expired,
            include_file_state=include_file_state,
        )
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option("--tags", required=True, help="Comma-separated tags")
@click.option("--task", default=None, help="Task description (shown in output header)")
@click.option("--budget", default=None, type=int, help="Token budget")
@click.option("--types", default=None, help="Comma-separated memory types")
@click.option("--include-stale", is_flag=True, default=False)
@click.option("--current-task", default=None, help="What you're working on right now (boosts relevant memories)")
@click.option("--max-item-chars", default=None, type=int)
@click.option("--as-of", "as_of", default=None, help="Evaluate temporal validity at this ISO timestamp")
@click.option("--include-expired", is_flag=True, default=False)
@click.pass_context
def context(
    ctx: click.Context,
    tags: str,
    task: str | None,
    budget: int | None,
    types: str | None,
    include_stale: bool,
    current_task: str | None,
    max_item_chars: int | None,
    as_of: str | None,
    include_expired: bool,
) -> None:
    """Assemble engineering context (§48 output ordering)."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        tag_list = [t.strip() for t in tags.split(",")]
        type_list = [t.strip() for t in types.split(",")] if types else None
        result = engineering_context(
            conn,
            tags=tag_list,
            task=task,
            token_budget=budget,
            types=type_list,
            include_stale=include_stale,
            current_task=current_task,
            max_item_chars=max_item_chars,
            as_of=as_of,
            include_expired=include_expired,
        )
        click.echo(json.dumps(result, indent=2))


@cli.command("file-context")
@click.argument("path")
@click.option("--reason", default="manual file context")
@click.option("--refresh/--cached", default=True, help="Refresh the TLDR when content changed")
@click.pass_context
def file_context_cmd(
    ctx: click.Context, path: str, reason: str, refresh: bool
) -> None:
    """Return a file's current-state TLDR and freshness."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_file_context(conn, path, reason=reason, refresh=refresh)
        click.echo(json.dumps(result, indent=2))


@cli.group("files")
def files_group() -> None:
    """File-state memory maintenance."""


@files_group.command("backfill")
@click.option("--apply", "apply_", is_flag=True, help="Write the backfilled TLDRs")
@click.option("--prune-missing", is_flag=True, help="Delete legacy file records whose source file is gone")
@click.pass_context
def files_backfill(ctx: click.Context, apply_: bool, prune_missing: bool) -> None:
    """Convert legacy file-change records into current-state TLDRs."""
    from .db import get_all_items

    with db_connection(project=ctx.obj.get("project")) as conn:
        items = [
            item for item in get_all_items(conn)
            if item.metadata
            and item.metadata.get("path")
            and item.metadata.get("fileKind") != "file_tldr"
        ]
        results = []
        for item in items:
            if not apply_:
                results.append({"id": item.id, "path": item.metadata["path"]})
                continue
            result = memory_file_context(
                conn, item.metadata["path"], reason="legacy file memory backfill"
            )
            if (
                prune_missing
                and result.get("error", "").startswith("File not found:")
            ):
                memory_delete(
                    conn, item.id,
                    reason="legacy file-state record: source file no longer exists",
                )
                result = {**result, "pruned": True}
            results.append({"id": item.id, **result})
        click.echo(json.dumps({
            "dryRun": not apply_,
            "count": len(results),
            "items": results,
        }, indent=2))


@cli.command()
@click.argument("item_id")
@click.pass_context
def timeline(ctx: click.Context, item_id: str) -> None:
    """Show the immutable timeline of a memory item."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        click.echo(json.dumps(memory_history(conn, item_id), indent=2))


@cli.command()
@click.option("--output", "-o", default=None, help="Output file path (default: stdout)")
@click.pass_context
def export(ctx: click.Context, output: str | None) -> None:
    """Export all memories and conflicts as JSON."""
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_export(conn)
        data = json.dumps(result, indent=2)
        if output:
            from pathlib import Path
            Path(output).write_text(data)
            click.echo(f"Exported to {output}")
        else:
            click.echo(data)


@cli.command(name="import")
@click.argument("file", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Validate and report without writing")
@click.option("--strict", "mode", flag_value="strict", help="Abort with no changes if any record is invalid")
@click.option("--replace", "mode", flag_value="replace", help="Clear existing data before importing")
@click.option("--normal", "mode", flag_value="normal", default=True, help="Skip invalid records (default)")
@click.pass_context
def import_cmd(ctx: click.Context, file: str, mode: str, dry_run: bool) -> None:
    """Import memories from a JSON export file."""
    from pathlib import Path

    from .models import LIMITS

    path = Path(file)
    if path.stat().st_size > LIMITS["import_bytes"]:
        raise click.ClickException(f"file exceeds {LIMITS['import_bytes']} bytes")
    data = json.loads(path.read_text())
    with db_connection(project=ctx.obj.get("project")) as conn:
        result = memory_import(conn, data, mode=mode, dry_run=dry_run)
        click.echo(json.dumps(result, indent=2))


def main() -> None:
    cli()


def mcp_main() -> None:
    """Entry point for MCP server."""
    from .server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
