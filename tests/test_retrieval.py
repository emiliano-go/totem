"""Corpus-wide candidates, graph expansion, temporal validity, and search."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from totem_mcp.context import engineering_context
from totem_mcp.retrieval import build_candidates
from totem_mcp.tools import memory_create, memory_relate, memory_search


def _item(conn, title, statement, tags=None, **kwargs):
    return memory_create(
        conn, type="gotcha", title=title, statement=statement,
        tags=tags or ["misc"], **kwargs,
    )


def test_untagged_memory_surfaces_by_task(fresh_db):
    tagged = _item(fresh_db, "Auth", "JWT refresh rotation.", ["auth"])
    untagged = _item(fresh_db, "Queue", "Failed deliveries retry with exponential backoff.", ["infra"])

    candidates, _meta = build_candidates(
        fresh_db, "retry failed deliveries", ["auth"], None
    )
    by_id = {candidate.item.id: candidate for candidate in candidates}
    assert untagged["id"] in by_id
    assert tagged["id"] in by_id
    assert by_id[untagged["id"]].dense > 0 or by_id[untagged["id"]].lexical > 0

    result = engineering_context(fresh_db, tags=["auth"], task="retry failed deliveries")
    assert untagged["id"] in result["selectedIds"]


def test_relation_hop_is_a_candidate(fresh_db):
    first = _item(fresh_db, "A", "The parser rejects empty input.", ["parse"])
    second = _item(fresh_db, "B", "Validation runs before parsing.", ["validate"])
    memory_relate(fresh_db, second["id"], first["id"], "depends_on")

    candidates, _meta = build_candidates(fresh_db, "parser rejects empty input", [], None)
    by_id = {candidate.item.id: candidate for candidate in candidates}
    assert first["id"] in by_id
    assert second["id"] in by_id
    assert "relation" in by_id[second["id"]].reasons


def test_temporal_validity_excludes_expired(fresh_db):
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    older = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    expired = _item(
        fresh_db, "Old", "This queue used retries.", ["queue"],
        valid_from=older, valid_to=past,
    )

    candidates, _meta = build_candidates(fresh_db, "queue retries", [], None)
    assert expired["id"] not in {candidate.item.id for candidate in candidates}

    candidates, _meta = build_candidates(
        fresh_db, "queue retries", [], None, include_expired=True
    )
    assert expired["id"] in {candidate.item.id for candidate in candidates}

    result = engineering_context(fresh_db, tags=["queue"], task="queue retries")
    assert result["abstained"] is True
    result = engineering_context(
        fresh_db, tags=["queue"], task="queue retries", include_expired=True
    )
    assert expired["id"] in result["selectedIds"]


def test_search_is_hybrid_and_scored(fresh_db):
    item = _item(
        fresh_db, "Queue", "Failed deliveries retry with exponential backoff.", ["infra"]
    )
    results = memory_search(fresh_db, "exponential backoff", tags=None, limit=5)
    assert results
    assert results[0]["id"] == item["id"]
    assert results[0]["score"] > 0
    assert "lexical" in results[0]["reasons"] or "dense" in results[0]["reasons"]


def test_empty_corpus_abstains(fresh_db):
    result = engineering_context(fresh_db, tags=["nothing"], task="nothing here")
    assert result["abstained"] is True
    assert result["context"] == ""
