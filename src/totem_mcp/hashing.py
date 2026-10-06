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


def read_file_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return None


def check_staleness(
    path: Path,
    start_line: int,
    end_line: int,
    stored_hash: str,
    symbol: str | None = None,
    blob_hash: str | None = None,
) -> bool:
    """Return True if evidence is stale.

    Prefers whole-file blob equality (the code moved but is unchanged), then
    symbol presence, then the line-range hash.
    """
    text = read_file_text(path)
    if text is None:
        return True
    if symbol:
        return symbol not in text  # moved code is not stale if the symbol remains
    if blob_hash:
        return hash_content(text) != blob_hash
    content = read_range(path, start_line, end_line)
    if content is None:
        return True
    return hash_content(content) != stored_hash
