"""Near-duplicate consolidation and contradiction hints."""

from __future__ import annotations

from totem_mcp.db import get_item
from totem_mcp.tools import memory_consolidate, memory_create


def test_consolidate_dry_run_then_apply(fresh_db):
    first = memory_create(
        fresh_db, type="gotcha", title="Retry A",
        statement="Failed deliveries retry with exponential backoff.",
        tags=["queue"],
    )["id"]
    second = memory_create(
        fresh_db, type="gotcha", title="Retry B",
        statement="Failed deliveries retry with exponential backoff again.",
        tags=["queue"],
    )["id"]

    dry = memory_consolidate(fresh_db, apply=False, threshold=0.7)
    assert dry["dryRun"] is True
    assert dry["count"] >= 1
    assert get_item(fresh_db, second).status.value == "active"

    applied = memory_consolidate(fresh_db, apply=True, threshold=0.7)
    assert applied["dryRun"] is False
    statuses = {
        get_item(fresh_db, first).status.value,
        get_item(fresh_db, second).status.value,
    }
    assert "superseded" in statuses


def test_consolidate_flags_opposite_polarity(fresh_db):
    memory_create(
        fresh_db, type="invariant", title="Retries A",
        statement="The queue retries failed deliveries.",
        tags=["queue"], metadata={"verificationMethod": "test", "condition": "failure"},
    )
    memory_create(
        fresh_db, type="invariant", title="Retries B",
        statement="The queue never retries failed deliveries.",
        tags=["queue"], metadata={"verificationMethod": "test", "condition": "failure"},
    )
    report = memory_consolidate(fresh_db, apply=False, threshold=0.6)
    assert report["count"] >= 1
    assert any(cluster["contradictionCandidate"] for cluster in report["clusters"])
