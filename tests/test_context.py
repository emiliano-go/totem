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


class TestEpistemicVisibility:
    def test_resolved_conflict_leaves_context(self, fresh_db):
        from totem_mcp.db import get_all_conflicts, get_conflicts_for_item
        from totem_mcp.tools import memory_create, resolve_conflict

        memory_create(
            fresh_db, type="decision", title="Use Redis",
            statement="We use Redis for the queue.", tags=["infra"],
            metadata={"rationale": "atomic claims"},
        )
        second = memory_create(
            fresh_db, type="decision", title="Use Redis",
            statement="We use SQLite instead.", tags=["infra"],
            metadata={"rationale": "simpler"},
        )
        assert get_conflicts_for_item(fresh_db, second["id"])

        before = engineering_context(fresh_db, tags=["infra"], task="queue")
        assert "Conflict:" in before["context"]

        conflict_id = fresh_db.execute("SELECT id FROM conflicts").fetchone()[0]
        resolve_conflict(fresh_db, conflict_id, "Use SQLite")

        after = engineering_context(fresh_db, tags=["infra"], task="queue")
        assert "Conflict:" not in after["context"]
        assert get_all_conflicts(fresh_db)  # still available for audit

    def test_stale_knowledge_stays_visible(self, fresh_db, tmp_path):
        from totem_mcp.hashing import hash_content
        from totem_mcp.tools import memory_create, memory_get

        target = tmp_path / "code.py"
        target.write_text("line one\nline two\n")
        evidence = {
            "path": str(target),
            "startLine": 1,
            "endLine": 2,
            "contentHash": hash_content(target.read_text()),
            "kind": "source",
            "capturedAt": "2026-01-01T00:00:00+00:00",
        }
        item = memory_create(
            fresh_db, type="gotcha", title="Evidence gotcha",
            statement="This file validates tokens.", tags=["auth"],
            evidence=[evidence],
        )

        target.write_text("changed\n")  # evidence goes stale
        got = memory_get(fresh_db, item["id"])
        assert got is not None

        result = engineering_context(fresh_db, tags=["auth"], task="login")
        assert "STALE KNOWLEDGE" in result["context"]
        assert "Evidence gotcha" in result["context"]
        assert item["id"] in result["staleIds"]


def test_context_token_budget_is_a_hard_upper_bound(fresh_db):
    for i in range(60):
        memory_create(
            fresh_db, type="gotcha", title=f"G{i}",
            statement=f"fact {i} " + "x" * 200, tags=["t"],
        )
    result = engineering_context(fresh_db, tags=["t"], task="t", token_budget=200)
    assert result["budget"] == 200
    assert result["estimatedTokens"] <= 200
    # the serialized context must not blow past the requested budget
    assert len(result["context"]) // 4 <= 220
    assert result["omitted"]["items"] > 0
    assert result["omittedIds"]


def test_context_without_budget_includes_everything(fresh_db):
    for i in range(10):
        memory_create(
            fresh_db, type="gotcha", title=f"U{i}",
            statement=f"note {i} " + "y" * 40, tags=["t"],
        )
    result = engineering_context(fresh_db, tags=["t"], task="t")
    assert result["budget"] is None
    assert "U0" in result["context"] and "U9" in result["context"]
