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
