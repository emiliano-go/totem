"""P4 portability: versioned export/import round-trip and the history timeline."""

from __future__ import annotations

import pytest

from totem_mcp.context import engineering_context
from totem_mcp.db import connect, init_db
from totem_mcp.tools import (
    memory_create,
    memory_export,
    memory_history,
    memory_import,
    memory_relate,
    memory_update,
)


def _seed(conn):
    first = memory_create(
        conn, type="decision", title="Use SQLite",
        statement="We use SQLite for storage.", tags=["db"],
        metadata={"rationale": "single container"},
    )
    second = memory_create(
        conn, type="invariant", title="Ids are UUIDs",
        statement="Every memory id is a UUID.", tags=["db"],
        metadata={"verificationMethod": "schema test", "condition": "id format"},
    )
    memory_relate(conn, first["id"], second["id"], "depends_on")
    return first, second


def test_export_import_round_trip(fresh_db, tmp_path):
    _seed(fresh_db)
    export = memory_export(fresh_db)
    assert export["format_version"] == 1
    assert export["schema_version"] >= 4
    assert len(export["relations"]) == 1

    copy = connect(db_path=tmp_path / "copy.db")
    init_db(copy)
    result = memory_import(copy, export)
    assert result["items_imported"] == 2
    assert result["relations_imported"] == 1

    original = engineering_context(fresh_db, tags=["db"], task="storage")["context"]
    restored = engineering_context(copy, tags=["db"], task="storage")["context"]
    assert original == restored
    copy.close()


def test_import_accepts_legacy_format(fresh_db, tmp_path):
    _seed(fresh_db)
    legacy = {"items": memory_export(fresh_db)["items"]}  # no format_version/relations

    copy = connect(db_path=tmp_path / "legacy.db")
    init_db(copy)
    result = memory_import(copy, legacy)
    assert result["items_imported"] == 2
    copy.close()


def test_import_rejects_newer_format(fresh_db):
    with pytest.raises(ValueError, match="newer"):
        memory_import(fresh_db, {"format_version": 99, "items": []})


def test_import_requires_items(fresh_db):
    with pytest.raises(ValueError, match="items"):
        memory_import(fresh_db, {"format_version": 1})


def test_history_timeline(fresh_db):
    created = memory_create(
        fresh_db, type="gotcha", title="Timeline", statement="Initial claim.", tags=["t"]
    )
    memory_update(
        fresh_db, created["id"], statement="Corrected claim.", reason="was wrong"
    )
    history = memory_history(fresh_db, created["id"])
    events = [h["event"] for h in history]
    assert "created" in events and "updated" in events
    assert any(h["field"] == "statement" and h["new_value"] == "Corrected claim." for h in history)
    assert all(h["timestamp"] for h in history)


def test_import_dry_run_does_not_mutate(fresh_db, tmp_path):
    from totem_mcp.db import get_all_items

    _seed(fresh_db)
    export = memory_export(fresh_db)
    copy = connect(db_path=tmp_path / "dry.db")
    init_db(copy)
    report = memory_import(copy, export, dry_run=True)
    assert report["dry_run"] is True
    assert report["items_imported"] == 2
    assert report["relations_imported"] == 1
    assert get_all_items(copy) == []
    copy.close()


def test_import_strict_aborts_on_invalid(tmp_path):
    from totem_mcp.db import get_all_items

    export = {
        "format_version": 1,
        "items": [
            {"id": "x", "type": "gotcha", "title": "ok", "statement": "fine", "tags": ["t"]},
            {"id": "y", "type": "invariant", "title": "bad", "statement": "no meta", "tags": ["t"]},
        ],
    }
    copy = connect(db_path=tmp_path / "strict.db")
    init_db(copy)
    report = memory_import(copy, export, mode="strict")
    assert report["aborted"] is True
    assert report["items_imported"] == 0
    assert report["errors"]
    assert get_all_items(copy) == []  # nothing written
    copy.close()


def test_import_replace_clears_existing(fresh_db, tmp_path):
    from totem_mcp.db import get_all_items

    _seed(fresh_db)
    export = memory_export(fresh_db)
    copy = connect(db_path=tmp_path / "rep.db")
    init_db(copy)
    memory_import(copy, export)
    assert len(get_all_items(copy)) == 2

    report = memory_import(copy, {"format_version": 1, "items": []}, mode="replace")
    assert report["items_imported"] == 0
    assert get_all_items(copy) == []
    copy.close()


def test_import_normal_skips_invalid_and_reports(fresh_db):
    export = {
        "format_version": 1,
        "items": [
            {"id": "x", "type": "gotcha", "title": "ok", "statement": "fine", "tags": ["t"]},
            {"id": "y", "type": "invariant", "title": "bad", "statement": "no meta", "tags": ["t"]},
        ],
    }
    report = memory_import(fresh_db, export, mode="normal")
    assert report["items_imported"] == 1
    assert report["items_skipped"] == 1
    assert any(e["section"] == "items" for e in report["errors"])


def test_history_records_actor_and_source(fresh_db):
    item = memory_create(
        fresh_db, type="gotcha", title="Audited", statement="audited claim", tags=["t"],
        actor="kimi", session="abc123", request_id="req-1",
    )
    created = next(h for h in memory_history(fresh_db, item["id"]) if h["event"] == "created")
    assert created["source"] == "memory_create"
    assert created["actor"] == "kimi"
    assert created["session"] == "abc123"
    assert created["request_id"] == "req-1"

    other = memory_create(
        fresh_db, type="gotcha", title="Other", statement="other claim", tags=["t"]
    )
    memory_relate(fresh_db, item["id"], other["id"], "depends_on", actor="kimi")
    related = [h for h in memory_history(fresh_db, item["id"]) if h["event"] == "related"]
    assert related and related[0]["actor"] == "kimi"
