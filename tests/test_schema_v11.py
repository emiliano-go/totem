"""Schema v12: validity, embeddings, feedback, heat, entities, and temporal edges."""

from __future__ import annotations

import pytest

from totem_mcp import models
from totem_mcp.db import (
    get_embedding,
    get_embeddings,
    list_feedback,
    record_feedback,
    upsert_embedding,
)
from totem_mcp.tools import memory_create


def test_schema_version_and_objects(fresh_db):
    assert models.SCHEMA_VERSION == 12
    columns = {
        row[1] for row in fresh_db.execute("PRAGMA table_info(memory_items)")
    }
    assert {"valid_from", "valid_to", "access_count", "last_accessed_at"} <= columns
    relation_columns = {
        row[1] for row in fresh_db.execute("PRAGMA table_info(memory_relations)")
    }
    assert {"recorded_at", "valid_from", "valid_to", "source_episode"} <= relation_columns
    tables = {
        row[0] for row in fresh_db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "memory_embeddings", "memory_feedback",
        "memory_entities", "memory_entity_links",
    } <= tables


def test_validity_window_round_trip(fresh_db):
    created = memory_create(
        fresh_db, type="gotcha", title="window", statement="A claim.",
        tags=["x"], valid_from="2026-01-01T00:00:00+00:00",
        valid_to="2026-02-01T00:00:00+00:00",
    )
    from totem_mcp.db import get_item

    stored = get_item(fresh_db, created["id"])
    assert stored.valid_from.startswith("2026-01-01")
    assert stored.valid_to.startswith("2026-02-01")


def test_invalid_validity_window_is_rejected(fresh_db):
    with pytest.raises(ValueError, match="valid_to must be later"):
        memory_create(
            fresh_db, type="gotcha", title="bad", statement="A claim.",
            tags=["x"], valid_from="2026-02-01T00:00:00+00:00",
            valid_to="2026-01-01T00:00:00+00:00",
        )


def test_embedding_round_trip(fresh_db):
    upsert_embedding(fresh_db, "item-1", "model2vec:test", 3, [1.0, 0.0, 0.0], "hash")
    assert get_embedding(fresh_db, "item-1", "model2vec:test")["vector"] == [1.0, 0.0, 0.0]
    assert "item-1" in get_embeddings(fresh_db, "model2vec:test")


def test_feedback_round_trip(fresh_db):
    item = memory_create(
        fresh_db, type="gotcha", title="f", statement="A claim.", tags=["x"]
    )
    record_feedback(fresh_db, item["id"], "useful", query="q", tags=["x"], weight=2)
    rows = list_feedback(fresh_db, item_id=item["id"])
    assert len(rows) == 1
    assert rows[0]["signal"] == "useful"
    assert rows[0]["weight"] == 2.0
    with pytest.raises(ValueError, match="signal must be one of"):
        record_feedback(fresh_db, item["id"], "bogus")
