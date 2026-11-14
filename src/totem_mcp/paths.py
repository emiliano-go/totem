"""Path containment for evidence.

A memory's ``evidence.path`` is caller-supplied and persisted, so it is
untrusted on every later read.  A bare ``startswith`` check is wrong twice
over: a sibling directory can share the root's prefix, and ``abspath`` does
not resolve symlinks.  Resolve both sides with ``realpath`` and compare with
``commonpath`` instead.
"""

from __future__ import annotations

import os
from pathlib import Path


class EvidencePathError(PermissionError):
    """An evidence path escapes the project root."""


def project_root(conn) -> Path | None:
    """Best-effort project root for a connection.

    ``connect()`` stores the DB path on the connection.  A project DB lives at
    ``<root>/.totem/totem.db``; an ad-hoc DB (tests, exports) is treated as
    rooted at its own directory.
    """
    raw = getattr(conn, "_totem_path", None)
    if not raw:
        return None
    path = Path(raw).resolve()
    if path.parent.name == ".totem":
        return path.parent.parent
    return path.parent


def resolve_within(root: str | Path, path: str | Path) -> Path:
    """Absolute real path for *path* under *root*, or ``EvidencePathError``."""
    root_real = os.path.realpath(os.path.abspath(str(root)))
    candidate = str(path)
    if not os.path.isabs(candidate):
        candidate = os.path.join(root_real, candidate)
    target = os.path.realpath(candidate)
    try:
        within = os.path.commonpath([root_real, target]) == root_real
    except ValueError:  # different drives (Windows)
        within = False
    if not within:
        raise EvidencePathError(f"path escapes project root: {path!r}")
    return Path(target)


def contained_path(path: str | Path, root: str | Path | None) -> Path:
    """Validate an evidence path, returning the path to actually read.

    With no known root (a direct call in a test, an imported DB) the path is
    accepted but still cannot contain ``..`` or NUL bytes.  With a root, the
    resolved target must stay inside it.
    """
    text = str(path)
    if not text or "\0" in text:
        raise EvidencePathError("empty or NUL-containing evidence path")
    parts = Path(text.replace("\\", "/")).parts
    if ".." in parts:
        raise EvidencePathError(f"evidence path may not traverse: {text!r}")
    if root is None:
        return Path(text)
    return resolve_within(root, text)
