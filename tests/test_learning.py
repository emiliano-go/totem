"""Feedback, reranker training, and context consumption of learned weights."""

from __future__ import annotations

import pytest

from totem_mcp.learning import load_reranker
from totem_mcp.tools import (
    memory_create,
    memory_feedback,
    memory_train_models,
)


def _items(conn, count=12):
    return [
        memory_create(
            conn, type="gotcha", title=f"Item {i}",
            statement=f"Fact number {i} about queue retries.", tags=["queue"],
        )["id"]
        for i in range(count)
    ]


def test_feedback_records_history(fresh_db):
    item_id = _items(fresh_db, 1)[0]
    result = memory_feedback(fresh_db, item_id, "useful", query="retries")
    assert result["signal"] == "useful"
    rows = fresh_db.execute(
        "SELECT event FROM memory_history WHERE item_id = ? AND event = 'feedback'",
        (item_id,),
    ).fetchall()
    assert rows


def test_train_models_needs_both_classes(fresh_db):
    ids = _items(fresh_db, 6)
    for item_id in ids:
        memory_feedback(fresh_db, item_id, "useful", query="queue", tags=["queue"])
    result = memory_train_models(fresh_db, min_examples=4)
    assert result["trained"] is False
    assert result["reason"] == "needs_both_classes"


def test_train_models_fits_and_persists(fresh_db):
    ids = _items(fresh_db, 12)
    for index, item_id in enumerate(ids):
        signal = "useful" if index % 2 == 0 else "wrong"
        memory_feedback(
            fresh_db, item_id, signal, query="queue retries", tags=["queue"]
        )
    result = memory_train_models(fresh_db, min_examples=8)
    assert result["trained"] is True
    assert result["reranker"] == "memory-linear-v1"
    assert load_reranker(fresh_db) is not None


def test_bad_feedback_signal_is_rejected(fresh_db):
    item_id = _items(fresh_db, 1)[0]
    with pytest.raises(ValueError, match="signal must be one of"):
        memory_feedback(fresh_db, item_id, "great")
