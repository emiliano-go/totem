"""engineering_context tool: §48 output ordering, §46 ambiguity blocking."""

from __future__ import annotations

import sqlite3

from .db import get_all_conflicts, list_items
from .hashing import check_staleness
from .models import Conflict, MemoryStatus, MemoryType
from pathlib import Path


def _score_item(item, tags: list[str]) -> float:
    """Score = 0.4*tagMatch + 0.3*importance + 0.2*confidence + 0.1*recency."""
    tag_match = len(set(item.tags) & set(tags)) / max(len(tags), 1)
    recency = 1.0
    score = 0.4 * tag_match + 0.3 * item.importance + 0.2 * item.confidence + 0.1 * recency
    if item.type == MemoryType.INVARIANT:
        score *= 1.25
    if item.status == MemoryStatus.POTENTIALLY_STALE:
        score *= 0.5
    return score


def _serialize_item(item) -> str:
    lines = [f"[{item.type.value.upper()}] {item.title}"]
    lines.append(f"  Statement: {item.statement}")
    if item.details:
        lines.append(f"  Details: {item.details}")
    lines.append(f"  Tags: {', '.join(item.tags)}")
    lines.append(f"  Confidence: {item.confidence} | Importance: {item.importance}")
    lines.append(f"  Status: {item.status.value}")
    if item.metadata:
        for k, v in item.metadata.items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _serialize_conflict(c: Conflict) -> str:
    lines = [
        f"  Conflict: {c.item_a} vs {c.item_b}",
        f"    Claim A: {c.claim_a}",
        f"    Claim B: {c.claim_b}",
        f"    Condition: {c.condition}",
        f"    Options: {', '.join(c.resolution_options)}",
    ]
    if c.recommended:
        lines.append(f"    Recommended: {c.recommended}")
    return "\n".join(lines)
