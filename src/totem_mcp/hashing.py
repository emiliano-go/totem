"""SHA256 content hashing and semantic symbol hashing for staleness detection."""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

# A definition line in the languages totem tends to see. Language-light on purpose.
_SYMBOL_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?(?:def|class|function|func|fn|sub|method)\b"
)
_COMMENT_PREFIXES = ("#", "//", "*", "/*")


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


def normalize_body(text: str) -> str:
    """Formatting-insensitive normalization.

    Drops blank lines and whole-line comments and collapses runs of whitespace,
    so moving or reformatting code does not count as a change.
    """
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(_COMMENT_PREFIXES):
            continue
        out.append(re.sub(r"\s+", " ", stripped))
    return "\n".join(out)


def _python_symbol_body(text: str, symbol: str) -> str | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
            node.name == symbol
        ):
            segment = ast.get_source_segment(text, node)
            if segment is not None:
                return segment
    return None


def _capture_block(lines: list[str], start: int) -> str:
    """Capture a definition's block: brace-matched, else indentation-matched."""
    base = lines[start]
    if "{" in base:
        depth = base.count("{") - base.count("}")
        out = [base]
        i = start + 1
        while i < len(lines) and depth > 0:
            depth += lines[i].count("{") - lines[i].count("}")
            out.append(lines[i])
            i += 1
        return "\n".join(out)
    indent = len(base) - len(base.lstrip())
    out = [base]
    i = start + 1
    while i < len(lines):
        line = lines[i]
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        out.append(line)
        i += 1
    return "\n".join(out)


def _generic_symbol_body(text: str, symbol: str) -> str | None:
    lines = text.splitlines()
    word = re.compile(r"\b" + re.escape(symbol) + r"\b")
    for i, line in enumerate(lines):
        if _SYMBOL_DEF_RE.match(line) and word.search(line):
            return _capture_block(lines, i)
    return None


def resolve_symbol_body(path: Path, symbol: str, text: str | None = None) -> str | None:
    """Source body of ``symbol`` (Python AST first, generic fallback), or None."""
    if not symbol:
        return None
    if text is None:
        text = read_file_text(path)
    if text is None:
        return None
    return _python_symbol_body(text, symbol) or _generic_symbol_body(text, symbol)


def hash_symbol(path: Path, symbol: str, text: str | None = None) -> str | None:
    """Normalized body hash for a symbol, or None if it cannot be resolved."""
    body = resolve_symbol_body(path, symbol, text=text)
    if body is None:
        return None
    return hash_content(normalize_body(body))


def check_staleness(
    path: Path,
    start_line: int,
    end_line: int,
    stored_hash: str,
    symbol: str | None = None,
    blob_hash: str | None = None,
    symbol_hash: str | None = None,
) -> bool:
    """Return True if evidence is stale.

    Symbol evidence is checked by semantic body hash (identity + content): a
    moved-but-unchanged symbol is fresh, a materially changed body is stale, an
    absent symbol is stale. Rows without a stored ``symbol_hash`` (legacy) keep
    the old presence-only behaviour. Then whole-file blob equality, then the
    line-range hash.
    """
    text = read_file_text(path)
    if text is None:
        return True
    if symbol:
        body_hash = hash_symbol(path, symbol, text=text)
        if body_hash is None:
            return True  # symbol no longer resolvable in the file
        if symbol_hash:
            return body_hash != symbol_hash
        return False  # legacy: presence-only
    if blob_hash:
        return hash_content(text) != blob_hash
    content = read_range(path, start_line, end_line)
    if content is None:
        return True
    return hash_content(content) != stored_hash


def verification_fresh(item) -> bool:
    """True only while a verification's justification still holds.

    Truth-maintenance idea: evidence is the premise, the verification the
    derived belief. If any evidence is stale, the verification is no longer
    valid and should be retracted.
    """
    if not getattr(item, "verified_at", None):
        return False
    for ev in getattr(item, "evidence", None) or []:
        if check_staleness(
            Path(ev.path),
            ev.start_line,
            ev.end_line,
            ev.content_hash,
            symbol=ev.symbol,
            blob_hash=ev.blob_hash,
            symbol_hash=ev.symbol_hash,
        ):
            return False
    return True
