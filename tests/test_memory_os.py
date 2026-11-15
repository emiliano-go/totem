from datetime import datetime, timedelta, timezone
from pathlib import Path

from totem_mcp.db import connect, init_db
from totem_mcp.paths import project_root
from totem_mcp.markdown import export_markdown, import_markdown
from totem_mcp.tools import (
    memory_create,
    memory_decay,
    memory_file_context,
    memory_graph,
    memory_heat,
    memory_induce_workflow,
    memory_maintenance,
    memory_profile,
    memory_relate,
    memory_relations,
    memory_rollup,
    memory_session_summary,
    memory_touch,
    memory_workflow,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_workflow_creates_and_updates_procedure(fresh_db):
    created = memory_workflow(
        fresh_db, "Release", ["Run tests", "Tag", "Publish"],
        verification=["Tests pass"],
    )
    again = memory_workflow(
        fresh_db, "Release", ["Run tests", "Build", "Tag", "Publish"],
    )
    assert created["action"] == "created"
    assert again["action"] == "updated"
    assert again["id"] == created["id"]


def test_profile_is_a_core_memory(fresh_db):
    profile = memory_profile(fresh_db, "identity", "The owner is Emiliano.")
    assert profile["action"] == "created"
    row = fresh_db.execute(
        "SELECT scope, json_extract(metadata, '$.memoryClass') "
        "FROM memory_items WHERE id = ?", (profile["id"],),
    ).fetchone()
    assert tuple(row) == ("user", "core")


def test_file_entities_and_graph(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "src/queue.py", '"""Queue helpers."""\n')
    item = memory_file_context(fresh_db, "src/queue.py")
    graph = memory_graph(fresh_db, entity="queue.py")
    assert any(entity["kind"] == "file" for entity in graph["entities"])
    assert any(entry["id"] == item["id"] for entry in graph["items"])


def test_rollup_builds_module_summary(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "src/a.py", '"""Alpha."""\n')
    _write(root, "src/b.py", '"""Beta."""\n')
    memory_file_context(fresh_db, "src/a.py")
    memory_file_context(fresh_db, "src/b.py")
    preview = memory_rollup(fresh_db, apply=False)
    assert preview["count"] == 1
    applied = memory_rollup(fresh_db, apply=True)
    assert applied["apply"] is True
    row = fresh_db.execute(
        "SELECT title FROM memory_items WHERE json_extract(metadata, '$.kind') = 'module_summary'"
    ).fetchone()
    assert row and row[0] == "Module: src"


def test_heat_and_decay_use_operational_state(fresh_db):
    root = Path(project_root(fresh_db))
    _write(root, "src/old.py", '"""Old."""\n')
    item = memory_file_context(fresh_db, "src/old.py")
    fresh_db.execute(
        "UPDATE memory_items SET updated_at = ? WHERE id = ?",
        ((datetime.now(timezone.utc) - timedelta(days=400)).isoformat(), item["id"]),
    )
    heat = memory_heat(fresh_db)
    assert heat["count"] == 1
    memory_touch(fresh_db, [item["id"]])
    assert fresh_db.execute(
        "SELECT access_count FROM memory_items WHERE id = ?", (item["id"],)
    ).fetchone()[0] == 1
    decay = memory_decay(fresh_db, days=180, dry_run=True)
    assert decay["count"] == 1
    applied = memory_decay(fresh_db, days=180, dry_run=False)
    assert applied["count"] == 1


def test_induce_workflow_from_session(fresh_db):
    session = memory_session_summary(
        fresh_db, "s1", "Implemented retry jitter and documented the cap.",
        files=["src/queue.py"],
    )
    draft = memory_induce_workflow(fresh_db, "s1", apply=False)
    assert draft["proposed"]["steps"]
    created = memory_induce_workflow(fresh_db, "s1", apply=True)
    assert created["action"] in ("created", "updated")
    assert session["id"]


def test_relations_are_bi_temporal(fresh_db):
    first = memory_create(
        fresh_db, type="decision", title="Old policy",
        statement="Retries stop after three attempts.", tags=["retry"],
    )
    second = memory_create(
        fresh_db, type="decision", title="New policy",
        statement="Retries stop after five attempts.", tags=["retry"],
    )
    now = datetime.now(timezone.utc).isoformat()
    memory_relate(
        fresh_db, second["id"], first["id"], "refines",
        valid_from=now, source_episode="session-1",
    )
    relation = memory_relations(fresh_db, second["id"])[0]
    assert relation["valid_from"] == now
    assert relation["source_episode"] == "session-1"


def test_maintenance_runs_all_cycles(fresh_db):
    result = memory_maintenance(fresh_db, apply=False)
    assert set(result) == {"entities", "revalidate", "rollup", "decay", "consolidate"}


def test_markdown_roundtrip(fresh_db, tmp_path):
    memory_create(
        fresh_db, type="decision", title="Queue policy",
        statement="Retries stop after three attempts.", tags=["retry"],
    )
    exported = export_markdown(fresh_db, tmp_path)
    assert exported["written"] == 1

    imported = connect(db_path=tmp_path / "import.db")
    try:
        init_db(imported)
        result = import_markdown(imported, tmp_path)
        assert result["created"] == 1
        again = import_markdown(imported, tmp_path)
        assert again["skipped"] == 1
        row = imported.execute(
            "SELECT title FROM memory_items WHERE status != 'deleted'"
        ).fetchone()
        assert row[0] == "Queue policy"
    finally:
        imported.close()
