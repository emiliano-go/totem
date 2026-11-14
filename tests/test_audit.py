"""Hash-chained history: tampering with the log or an item is detected."""

from __future__ import annotations

from totem_mcp.tools import memory_audit, memory_create, memory_update


def _item(conn):
    return memory_create(
        conn, type="gotcha", title="g", statement="s", tags=["t"]
    )["id"]


def test_chain_verifies(fresh_db):
    item_id = _item(fresh_db)
    memory_update(fresh_db, item_id, statement="updated")
    verdict = memory_audit(fresh_db)
    assert verdict["ok"] is True
    assert verdict["rows"] >= 2
    assert verdict["first_bad_id"] is None
    assert verdict["state_mismatches"] == []


def test_rewriting_a_history_row_breaks_the_chain(fresh_db):
    item_id = _item(fresh_db)
    fresh_db.execute(
        "UPDATE memory_history SET new_value = 'tampered' "
        "WHERE item_id = ? AND event = 'created'",
        (item_id,),
    )
    verdict = memory_audit(fresh_db)
    assert verdict["ok"] is False
    assert verdict["first_bad_id"] is not None
    assert verdict["reason"] == "hash chain mismatch"


def test_deleting_a_history_row_breaks_the_chain(fresh_db):
    item_id = _item(fresh_db)
    memory_update(fresh_db, item_id, statement="one")
    memory_update(fresh_db, item_id, statement="two")
    fresh_db.execute(
        "DELETE FROM memory_history WHERE rowid = "
        "(SELECT MIN(rowid) + 1 FROM memory_history WHERE item_id = ?)",
        (item_id,),
    )
    assert memory_audit(fresh_db)["ok"] is False


def test_out_of_band_item_update_is_detected(fresh_db):
    item_id = _item(fresh_db)
    fresh_db.execute(
        "UPDATE memory_items SET statement = 'rewritten' WHERE id = ?", (item_id,)
    )
    verdict = memory_audit(fresh_db)
    assert verdict["ok"] is False
    assert item_id in verdict["state_mismatches"]
