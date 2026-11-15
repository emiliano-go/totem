"""Deterministic real-corpus benchmark for engineering memory."""

from __future__ import annotations

import json
from pathlib import Path

import turso

from .context import engineering_context
from .tools import memory_search


def load_cases(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark file must contain a non-empty cases list")
    return cases


def run_cases(
    conn: turso.Connection,
    cases: list[dict],
    k: int = 5,
) -> dict:
    """Score title-level retrieval, abstention, and temporal cutoffs."""
    details = []
    recalls = []
    reciprocal = []
    abstentions = []
    for case in cases:
        name = str(case.get("name") or "case")
        if case.get("abstain") is True:
            context = engineering_context(
                conn,
                tags=list(case.get("tags") or []),
                task=str(case.get("query") or ""),
            )
            correct = context.get("abstained") is True
            abstentions.append(1.0 if correct else 0.0)
            details.append({"name": name, "kind": "abstention", "pass": correct})
            continue
        hits = memory_search(
            conn,
            str(case.get("query") or ""),
            tags=list(case.get("tags") or []) or None,
            limit=k,
            as_of=case.get("as_of"),
        )
        expected = [str(value).lower() for value in case.get("expect") or []]
        rank = None
        for index, hit in enumerate(hits, start=1):
            title = str(hit.get("title") or "").lower()
            if any(value in title for value in expected):
                rank = index
                break
        recalls.append(1.0 if rank else 0.0)
        reciprocal.append(1.0 / rank if rank else 0.0)
        details.append({
            "name": name,
            "kind": "retrieval",
            "rank": rank,
            "top": [hit.get("title") for hit in hits[:k]],
        })
    total = len(cases)
    retrieval = len(recalls)
    return {
        "cases": total,
        "retrievalCases": retrieval,
        "abstentionCases": len(abstentions),
        "recallAtK": round(sum(recalls) / retrieval, 3) if retrieval else 1.0,
        "mrr": round(sum(reciprocal) / retrieval, 3) if retrieval else 1.0,
        "abstentionAccuracy": (
            round(sum(abstentions) / len(abstentions), 3)
            if abstentions else 1.0
        ),
        "details": details,
    }
