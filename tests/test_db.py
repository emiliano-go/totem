"""Regression tests for db.py: column alignment, FTS sanitizing, import, db_connection."""

from __future__ import annotations

import json

import pytest

from totem_mcp.db import (
    CREATE_FTS,
    connect,
    db_connection,
    get_all_items,
    get_item,
    import_items,
    init_db,
    insert_item,
    search_fts,
    soft_delete,
)
from totem_mcp.models import Evidence, MemoryItem, MemoryType


def _make_item(**overrides) -> MemoryItem:
    kwargs = dict(
        type=MemoryType.GOTCHA,
        title="FTS drops unicode",
        statement="FTS5 tokenizer drops non-ascii chars",
        details="observed on turso 0.7",
        tags=["cmd:pytest", "fts"],
        importance=0.8,
        scope="project",
        evidence=[
            Evidence(
                path="src/db.py",
                startLine=1,
                endLine=10,
                contentHash="abc123",
                capturedAt="2026-01-01T00:00:00+00:00",
            )
        ],
        metadata={"key": "value"},
    )
    kwargs.update(overrides)
    return MemoryItem(**kwargs)


class TestColumnAlignmentFreshDB:
    def test_insert_and_get_round_trip(self, fresh_db):
        item = _make_item()
        insert_item(fresh_db, item)

        got = get_item(fresh_db, item.id)
        assert got is not None
        assert got.id == item.id
        assert got.tags == ["cmd:pytest", "fts"]
        assert got.scope == "project"
        assert got.metadata == {"key": "value"}
        assert len(got.evidence) == 1
        assert got.evidence[0].path == "src/db.py"
        assert got.evidence[0].start_line == 1
        assert got.evidence[0].end_line == 10
        assert got.evidence[0].content_hash == "abc123"

    def test_get_all_items_round_trip(self, fresh_db):
        item = _make_item()
        insert_item(fresh_db, item)
        items = get_all_items(fresh_db)
        assert len(items) == 1
        assert items[0].scope == "project"
        assert items[0].tags == ["cmd:pytest", "fts"]

    def test_search_fts_round_trip(self, fresh_db):
        item = _make_item()
        insert_item(fresh_db, item)
        results = search_fts(fresh_db, "unicode")
        assert len(results) == 1
        assert results[0].id == item.id
        assert results[0].scope == "project"
        assert results[0].metadata == {"key": "value"}


# Old column layout: base columns WITHOUT scope mid-table, then
# verified_commit and scope appended via ALTER TABLE (migration order).
OLD_LAYOUT = """
CREATE TABLE memory_items (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    statement TEXT NOT NULL,
    details TEXT,
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 1.0,
    importance REAL NOT NULL DEFAULT 0.5,
    evidence TEXT NOT NULL DEFAULT '[]',
    related_memory_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    verified_at TEXT,
    metadata TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1
);
"""


@pytest.fixture
def migrated_db(db_path):
    """DB created with the pre-migration layout, then ALTERed like real migrations."""
    conn = connect(db_path=db_path)
    conn.executescript(OLD_LAYOUT)
    conn.execute("ALTER TABLE memory_items ADD COLUMN verified_commit TEXT")
    conn.execute("ALTER TABLE memory_items ADD COLUMN scope TEXT")
    conn.executescript(CREATE_FTS)
    conn.commit()
    init_db(conn)  # v3 -> v4: provenance columns added
    yield conn
    conn.close()


class TestColumnAlignmentMigratedDB:
    def test_reads_work_on_migrated_layout(self, migrated_db):
        item = _make_item()
        insert_item(migrated_db, item)

        got = get_item(migrated_db, item.id)
        assert got is not None
        assert got.scope == "project"
        assert got.tags == ["cmd:pytest", "fts"]
        assert got.metadata == {"key": "value"}
        assert got.evidence[0].content_hash == "abc123"

        all_items = get_all_items(migrated_db)
        assert len(all_items) == 1
        assert all_items[0].scope == "project"

        results = search_fts(migrated_db, "unicode")
        assert len(results) == 1
        assert results[0].scope == "project"

    def test_init_db_is_idempotent_on_migrated_layout(self, migrated_db):
        # scope column exists, so init_db should treat schema as current
        init_db(migrated_db)
        item = _make_item()
        insert_item(migrated_db, item)
        assert get_item(migrated_db, item.id) is not None


class TestSearchFtsHostileQueries:
    def test_sane_query_matches(self, fresh_db):
        insert_item(fresh_db, _make_item())
        assert len(search_fts(fresh_db, "unicode tokenizer")) == 1

    @pytest.mark.parametrize(
        "query",
        ["cmd: git status", "foo (bar", '"unbalanced', "AND", ""],
    )
    def test_hostile_queries_do_not_raise(self, fresh_db, query):
        insert_item(fresh_db, _make_item())
        results = search_fts(fresh_db, query)
        assert isinstance(results, list)

    def test_empty_query_returns_nothing(self, fresh_db):
        insert_item(fresh_db, _make_item())
        assert search_fts(fresh_db, "") == []


class TestImportItems:
    def test_soft_deleted_id_is_skipped_and_import_completes(self, fresh_db):
        item = _make_item()
        insert_item(fresh_db, item)
        soft_delete(fresh_db, item.id)

        new_item = _make_item(title="Another gotcha")
        result = import_items(
            fresh_db,
            [
                json.loads(new_item.model_dump_json(by_alias=True)),
                json.loads(item.model_dump_json(by_alias=True)),
            ],
        )
        assert result == {"imported": 1, "skipped": 1}
        assert get_item(fresh_db, new_item.id) is not None

    def test_malformed_item_does_not_abort_batch(self, fresh_db):
        good = _make_item(title="Good one")
        result = import_items(
            fresh_db,
            [
                {"id": "broken", "type": "not-a-real-type"},
                {"no_id_here": True},
                json.loads(good.model_dump_json(by_alias=True)),
            ],
        )
        assert result["imported"] == 1
        assert result["skipped"] == 2
        assert get_item(fresh_db, good.id) is not None


class TestDbConnection:
    def test_normal_usage(self, project_dir):
        with db_connection(project=str(project_dir)) as conn:
            item = _make_item()
            insert_item(conn, item)
            assert get_item(conn, item.id) is not None
        assert (project_dir / ".totem" / "totem.db").exists()

    def test_setup_failure_closes_and_raises(self, tmp_path):
        # Corrupt DB file at the expected path: init_db must fail cleanly
        proj = tmp_path / "broken-proj"
        (proj / ".totem").mkdir(parents=True)
        (proj / ".totem" / "totem.db").write_bytes(b"this is not a sqlite database" * 100)
        with pytest.raises(Exception):
            with db_connection(project=str(proj)):
                pass


def test_user_db_path_env_override(monkeypatch, tmp_path):
    from totem_mcp import db as db_mod

    monkeypatch.setenv("TOTEM_USER_DB", str(tmp_path / "user.db"))
    assert db_mod.get_user_db_path() == tmp_path / "user.db"

    monkeypatch.delenv("TOTEM_USER_DB")
    assert db_mod.get_user_db_path().name == "totem.db"


def test_schema_version_recorded(fresh_db):
    from totem_mcp.db import SCHEMA_VERSION, _schema_version

    assert _schema_version(fresh_db) == SCHEMA_VERSION


def test_locator_migration_dedupes_and_enforces_unique(fresh_db):
    from totem_mcp.db import _migrate_locators

    fresh_db.execute("DROP INDEX IF EXISTS memory_locators_path_subject")
    fresh_db.execute(
        "CREATE INDEX memory_locators_path_subject ON memory_locators (path, subject)"
    )
    fresh_db.execute("INSERT INTO memory_locators VALUES ('a', '/x.py', 'sub', 's')")
    fresh_db.execute("INSERT INTO memory_locators VALUES ('b', '/x.py', 'sub', 's')")
    fresh_db.commit()

    _migrate_locators(fresh_db)

    rows = fresh_db.execute(
        "SELECT item_id FROM memory_locators WHERE path = '/x.py'"
    ).fetchall()
    assert len(rows) == 1
    sql = fresh_db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'memory_locators_path_subject'"
    ).fetchone()[0]
    assert "UNIQUE" in sql.upper()
    with pytest.raises(Exception):
        fresh_db.execute("INSERT INTO memory_locators VALUES ('c', '/x.py', 'sub', 's')")


def test_legacy_db_migrates_to_current_version(db_path):
    from totem_mcp.db import SCHEMA_VERSION, _schema_version, connect, init_db

    conn = connect(db_path=db_path)
    conn.executescript(OLD_LAYOUT)
    conn.commit()

    init_db(conn)

    assert _schema_version(conn) == SCHEMA_VERSION
    row = conn.execute(
        "SELECT 1 FROM pragma_table_info('memory_items') WHERE name = 'scope'"
    ).fetchone()
    assert row is not None
    conn.close()


def test_memory_create_rolls_back_on_failure(fresh_db, monkeypatch):
    """A failure mid-operation leaves no partial rows (atomic semantic ops)."""
    import totem_mcp.tools as tools
    from totem_mcp.models import MemoryItem

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(tools, "insert_history", boom)
    with pytest.raises(RuntimeError):
        tools.memory_create(
            fresh_db, type="gotcha", title="T", statement="S", tags=["x"]
        )
    assert get_all_items(fresh_db) == []


def test_memory_relate_rolls_back_on_failure(fresh_db, monkeypatch):
    import totem_mcp.tools as tools
    from totem_mcp.db import get_all_relations

    a = tools.memory_create(fresh_db, type="gotcha", title="A", statement="claim A", tags=["x"])
    b = tools.memory_create(fresh_db, type="gotcha", title="B", statement="claim B", tags=["x"])

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    # supersedes inserts the relation then updates the target status; a failure
    # in that second step must roll the relation back too.
    monkeypatch.setattr(tools, "update_item_row", boom)
    with pytest.raises(RuntimeError):
        tools.memory_relate(fresh_db, a["id"], b["id"], "supersedes")
    assert get_all_relations(fresh_db) == []


def _downgrade_to_v4(conn):
    import totem_mcp.db as db

    conn.execute("DROP INDEX IF EXISTS memory_relations_unique")
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '4')"
    )
    conn.commit()
    assert db._schema_version(conn) == 4


def test_migration_failure_rolls_back_and_does_not_stamp(db_path, monkeypatch):
    import totem_mcp.db as db

    conn = connect(db_path=db_path)
    init_db(conn)
    _downgrade_to_v4(conn)

    def boom(_conn):
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        [(2, db._migrate_conflicts), (3, db._migrate_v3),
         (4, db._migrate_epistemics), (5, boom)],
    )
    with pytest.raises(RuntimeError):
        db._migrate(conn)
    # version unchanged and the v5 index was never created
    assert db._schema_version(conn) == 4
    assert not db._has_index(conn, "memory_relations_unique")
    conn.close()


def test_migration_postcondition_failure_does_not_stamp(db_path, monkeypatch):
    import totem_mcp.db as db

    conn = connect(db_path=db_path)
    init_db(conn)
    _downgrade_to_v4(conn)

    def noop(_conn):
        return None

    # a migration that claims success but leaves the index missing must fail
    monkeypatch.setattr(
        db,
        "MIGRATIONS",
        [(2, db._migrate_conflicts), (3, db._migrate_v3),
         (4, db._migrate_epistemics), (5, noop)],
    )
    with pytest.raises(RuntimeError):
        db._migrate(conn)
    assert db._schema_version(conn) == 4
    conn.close()


def test_operation_id_makes_writes_idempotent(fresh_db):
    from totem_mcp.tools import memory_create, memory_relate

    first = memory_create(
        fresh_db, type="gotcha", title="Idem", statement="idempotent claim",
        tags=["t"], operation_id="op-1",
    )
    replay = memory_create(
        fresh_db, type="gotcha", title="Idem", statement="idempotent claim",
        tags=["t"], operation_id="op-1",
    )
    assert replay.get("replayed") is True
    assert replay["id"] == first["id"]
    assert len([i for i in get_all_items(fresh_db) if i.title == "Idem"]) == 1

    other = memory_create(fresh_db, type="gotcha", title="O", statement="other claim", tags=["t"])
    rel1 = memory_relate(fresh_db, first["id"], other["id"], "depends_on", operation_id="op-2")
    rel2 = memory_relate(fresh_db, first["id"], other["id"], "depends_on", operation_id="op-2")
    assert rel2.get("replayed") is True
    assert rel1["id"] == rel2["id"]
