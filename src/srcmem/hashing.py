"""SHA256 content hashing for staleness detection."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_range(path: Path, start_line: int, end_line: int) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return None
    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        return None
    return "".join(lines[start_line - 1 : end_line])
