"""Conflict detection for overlapping evidence with differing statements (§45)."""

from __future__ import annotations

import turso

from .db import get_overlapping_items, insert_conflict
from .models import Conflict, MemoryItem, MemoryType


def detect_conflicts(
    conn: turso.Connection,
    new_item: MemoryItem,
) -> list[Conflict]:
    """Check if a new decision/invariant conflicts with existing items (§45).

    On memory_create for decision or invariant: check active items of the same
    type with overlapping evidence. If statements differ, produce the full
    contradiction structure per §6.
    """
    if new_item.type not in (MemoryType.DECISION, MemoryType.INVARIANT):
        return []

    if not new_item.evidence:
        return []

    conflicts: list[Conflict] = []
    for ev in new_item.evidence:
        existing = get_overlapping_items(
            conn,
            new_item.type.value,
            ev.path,
            ev.start_line,
            ev.end_line,
        )
        for existing_item in existing:
            if existing_item.id == new_item.id:
                continue
            if existing_item.statement.strip() == new_item.statement.strip():
                continue

            conflict = Conflict(
                itemA=new_item.id,
                itemB=existing_item.id,
                claimA=new_item.statement,
                claimB=existing_item.statement,
                condition=(
                    f"Both {new_item.type.value} items have overlapping evidence "
                    f"in {ev.path}:{ev.start_line}-{ev.end_line}"
                ),
                resolutionOptions=[
                    f"Keep new: {new_item.title}",
                    f"Keep existing: {existing_item.title}",
                    "Merge statements",
                ],
                recommended=(
                    f"Keep existing ({existing_item.title}) if backwards "
                    f"compatibility is the priority"
                ),
            )
            conflicts.append(conflict)
            insert_conflict(conn, conflict)

    return conflicts
