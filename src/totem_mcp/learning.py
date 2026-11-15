"""Fit advisory retrieval models from feedback.

The reranker and conformal calibrator stay uncommitted until feedback has both
positive and negative examples.  This module turns ``memory_feedback`` rows
into labelled feature vectors, fits the linear reranker, and stores the
weights in ``meta`` together with the commit and example count that earned
them.  A later training run replaces them; nothing here changes a verification
verdict.
"""

from __future__ import annotations

import json

from .calibration import fit_calibrator, rule_trust, save_calibrator
from .db import (
    get_all_items,
    get_head_commit,
    get_item,
    get_meta,
    list_feedback,
    set_meta,
)
from .paths import project_root
from .ranking import MEMORY_FEATURES, MemoryReranker
from .retrieval import Candidate, build_candidates

RERANKER_KEY = "reranker:model"
POSITIVE_SIGNALS = {"useful", "verified"}
NEGATIVE_SIGNALS = {"wrong", "irrelevant", "stale"}


def load_reranker(conn) -> MemoryReranker | None:
    raw = get_meta(conn, RERANKER_KEY)
    if not raw:
        return None
    try:
        model = json.loads(raw)
    except ValueError:
        return None
    return MemoryReranker.from_dict(model)


def _examples(conn) -> tuple[list[tuple[list[float], float]], dict]:
    rows = list_feedback(conn, limit=5000)
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row.get("query") or "", tuple(row.get("tags") or ()))
        groups.setdefault(key, []).append(row)

    root = project_root(conn)
    examples: list[tuple[list[float], float]] = []
    positives = negatives = 0
    for (query, tags), group in groups.items():
        candidates, _meta = build_candidates(
            conn, query, list(tags), None,
            include_stale=True, include_expired=True,
        )
        by_id = {candidate.item.id: candidate for candidate in candidates}
        for row in group:
            candidate = by_id.get(row["itemId"])
            if candidate is None:
                item = get_item(conn, row["itemId"])
                if item is None:
                    continue
                candidate = Candidate(item=item)
            trust = rule_trust(candidate.item, root=root)
            features = MemoryReranker.features(candidate, trust)
            if row["signal"] in POSITIVE_SIGNALS:
                label = 1.0
                positives += 1
            elif row["signal"] in NEGATIVE_SIGNALS:
                label = 0.0
                negatives += 1
            else:
                continue
            weight = max(1, min(5, int(round(float(row.get("weight") or 1.0)))))
            for _ in range(weight):
                examples.append((features, label))
    return examples, {"positives": positives, "negatives": negatives}


def train_models(conn, min_examples: int = 24) -> dict:
    """Fit the reranker and calibrator when feedback supports both classes."""
    examples, counts = _examples(conn)
    result = {
        "examples": len(examples),
        "positives": counts["positives"],
        "negatives": counts["negatives"],
        "trained": False,
        "reason": None,
        "features": list(MEMORY_FEATURES),
    }
    if len(examples) < min_examples:
        result["reason"] = "insufficient_feedback"
        return result
    labels = {label for _features, label in examples}
    if len(labels) < 2:
        result["reason"] = "needs_both_classes"
        return result

    reranker = MemoryReranker().train(examples)
    payload = {
        **reranker.to_dict(),
        "commit": get_head_commit(),
        "examples": len(examples),
        "positives": counts["positives"],
        "negatives": counts["negatives"],
    }
    set_meta(conn, RERANKER_KEY, json.dumps(payload, sort_keys=True))

    calibrator = fit_calibrator(get_all_items(conn), root=project_root(conn))
    if calibrator is not None:
        save_calibrator(conn, calibrator)

    result.update({
        "trained": True,
        "reranker": reranker.version,
        "weights": reranker.weights,
        "calibrator": calibrator.mode if calibrator is not None else None,
    })
    return result
