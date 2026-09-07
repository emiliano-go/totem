"""Pydantic models for srcmem memory items."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MemoryType(str, Enum):
    DECISION = "decision"
    INVARIANT = "invariant"
    GOTCHA = "gotcha"
    REJECTED_IDEA = "rejected_idea"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    POTENTIALLY_STALE = "potentially_stale"
    INVALIDATED = "invalidated"
    DELETED = "deleted"


class Evidence(BaseModel):
    path: str
    start_line: int = Field(alias="startLine")
    end_line: int = Field(alias="endLine")
    content_hash: str = Field(alias="contentHash")
    commit: str | None = None
    captured_at: str = Field(alias="capturedAt")

    model_config = {"populate_by_name": True}


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: MemoryType
    title: str
    statement: str
    details: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: MemoryStatus = MemoryStatus.ACTIVE
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    importance: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence: list[Evidence] = Field(default_factory=list)
    related_memory_ids: list[str] = Field(
        default_factory=list, alias="relatedMemoryIds"
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        alias="createdAt",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        alias="updatedAt",
    )
    verified_at: str | None = Field(default=None, alias="verifiedAt")
    metadata: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate_type_specific_metadata(self) -> MemoryItem:
        meta = self.metadata or {}
        if self.type == MemoryType.INVARIANT:
            if "verificationMethod" not in meta:
                msg = "Invariant items require 'verificationMethod' in metadata (§41)"
                raise ValueError(msg)
            if "condition" not in meta:
                msg = "Invariant items require 'condition' in metadata (§41)"
                raise ValueError(msg)
        elif self.type == MemoryType.DECISION:
            if "rationale" not in meta:
                msg = "Decision items require 'rationale' in metadata (§41)"
                raise ValueError(msg)
        elif self.type == MemoryType.REJECTED_IDEA:
            if "proposal" not in meta:
                msg = "Rejected idea items require 'proposal' in metadata (§41)"
                raise ValueError(msg)
            if "reasonRejected" not in meta:
                msg = "Rejected idea items require 'reasonRejected' in metadata (§41)"
                raise ValueError(msg)
        return self


class DecisionData(BaseModel):
    alternatives: list[str] | None = None
    rationale: str
