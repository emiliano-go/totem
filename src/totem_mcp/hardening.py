"""MCP call hardening: bounded output, rate limits, deadlines, manifests.

The tool surface is used by every agent in every session, so one pathological
call must not exhaust the process or flood the client. Every guard here fails
soft with a JSON error rather than killing the server.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

MAX_OUTPUT_BYTES = int(os.environ.get("TOTEM_MCP_MAX_OUTPUT_BYTES", "200000"))
MAX_ARG_STRING = int(os.environ.get("TOTEM_MCP_MAX_ARG_STRING", "100000"))
MAX_ARG_LIST = int(os.environ.get("TOTEM_MCP_MAX_ARG_LIST", "1000"))
RATE_PER_MIN = float(os.environ.get("TOTEM_MCP_RATE_PER_MIN", "600"))
RATE_BURST = float(os.environ.get("TOTEM_MCP_BURST", "100"))
TOOL_TIMEOUT_MS = int(os.environ.get("TOTEM_MCP_TIMEOUT_MS", "30000"))

_LOCK = threading.Lock()
_RATE: dict[str, list[float]] = {}
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="totem-tool")


class ToolTimeout(RuntimeError):
    pass


class ToolRateLimited(RuntimeError):
    pass


def _rate_ok(tool: str) -> bool:
    now = time.monotonic()
    with _LOCK:
        tokens, last = _RATE.get(tool, [RATE_BURST, now])
        tokens = min(RATE_BURST, tokens + (now - last) * (RATE_PER_MIN / 60.0))
        if tokens < 1.0:
            _RATE[tool] = [tokens, now]
            return False
        _RATE[tool] = [tokens - 1.0, now]
        return True


def validate_arguments(args: dict) -> str | None:
    """Bound argument sizes; type/shape validation stays with FastMCP."""
    for name, value in args.items():
        if isinstance(value, str) and len(value) > MAX_ARG_STRING:
            return f"argument {name!r} exceeds {MAX_ARG_STRING} characters"
        if isinstance(value, list):
            if len(value) > MAX_ARG_LIST:
                return f"argument {name!r} has more than {MAX_ARG_LIST} items"
            for item in value[:MAX_ARG_LIST]:
                if isinstance(item, str) and len(item) > MAX_ARG_STRING:
                    return f"argument {name!r} contains an oversized string"
        if isinstance(value, dict) and len(json.dumps(value, default=str)) > MAX_ARG_STRING:
            return f"argument {name!r} is too large"
    return None


def _bounded(text: str) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= MAX_OUTPUT_BYTES:
        return text
    marker = "\n…[truncated]"
    keep = MAX_OUTPUT_BYTES - len(marker.encode("utf-8"))
    preview = raw[:max(keep, 0)].decode("utf-8", "ignore") + marker
    return json.dumps({
        "truncated": True,
        "bytes": len(raw),
        "limit": MAX_OUTPUT_BYTES,
        "preview": preview[:8192],
    })


def _error(tool: str, message: str) -> str:
    return json.dumps({"error": message, "tool": tool})


def harden_tool(fn):
    """Wrap one tool callable with rate, size, deadline, and error guards."""
    name = getattr(fn, "__name__", "tool")
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            bound = signature.bind_partial(*args, **kwargs)
        except TypeError as exc:
            return _error(name, f"invalid arguments: {exc}")
        problem = validate_arguments(dict(bound.arguments))
        if problem:
            return _error(name, problem)
        if not _rate_ok(name):
            return _error(name, "rate limit exceeded; retry shortly")
        future = _POOL.submit(fn, *args, **kwargs)
        try:
            result = future.result(timeout=TOOL_TIMEOUT_MS / 1000.0)
        except FutureTimeout:
            return _error(name, f"timed out after {TOOL_TIMEOUT_MS} ms")
        except Exception as exc:  # a tool bug must not kill the server loop
            return _error(name, f"{type(exc).__name__}: {exc}")
        return _bounded(result if isinstance(result, str) else str(result))

    return wrapper


def tool_manifest(tools) -> dict:
    """Stable digest of the registered tool surface."""
    rows = []
    for name, tool in sorted(tools.items()):
        rows.append({
            "name": name,
            "description": tool.description or "",
            "parameters": tool.parameters,
        })
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {"sha256": digest, "tools": len(rows)}


def harden_registry(tools) -> None:
    """Wrap every registered tool in place (after decorators have run)."""
    for _name, tool in tools.items():
        if not getattr(tool.fn, "_totem_hardened", False):
            tool.fn = harden_tool(tool.fn)
            tool.fn._totem_hardened = True
