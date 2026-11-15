"""Five LongMemEval-style abilities applied to engineering memory."""

from datetime import datetime, timedelta, timezone

from totem_mcp.context import engineering_context
from totem_mcp.tools import memory_create, memory_search


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_extraction(fresh_db):
    item = memory_create(
        fresh_db, type="decision", title="Hybrid retrieval",
        statement="Context uses lexical and dense candidates fused with RRF.",
        tags=["retrieval"],
    )
    hits = memory_search(fresh_db, "lexical dense RRF", tags=None, limit=5)
    assert hits and hits[0]["id"] == item["id"]


def test_multi_session_reasoning(fresh_db):
    first = memory_create(
        fresh_db, type="gotcha", title="Retry cap",
        statement="Retries stop after three attempts.", tags=["retry"],
    )
    second = memory_create(
        fresh_db, type="decision", title="Retry jitter",
        statement="Retries use full jitter to avoid synchronized storms.",
        tags=["retry"],
    )
    hits = memory_search(fresh_db, "retry attempts jitter", tags=["retry"], limit=5)
    assert {hit["id"] for hit in hits[:2]} == {first["id"], second["id"]}


def test_temporal_reasoning(fresh_db):
    future = (_now() + timedelta(days=2)).isoformat()
    item = memory_create(
        fresh_db, type="decision", title="Future retry policy",
        statement="Retries use adaptive timeouts.", tags=["retry"],
        valid_from=future,
    )
    assert all(hit["id"] != item["id"] for hit in memory_search(
        fresh_db, "adaptive timeouts", tags=None, limit=5,
    ))
    later = (_now() + timedelta(days=3)).isoformat()
    result = engineering_context(
        fresh_db, tags=["retry"], task="adaptive timeouts", as_of=later,
    )
    assert item["id"] in result["selectedIds"]


def test_knowledge_update(fresh_db):
    old = memory_create(
        fresh_db, type="decision", title="Retry cap three",
        statement="Retries stop after three attempts.", tags=["retry"],
    )
    new = memory_create(
        fresh_db, type="decision", title="Retry cap five",
        statement="Retries stop after five attempts.", tags=["retry"],
        supersedes_id=old["id"],
    )
    result = engineering_context(fresh_db, tags=["retry"], task="retry cap")
    assert new["id"] in result["selectedIds"]
    assert old["id"] not in result["selectedIds"]


def test_abstention(fresh_db):
    result = engineering_context(fresh_db, tags=["nothing"], task="nothing here")
    assert result["abstained"] is True
