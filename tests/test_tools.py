"""Tests for tools.py: memory CRUD round-trip, staleness, register_file_read/write."""

from __future__ import annotations

from totem_mcp.tools import (
    memory_create,
    memory_delete,
    memory_get,
    memory_update,
    register_file_read,
    register_file_write,
)


class TestMemoryCrudRoundTrip:
    def test_create_get_update_delete(self, fresh_db):
        created = memory_create(
            fresh_db,
            type="decision",
            title="13 memory types",
            statement="Use 13 types over 4 for precision",
            tags=["architecture"],
            metadata={"rationale": "finer-grained retrieval"},
        )
        item_id = created["id"]
        assert created["status"] == "active"

        got = memory_get(fresh_db, item_id)
        assert got is not None
        assert got["title"] == "13 memory types"
        assert got["metadata"]["rationale"] == "finer-grained retrieval"

        updated = memory_update(
            fresh_db, id=item_id, statement="Use 13 types", reason="wording"
        )
        assert updated["statement"] == "Use 13 types"

        deleted = memory_delete(fresh_db, item_id, reason="no longer relevant")
        assert deleted["status"] == "deleted"
        assert memory_get(fresh_db, item_id) is None

    def test_get_missing_returns_none(self, fresh_db):
        assert memory_get(fresh_db, "nonexistent-id") is None

    def test_staleness_flagged_after_file_change(self, fresh_db, sample_file):
        result = register_file_read(
            fresh_db,
            path=str(sample_file),
            statement="sample file facts",
            subject="sample",
            kind="module",
            tags=["sample"],
        )
        item_id = result["id"]
        assert memory_get(fresh_db, item_id)["status"] == "active"

        sample_file.write_text("line one\nCHANGED\nline three\nline four\nline five\n")
        got = memory_get(fresh_db, item_id)
        assert got["status"] == "potentially_stale"
        assert any("stale" in w.lower() for w in got.get("warnings", []))


class TestRegisterFileReadWrite:
    def test_create_then_update_same_path(self, fresh_db, sample_file):
        r1 = register_file_read(
            fresh_db,
            path=str(sample_file),
            statement="first read",
            subject="sample",
            kind="module",
            tags=["alpha"],
            title="My custom title",
        )
        assert r1["action"] == "created"

        r2 = register_file_read(
            fresh_db,
            path=str(sample_file),
            statement="second read",
            subject="sample",
            kind="module",
            tags=["beta", "gamma"],
        )
        assert r2["action"] == "updated"
        assert r2["id"] == r1["id"]

        got = memory_get(fresh_db, r1["id"])
        # tags actually replaced, not merged
        assert got["tags"] == ["beta", "gamma"]
        # explicit title preserved when later call omits title
        assert got["title"] == "My custom title"
        assert got["statement"] == "second read"

    def test_register_file_write_create_then_update(self, fresh_db, sample_file):
        r1 = register_file_write(
            fresh_db,
            path=str(sample_file),
            statement="wrote sample",
            reason="initial",
            tags=["writes"],
        )
        assert r1["action"] == "created"
        r2 = register_file_write(
            fresh_db,
            path=str(sample_file),
            statement="rewrote sample",
            reason="fix typo",
            tags=["writes-v2"],
        )
        assert r2["action"] == "updated"
        assert r2["id"] == r1["id"]
        got = memory_get(fresh_db, r1["id"])
        assert got["tags"] == ["writes-v2"]
        assert got["metadata"]["reason"] == "fix typo"

    def test_line_range_clamped_to_evidence(self, fresh_db, sample_file):
        r = register_file_read(
            fresh_db,
            path=str(sample_file),
            statement="clamped range",
            subject="sample",
            kind="module",
            tags=["t"],
            start_line=0,
            end_line=9999,
        )
        assert r["evidence"]["startLine"] == 1
        assert r["evidence"]["endLine"] == 5  # file has 5 lines
        got = memory_get(fresh_db, r["id"])
        assert got["metadata"]["startLine"] == 1
        assert got["metadata"]["endLine"] == 5

    def test_missing_file_errors(self, fresh_db, tmp_path):
        r = register_file_read(
            fresh_db,
            path=str(tmp_path / "nope.py"),
            statement="x",
            subject="x",
            kind="module",
            tags=["t"],
        )
        assert "error" in r
        r = register_file_write(
            fresh_db,
            path=str(tmp_path / "nope.py"),
            statement="x",
            reason="x",
            tags=["t"],
        )
        assert "error" in r

    def test_invalid_line_range_errors(self, fresh_db, sample_file):
        r = register_file_read(
            fresh_db,
            path=str(sample_file),
            statement="bad range",
            subject="sample",
            kind="module",
            tags=["t"],
            start_line=4,
            end_line=2,
        )
        assert "error" in r
        assert "Invalid line range" in r["error"]
