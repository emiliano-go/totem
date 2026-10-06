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
    assert result["imported"] == 2
    assert result["relations"] == 1

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
    assert result["format_version"] == 0
    assert result["imported"] == 2
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
