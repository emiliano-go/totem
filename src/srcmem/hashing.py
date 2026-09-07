"""SHA256 content hashing for staleness detection."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
