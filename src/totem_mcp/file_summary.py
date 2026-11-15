"""Deterministic, dependency-free file summaries.

The summary describes the state of a file after a change, not the action that
changed it. It is intentionally cheap: no model call, no network, and no
language server. A content hash beside the summary is the freshness key, so a
later read can reuse the summary when the bytes have not changed.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_SUMMARY_CHARS = 600
MAX_SYMBOL_CHARS = 240
MAX_SYMBOLS = 24


@dataclass
class FileSymbol:
    name: str
    kind: str
    signature: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name[:200],
            "kind": self.kind,
            "signature": self.signature[:MAX_SYMBOL_CHARS],
            **({"detail": self.detail[:MAX_SYMBOL_CHARS]} if self.detail else {}),
        }


@dataclass
class FileSummary:
    path: str
    language: str
    tldr: str
    symbols: list[FileSymbol] = field(default_factory=list)
    line_count: int = 0
    byte_count: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "language": self.language,
            "tldr": self.tldr[:MAX_SUMMARY_CHARS],
            "symbols": [symbol.to_dict() for symbol in self.symbols[:MAX_SYMBOLS]],
            "lineCount": self.line_count,
            "byteCount": self.byte_count,
        }


_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".md": "markdown",
    ".mdx": "markdown",
    ".json": "json",
    ".jsonc": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".css": "css",
    ".html": "html",
    ".sql": "sql",
    ".rs": "rust",
    ".go": "go",
}


def _language(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return _LANGUAGES.get(suffix, "text")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _first_sentence(text: str) -> str:
    clean = _clean(text)
    if not clean:
        return ""
    match = re.search(r"(?<=[.!?])\s", clean)
    return clean[: match.start()] if match else clean


def _python(text: str) -> tuple[str, list[FileSymbol]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return _fallback(text), []
    symbols: list[FileSymbol] = []
    functions = classes = constants = 0
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions += 1
            args = ast.unparse(node.args) if node.args else ""
            signature = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}{node.name}({args})"
            if node.returns is not None:
                signature += f" -> {ast.unparse(node.returns)}"
            symbols.append(FileSymbol(
                name=node.name, kind="function", signature=signature,
                detail=_first_sentence(ast.get_docstring(node) or ""),
            ))
        elif isinstance(node, ast.ClassDef):
            classes += 1
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            symbols.append(FileSymbol(
                name=node.name, kind="class",
                signature=f"class {node.name}({bases})" if bases else f"class {node.name}",
                detail=_first_sentence(ast.get_docstring(node) or ""),
            ))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    constants += 1
                    if len(symbols) < MAX_SYMBOLS:
                        symbols.append(FileSymbol(
                            name=target.id, kind="constant",
                            signature=target.id,
                        ))
    doc = _first_sentence(ast.get_docstring(tree) or "")
    parts = []
    if doc:
        parts.append(doc)
    inventory = []
    if functions:
        names = ", ".join(s.name for s in symbols if s.kind == "function")[:160]
        inventory.append(f"{functions} function{'s' if functions != 1 else ''} ({names})")
    if classes:
        names = ", ".join(s.name for s in symbols if s.kind == "class")[:120]
        inventory.append(f"{classes} class{'es' if classes != 1 else ''} ({names})")
    if constants:
        inventory.append(f"{constants} constant{'s' if constants != 1 else ''}")
    if inventory:
        parts.append("Python module with " + ", ".join(inventory) + ".")
    if not parts:
        parts.append(f"Python module with {len(text.splitlines())} lines.")
    return " ".join(parts), symbols


def _markdown(text: str) -> tuple[str, list[FileSymbol]]:
    body = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, flags=re.DOTALL)
    headings = re.findall(r"^(#{1,3})\s+(.+?)\s*$", body, flags=re.MULTILINE)
    symbols = [
        FileSymbol(name=title.strip(), kind="heading", signature=level)
        for level, title in headings[:MAX_SYMBOLS]
    ]
    title = next((title.strip() for level, title in headings if len(level) == 1), "")
    paragraph = ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "```", "|", "-", "*", ">", "[")):
            continue
        paragraph = stripped
        break
    pieces = []
    if title:
        pieces.append(f"Markdown document '{title}'")
    elif paragraph:
        pieces.append("Markdown document")
    if headings:
        sections = ", ".join(title.strip() for _level, title in headings[1:7])
        if sections:
            pieces.append(f"covering {sections}")
    summary = ", ".join(pieces) if pieces else "Markdown document"
    if paragraph:
        summary += f". {_first_sentence(paragraph)}"
    return summary, symbols


def _json_summary(text: str) -> tuple[str, list[FileSymbol]]:
    try:
        value = json.loads(text)
    except ValueError:
        return _fallback(text), []
    if isinstance(value, dict):
        keys = list(value.keys())
        symbols = [FileSymbol(name=str(key), kind="key") for key in keys[:MAX_SYMBOLS]]
        preview = ", ".join(str(key) for key in keys[:8])
        return f"JSON object with {len(keys)} key{'s' if len(keys) != 1 else ''}: {preview}.", symbols
    if isinstance(value, list):
        return f"JSON array with {len(value)} item{'s' if len(value) != 1 else ''}.", []
    return f"JSON {type(value).__name__}.", []


def _key_value(text: str, language: str) -> tuple[str, list[FileSymbol]]:
    keys = []
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z_][\w.-]*)\s*[:=]", line)
        if match and match.group(1) not in keys:
            keys.append(match.group(1))
    symbols = [FileSymbol(name=key, kind="key") for key in keys[:MAX_SYMBOLS]]
    if not keys:
        return _fallback(text), []
    preview = ", ".join(keys[:10])
    return f"{language.upper()} configuration with {len(keys)} top-level keys: {preview}.", symbols


_JS_PATTERNS = (
    ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.MULTILINE)),
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)", re.MULTILINE)),
    ("export", re.compile(r"^\s*export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)),
    ("export", re.compile(r"^\s*export\s+(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)),
)


def _javascript(text: str, language: str) -> tuple[str, list[FileSymbol]]:
    symbols = []
    seen = set()
    for kind, pattern in _JS_PATTERNS:
        for name in pattern.findall(text):
            if name not in seen:
                seen.add(name)
                symbols.append(FileSymbol(name=name, kind=kind))
    imports = len(re.findall(r"^\s*import\s", text, flags=re.MULTILINE))
    if symbols:
        names = ", ".join(symbol.name for symbol in symbols[:12])
        return (
            f"{language.title()} module exporting {names}"
            + (f"; {imports} imports." if imports else "."),
            symbols,
        )
    return _fallback(text), []


def _shell(text: str) -> tuple[str, list[FileSymbol]]:
    names = []
    for match in re.finditer(
        r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{", text, flags=re.MULTILINE
    ):
        if match.group(1) not in names:
            names.append(match.group(1))
    symbols = [FileSymbol(name=name, kind="function") for name in names[:MAX_SYMBOLS]]
    if names:
        return f"Shell script defining {', '.join(names[:10])}.", symbols
    return _fallback(text), []


def _fallback(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "Empty file."
    return f"{len(lines)} non-empty lines; starts with: {lines[0][:200]}"


def summarize_file(path: str, text: str) -> FileSummary:
    """Summarize one file's current state without any model call."""
    language = _language(path)
    if language == "python":
        tldr, symbols = _python(text)
    elif language == "markdown":
        tldr, symbols = _markdown(text)
    elif language == "json":
        tldr, symbols = _json_summary(text)
    elif language in ("yaml", "toml"):
        tldr, symbols = _key_value(text, language)
    elif language in ("javascript", "typescript"):
        tldr, symbols = _javascript(text, language)
    elif language == "shell":
        tldr, symbols = _shell(text)
    else:
        tldr, symbols = _fallback(text), []
    return FileSummary(
        path=path.replace("\\", "/"),
        language=language,
        tldr=_clean(tldr)[:MAX_SUMMARY_CHARS],
        symbols=symbols[:MAX_SYMBOLS],
        line_count=text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        byte_count=len(text.encode("utf-8")),
    )
