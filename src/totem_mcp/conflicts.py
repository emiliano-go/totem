"""Conflict detection for overlapping evidence and conceptual contradictions."""

from __future__ import annotations

import turso

from .db import get_overlapping_items, find_same_title_different_statement, insert_conflict
from .models import Conflict, MemoryItem, MemoryType

# Types that carry claims worth checking for contradictions
CLAIM_TYPES = (
    MemoryType.DECISION,
    MemoryType.INVARIANT,
    MemoryType.ASSUMPTION,
    MemoryType.CONTRACT if hasattr(MemoryType, "CONSTRAINT") else MemoryType.ASSUMPTION,
)


def detect_conflicts(
    conn: turso.Connection,
    new_item: MemoryItem,
) -> list[Conflict]:
    """Detect conflicts on memory_create.

    Two checks:
    1. Evidence overlap: same type + overlapping file range + different statement
    2. Same title: same type + same title + different statement (conceptual contradiction)
    """
    if new_item.type not in (
        MemoryType.DECISION, MemoryType.INVARIANT, MemoryType.ASSUMPTION,
        MemoryType.CONSTRAINT, MemoryType.CONTRACT, MemoryType.ARCHITECTURE,
    ):
        return []

    conflicts: list[Conflict] = []

    # Check 1: Evidence-overlap conflicts
    if new_item.evidence:
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

    # Check 2: Same-title-different-statement (conceptual contradiction)
    same_title = find_same_title_different_statement(
        conn,
        title=new_item.title,
        statement=new_item.statement,
        item_type=new_item.type.value,
        exclude_id=new_item.id,
    )
    for existing_item in same_title:
        # Skip if already caught by evidence-overlap check
        if any(c.item_b == existing_item.id for c in conflicts):
            continue

        conflict = Conflict(
            itemA=new_item.id,
            itemB=existing_item.id,
            claimA=new_item.statement,
            claimB=existing_item.statement,
            condition=(
                f"Two {new_item.type.value} items share the title "
                f"'{new_item.title}' but have different statements"
            ),
            resolutionOptions=[
                f"Keep new: {new_item.title}",
                f"Keep existing: {existing_item.title}",
                "Merge or reconcile statements",
            ],
            recommended="Re-verify against current source to determine which statement is correct",
        )
        conflicts.append(conflict)
        insert_conflict(conn, conflict)

    return conflicts
