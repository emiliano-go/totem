"""Retrieval evaluation for the context compiler.

The ranking formula is hand-tuned; a change to it is a measurement question,
not an opinion.  A case names the tags/task/paths a caller would supply and the
memory ids that should be selected.  We report recall@k, MRR, and p95 latency,
and a gate that refuses a candidate which regresses recall/MRR beyond a
tolerance or exceeds the latency budget.

This mirrors the harness's ``eval/model_gate.py`` discipline: learned or tuned
ranking stays advisory until it wins on held-out cases.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass, field


@dataclass
class Case:
    name: str
    tags: list[str] = field(default_factory=list)
    task: str = ""
    paths: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    kind: str = "single"
    as_of: str | None = None
    include_expired: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Case":
        return cls(
            name=str(data.get("name") or "case"),
            tags=[str(t) for t in data.get("tags") or []],
            task=str(data.get("task") or ""),
            paths=[str(p) for p in data.get("paths") or []],
            expected=[str(i) for i in data.get("expected") or []],
            kind=str(data.get("kind") or "single"),
            as_of=str(data["as_of"]) if data.get("as_of") else None,
            include_expired=bool(data.get("include_expired", False)),
        )


def load_cases(path: str) -> list[Case]:
    data = json.loads(open(path, encoding="utf-8").read())
    raw = data.get("cases") if isinstance(data, dict) else data
    return [Case.from_dict(item) for item in (raw or [])]


def auto_cases(items, limit: int = 100) -> list[Case]:
    """A smoke benchmark: each item's own tags/title should surface itself."""
    cases: list[Case] = []
    for item in items[:limit]:
        cases.append(Case(
            name=item.id,
            tags=list(item.tags),
            task=item.title,
            paths=sorted({ev.path for ev in item.evidence}),
            expected=[item.id],
        ))
    return cases


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    return ordered[index]


def evaluate(cases: list[Case], runner, k: int = 10) -> dict:
    """Run every case through ``runner(case) -> engineering_context result``."""
    recalls: list[float] = []
    reciprocal: list[float] = []
    ndcg: list[float] = []
    latencies: list[float] = []
    abstention_hits = 0
    abstention_total = 0
    details: list[dict] = []
    for case in cases:
        started = time.perf_counter()
        try:
            result = runner(case)
        except Exception as exc:  # a bad case is a failed case, not a crash
            result = {"error": f"{type(exc).__name__}: {exc}", "selectedIds": []}
        latency = (time.perf_counter() - started) * 1000.0
        latencies.append(latency)
        selected = list(result.get("selectedIds") or [])[:k]
        expected = set(case.expected)
        if case.kind == "abstention":
            abstention_total += 1
            abstained = bool(result.get("abstained")) or not selected
            recall = 1.0 if abstained else 0.0
            rr = recall
            if abstained:
                abstention_hits += 1
                recalls.append(1.0)
                reciprocal.append(1.0)
                ndcg.append(1.0)
            else:
                recalls.append(0.0)
                reciprocal.append(0.0)
                ndcg.append(0.0)
        elif expected:
            recall = len(expected & set(selected)) / len(expected)
            rr = 0.0
            dcg = 0.0
            for rank, item_id in enumerate(selected, start=1):
                if item_id in expected:
                    if rr == 0.0:
                        rr = 1.0 / rank
                    dcg += 1.0 / math.log2(rank + 1)
            ideal = sum(
                1.0 / math.log2(rank + 1)
                for rank in range(1, min(len(expected), k) + 1)
            )
            ndcg.append(min(1.0, dcg / ideal) if ideal else 0.0)
        else:
            recall, rr = 1.0, 1.0
            ndcg.append(1.0)
        recalls.append(recall)
        reciprocal.append(rr)
        details.append({
            "name": case.name, "recall": recall, "rr": rr,
            "ndcg": ndcg[-1],
            "kind": case.kind,
            "latency_ms": round(latency, 2),
            "selected": selected[:5],
            **({"error": result["error"]} if result.get("error") else {}),
        })
    return {
        "n": len(cases),
        "k": k,
        f"recall_at_{k}": round(statistics.fmean(recalls), 4) if recalls else 0.0,
        "mrr": round(statistics.fmean(reciprocal), 4) if reciprocal else 0.0,
        f"ndcg_at_{k}": round(statistics.fmean(ndcg), 4) if ndcg else 0.0,
        "abstention_accuracy": (
            round(abstention_hits / abstention_total, 4)
            if abstention_total else None
        ),
        "abstention_cases": abstention_total,
        "p95_ms": round(_p95(latencies), 2),
        "mean_ms": round(statistics.fmean(latencies), 2) if latencies else 0.0,
        "cases": details,
    }


def gate(baseline: dict, candidate: dict, tolerance: float = 0.02,
         p95_ms: float = 2000.0) -> dict:
    """Promotion gate: no recall/MRR regression beyond tolerance, p95 in budget."""
    recall_key = next(
        (key for key in candidate if key.startswith("recall_at_")), None
    )
    if recall_key is None:
        return {"ok": False, "failures": ["missing_recall_metric"]}
    failures: list[str] = []
    if candidate.get(recall_key, 0.0) + tolerance < baseline.get(recall_key, 0.0):
        failures.append("recall_regression")
    if candidate.get("mrr", 0.0) + tolerance < baseline.get("mrr", 0.0):
        failures.append("mrr_regression")
    latency = float(candidate.get("p95_ms") or 0.0)
    if latency and latency > p95_ms:
        failures.append("latency_budget")
    return {
        "ok": not failures,
        "failures": failures,
        "recall_key": recall_key,
        "baseline": {recall_key: baseline.get(recall_key), "mrr": baseline.get("mrr")},
        "candidate": {recall_key: candidate.get(recall_key), "mrr": candidate.get("mrr"),
                      "p95_ms": latency},
        "tolerance": tolerance,
        "p95_budget": p95_ms,
    }
