"""Markdown export and import for human-readable memory."""

from __future__ import annotations

import json
import re
from pathlib import Path

import turso

from .db import get_all_items, get_all_relations, insert_relation
from .tools import memory_create


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value[:60] or "memory"


def export_markdown(conn: turso.Connection, directory: str | Path) -> dict:
    """Write one Markdown note per active memory, plus wikilink relations."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    items = get_all_items(conn)
    relations = get_all_relations(conn)
    by_from: dict[str, list[dict]] = {}
    for relation in relations:
        by_from.setdefault(relation["from_id"], []).append(relation)
    written = 0
    for item in items:
        folder = root / item.type.value
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{_slug(item.title)}-{item.id[:8]}.md"
        lines = [
            "---",
            f"id: {json.dumps(item.id)}",
            f"type: {json.dumps(item.type.value)}",
            f"title: {json.dumps(item.title)}",
            f"status: {json.dumps(item.status.value)}",
            f"confidence: {item.confidence}",
            f"importance: {item.importance}",
            f"tags: {json.dumps(item.tags)}",
            f"asserted_by: {json.dumps(item.asserted_by)}",
            f"scope: {json.dumps(item.scope)}",
            f"created_at: {json.dumps(item.created_at)}",
            f"updated_at: {json.dumps(item.updated_at)}",
            f"valid_from: {json.dumps(item.valid_from)}",
            f"valid_to: {json.dumps(item.valid_to)}",
            f"metadata: {json.dumps(item.metadata or {})}",
            f"evidence: {json.dumps([ev.model_dump(by_alias=True) for ev in item.evidence])}",
            "---",
            "",
            f"# {item.title}",
            "",
            item.statement,
        ]
        if item.details:
            lines.extend(["", "## Details", "", item.details])
        if by_from.get(item.id):
            lines.extend(["", "## Relations"])
            lines.extend(
                f"- {relation['kind']} [[{relation['to_id']}]]"
                for relation in by_from[item.id]
            )
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written += 1
    return {"directory": str(root), "written": written}


def _parse_note(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    _, frontmatter, body = text.split("---", 2)
    fields: dict = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        raw = value.strip()
        try:
            fields[key.strip()] = json.loads(raw)
        except json.JSONDecodeError:
            fields[key.strip()] = raw
    main_body = body.split("## Relations", 1)[0]
    lines = [line for line in main_body.splitlines() if line.strip() and not line.startswith("#")]
    statement = lines[0] if lines else ""
    details = "\n".join(lines[1:]) if len(lines) > 1 else None
    related = []
    for line in body.splitlines():
        match = re.match(r"- (\w+) \[\[([^\]]+)\]\]", line.strip())
        if match:
            related.append({"kind": match.group(1), "to": match.group(2)})
    return {**fields, "statement": statement, "details": details, "relations": related}


def import_markdown(conn: turso.Connection, directory: str | Path) -> dict:
    """Import Markdown notes, preserving relations by source id."""
    root = Path(directory)
    if not root.exists():
        raise ValueError(f"directory not found: {root}")
    notes = []
    for path in sorted(root.rglob("*.md")):
        parsed = _parse_note(path)
        if parsed and parsed.get("type") and parsed.get("title"):
            notes.append(parsed)
    mapping: dict[str, str] = {}
    created = 0
    skipped = 0
    for note in notes:
        source_id = str(note.get("id") or "")
        if source_id:
            row = conn.execute(
                "SELECT id FROM memory_items WHERE status != 'deleted' "
                "AND json_extract(metadata, '$.sourceId') = ? LIMIT 1",
                (source_id,),
            ).fetchone()
            if row is not None:
                mapping[source_id] = row[0]
                skipped += 1
                continue
        metadata = dict(note.get("metadata") or {})
        if source_id:
            metadata["sourceId"] = source_id
        result = memory_create(
            conn,
            type=str(note["type"]),
            title=str(note["title"]),
            statement=str(note.get("statement") or note["title"]),
            tags=list(note.get("tags") or ["imported"]),
            details=note.get("details"),
            confidence=note.get("confidence"),
            importance=float(note.get("importance") or 0.5),
            evidence=list(note.get("evidence") or []),
            metadata=metadata,
            asserted_by=note.get("asserted_by") or "doc",
            scope=note.get("scope") or "project",
            valid_from=note.get("valid_from"),
            valid_to=note.get("valid_to"),
        )
        mapping[source_id] = result["id"]
        created += 1
    linked = 0
    for note in notes:
        source_id = str(note.get("id") or "")
        from_id = mapping.get(source_id)
        if not from_id:
            continue
        for relation in note.get("relations") or []:
            to_id = mapping.get(relation["to"])
            if not to_id:
                continue
            try:
                insert_relation(conn, from_id, to_id, relation["kind"])
                linked += 1
            except (turso.IntegrityError, ValueError):
                pass
    return {"directory": str(root), "created": created, "skipped": skipped, "linked": linked}
