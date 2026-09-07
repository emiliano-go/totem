"""Pydantic models for totem memory items."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 3


class MemoryType(str, Enum):
    DECISION = "decision"
    INVARIANT = "invariant"
    GOTCHA = "gotcha"
    REJECTED_IDEA = "rejected_idea"
    ASSUMPTION = "assumption"
    OPEN_QUESTION = "open_question"
    AMBIGUITY = "ambiguity"
    CONTRACT = "contract"
    CONSTRAINT = "constraint"
    HYPOTHESIS = "hypothesis"
    OBSERVATION = "observation"
    BUG = "bug"
    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    POTENTIALLY_STALE = "potentially_stale"
    INVALIDATED = "invalidated"
    DELETED = "deleted"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"


class EvidenceKind(str, Enum):
    SOURCE = "source"
    TEST = "test"
    DOC = "doc"
    CONFIG = "config"
    GIT = "git"
    USER = "user"
    RUNTIME = "runtime"
    AGENT = "agent"


class Evidence(BaseModel):
    path: str
    start_line: int = Field(alias="startLine")
    end_line: int = Field(alias="endLine")
    content_hash: str = Field(alias="contentHash")
    kind: EvidenceKind = EvidenceKind.SOURCE
    commit: str | None = None
    captured_at: str = Field(alias="capturedAt")

    model_config = {"populate_by_name": True}


# Bug state machine: open → confirmed → fixed → verified
_BUG_TRANSITIONS = {
    "open": {"confirmed"},
    "confirmed": {"fixed"},
    "fixed": {"verified"},
    "verified": set(),
}


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
    scope: str | None = None
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
    verified_commit: str | None = Field(default=None, alias="verifiedCommit")
    schema_version: int = Field(default=SCHEMA_VERSION, alias="schemaVersion")
    metadata: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate_type_specific_metadata(self) -> MemoryItem:
        meta = self.metadata or {}
        if self.type == MemoryType.INVARIANT:
            if "verificationMethod" not in meta:
                msg = "Invariant items require 'verificationMethod' in metadata"
                raise ValueError(msg)
            if "condition" not in meta:
                msg = "Invariant items require 'condition' in metadata"
                raise ValueError(msg)
        elif self.type == MemoryType.DECISION:
            if "rationale" not in meta:
                meta["rationale"] = "see statement"
        elif self.type == MemoryType.REJECTED_IDEA:
            if "proposal" not in meta:
                msg = "Rejected idea items require 'proposal' in metadata"
                raise ValueError(msg)
            if "reasonRejected" not in meta:
                msg = "Rejected idea items require 'reasonRejected' in metadata"
                raise ValueError(msg)
        elif self.type == MemoryType.ASSUMPTION:
            if "claimCategory" not in meta:
                msg = "Assumption items require 'claimCategory' in metadata (fact/assumption/hypothesis/guarantee)"
                raise ValueError(msg)
            if meta["claimCategory"] not in ("fact", "assumption", "hypothesis", "guarantee"):
                msg = "claimCategory must be one of: fact, assumption, hypothesis, guarantee"
                raise ValueError(msg)
            if "basis" not in meta:
                msg = "Assumption items require 'basis' in metadata (what establishes this claim)"
                raise ValueError(msg)
        elif self.type == MemoryType.OPEN_QUESTION:
            if "question" not in meta:
                msg = "Open question items require 'question' in metadata"
                raise ValueError(msg)
            if "impact" not in meta:
                msg = "Open question items require 'impact' in metadata (low/medium/high/critical)"
                raise ValueError(msg)
            if meta["impact"] not in ("low", "medium", "high", "critical"):
                msg = "impact must be one of: low, medium, high, critical"
                raise ValueError(msg)
            if "blocking" not in meta:
                msg = "Open question items require 'blocking' in metadata (boolean)"
                raise ValueError(msg)
        elif self.type == MemoryType.AMBIGUITY:
            if "question" not in meta:
                msg = "Ambiguity items require 'question' in metadata"
                raise ValueError(msg)
            if "interpretations" not in meta:
                msg = "Ambiguity items require 'interpretations' in metadata (list of {id, description})"
                raise ValueError(msg)
            if "impact" not in meta:
                msg = "Ambiguity items require 'impact' in metadata (low/medium/high/critical)"
                raise ValueError(msg)
            if meta["impact"] not in ("low", "medium", "high", "critical"):
                msg = "impact must be one of: low, medium, high, critical"
                raise ValueError(msg)
        elif self.type == MemoryType.CONTRACT:
            if "subject" not in meta:
                msg = "Contract items require 'subject' in metadata"
                raise ValueError(msg)
        elif self.type == MemoryType.CONSTRAINT:
            if "constraint" not in meta:
                msg = "Constraint items require 'constraint' in metadata"
                raise ValueError(msg)
        elif self.type == MemoryType.HYPOTHESIS:
            if "hypothesis" not in meta:
                msg = "Hypothesis items require 'hypothesis' in metadata"
                raise ValueError(msg)
        elif self.type == MemoryType.OBSERVATION:
            if "observation" not in meta:
                msg = "Observation items require 'observation' in metadata"
                raise ValueError(msg)
        elif self.type == MemoryType.BUG:
            if "symptom" not in meta:
                msg = "Bug items require 'symptom' in metadata"
                raise ValueError(msg)
            if "severity" not in meta:
                msg = "Bug items require 'severity' in metadata (low/medium/high/critical)"
                raise ValueError(msg)
            if meta["severity"] not in ("low", "medium", "high", "critical"):
                msg = "severity must be one of: low, medium, high, critical"
                raise ValueError(msg)
            if "state" not in meta:
                msg = "Bug items require 'state' in metadata (open/confirmed/fixed/verified)"
                raise ValueError(msg)
            if meta["state"] not in ("open", "confirmed", "fixed", "verified"):
                msg = "state must be one of: open, confirmed, fixed, verified"
                raise ValueError(msg)
        elif self.type == MemoryType.ARCHITECTURE:
            if "component" not in meta:
                msg = "Architecture items require 'component' in metadata"
                raise ValueError(msg)
            if "responsibility" not in meta:
                msg = "Architecture items require 'responsibility' in metadata"
                raise ValueError(msg)
        elif self.type == MemoryType.IMPLEMENTATION:
            if "subject" not in meta:
                msg = "Implementation items require 'subject' in metadata"
                raise ValueError(msg)
            if "kind" not in meta:
                msg = "Implementation items require 'kind' in metadata (api/function/module/type/config/schema)"
                raise ValueError(msg)
            if meta["kind"] not in ("api", "function", "module", "type", "config", "schema"):
                msg = "kind must be one of: api, function, module, type, config, schema"
                raise ValueError(msg)
            if "path" not in meta:
                msg = "Implementation items require 'path' in metadata"
                raise ValueError(msg)
        return self


class Conflict(BaseModel):
    item_a: str = Field(alias="itemA")
    item_b: str = Field(alias="itemB")
    claim_a: str = Field(alias="claimA")
    claim_b: str = Field(alias="claimB")
    condition: str
    resolution_options: list[str] = Field(alias="resolutionOptions")
    recommended: str | None = None
    resolved_at: str | None = Field(default=None, alias="resolvedAt")
    resolution: str | None = None

    model_config = {"populate_by_name": True}
