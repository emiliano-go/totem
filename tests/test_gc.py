"""Memory GC: safe purge of terminal-state items with dry-run default."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from totem_mcp.db import get_item
from totem_mcp.tools import memory_create, memory_gc, memory_relate


def _old(days: int = 200) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _make(conn, status: str, updated_at: str, title: str) -> str:
    item = memory_create(
        conn, type="gotcha", title=title, statement=f"claim {title}", tags=["gc"]
    )
    conn.execute(
        "UPDATE memory_items SET status = ?, updated_at = ? WHERE id = ?",
        (status, updated_at, item["id"]),
    )
    conn.commit()
    return item["id"]


def test_gc_dry_run_reports_without_deleting(fresh_db):
    item_id = _make(fresh_db, "superseded", _old(), "old superseded")

    result = memory_gc(fresh_db, retention_days=90, dry_run=True)

    assert result["dry_run"] is True
    assert result["count"] == 1
    assert result["deleted"] == 0
    assert item_id in result["candidates"]
    assert get_item(fresh_db, item_id).status.value == "superseded"


def test_gc_apply_soft_deletes_then_is_idempotent(fresh_db):
    item_id = _make(fresh_db, "invalidated", _old(), "old invalidated")

    result = memory_gc(fresh_db, retention_days=90, dry_run=False)

    assert result["deleted"] == 1
    assert (
        fresh_db.execute(
            "SELECT status FROM memory_items WHERE id = ?", (item_id,)
        ).fetchone()[0]
        == "deleted"
    )
    assert memory_gc(fresh_db, retention_days=90, dry_run=False)["count"] == 0


def test_gc_skips_recent_and_active_items(fresh_db):
    _make(fresh_db, "active", _old(), "old active")
    recent = _make(fresh_db, "superseded", _old(days=1), "recent superseded")

    result = memory_gc(fresh_db, retention_days=90, dry_run=True)

    assert result["count"] == 0
    assert recent not in result["candidates"]


def test_gc_skips_items_with_inbound_relations(fresh_db):
    keeper = _make(fresh_db, "superseded", _old(), "referenced")
    source = _make(fresh_db, "active", _old(), "source")
    memory_relate(fresh_db, source, keeper, "supersedes")

    result = memory_gc(fresh_db, retention_days=90, dry_run=True)

    assert keeper not in result["candidates"]
    assert result["count"] == 0
