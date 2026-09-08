"""Tests for context.py: engineering_context sections, corrupt user DB, budget truncation."""

from __future__ import annotations

import totem_mcp.context as context_mod
from totem_mcp.context import engineering_context
from totem_mcp.tools import memory_create


def _seed(conn):
    ids = []
    ids.append(
        memory_create(
            conn,
            type="gotcha",
            title="FTS5 drops unicode",
            statement="Non-ascii chars vanish from FTS index",
            tags=["fts", "turso"],
        )["id"]
    )
    ids.append(
        memory_create(
            conn,
            type="decision",
            title="Explicit column lists",
            statement="Never use SELECT * on memory_items",
            tags=["db"],
            metadata={"rationale": "migrated DBs have different column order"},
        )["id"]
    )
    ids.append(
        memory_create(
            conn,
            type="constraint",
            title="pyturso only",
            statement="sqlite3 cannot read turso FTS indexes",
            tags=["db"],
            metadata={"constraint": "use pyturso exclusively"},
        )["id"]
    )
    return ids


class TestEngineeringContext:
    def test_sections_for_populated_db(self, fresh_db):
        _seed(fresh_db)
        result = engineering_context(fresh_db, tags=["db", "fts"], task="test task")
        text = result["context"]
        assert "TASK: test task" in text
        assert "GOTCHAS" in text
        assert "DECISIONS" in text
        assert "CRITICAL CONSTRAINTS" in text
        assert "BLOCKING AMBIGUITIES" in text
        assert "CONTEXT CONFLICTS" in text
        assert set(result["selectedIds"])  # non-empty
        assert result["omittedIds"] == []

    def test_corrupt_user_db_does_not_break_project_results(
        self, fresh_db, monkeypatch, tmp_path
    ):
        garbage = tmp_path / "garbage.db"
        garbage.write_bytes(b"definitely not a database" * 200)
        monkeypatch.setattr(context_mod, "get_user_db_path", lambda: garbage)

        seeded = _seed(fresh_db)
        result = engineering_context(fresh_db, tags=["db", "fts", "turso"])
        assert "GOTCHAS" in result["context"]
        assert set(seeded) & set(result["selectedIds"])

    def test_tiny_budget_truncates_without_overlap(self, fresh_db):
        _seed(fresh_db)
        result = engineering_context(
            fresh_db, tags=["db", "fts", "turso"], token_budget=20
        )
        assert result["omittedIds"], "expected some items omitted under tiny budget"
        assert not set(result["selectedIds"]) & set(result["omittedIds"])
