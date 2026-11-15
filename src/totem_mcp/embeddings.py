"""Optional static embedding backend for memory retrieval.

The default dense leg is a dependency-free signed-hash TF-IDF encoder.  When
the optional ``model2vec`` package is installed, Totem can instead use
``minishlab/potion-retrieval-32M``: a 32M static retrieval model that runs on
CPU with numpy only, no torch.  It is opt-in because the model is downloaded
from Hugging Face on first use and adds a dependency.

Configuration:

* ``TOTEM_EMBEDDING_BACKEND``: ``auto`` (default), ``hash``, or ``model2vec``.
  ``auto`` uses Model2Vec when installed and falls back to the hash encoder.
* ``TOTEM_EMBEDDING_MODEL``: Hugging Face id or local directory; defaults to
  ``minishlab/potion-retrieval-32M``.
* ``TOTEM_EMBEDDING_LOCAL``: optional local model directory. When set it wins
  over the model id, so an air-gapped install can point at a pre-pulled copy.

The configured model is loaded once per process and cached.  Any load failure
is reported by :func:`status` and the hash encoder is used instead; retrieval
must never fail because the optional model is missing.
"""

from __future__ import annotations

import importlib.util
import os
import threading

DEFAULT_MODEL = "minishlab/potion-retrieval-32M"
DEFAULT_BACKEND = "auto"
_BACKENDS = ("auto", "hash", "model2vec")


def configured_backend() -> str:
    """Requested backend; unknown values fall back to ``auto``."""
    value = os.environ.get("TOTEM_EMBEDDING_BACKEND", DEFAULT_BACKEND)
    value = value.strip().lower()
    return value if value in _BACKENDS else DEFAULT_BACKEND


def configured_model() -> str:
    value = os.environ.get("TOTEM_EMBEDDING_MODEL", "").strip()
    return value or DEFAULT_MODEL


def configured_local() -> str:
    return os.environ.get("TOTEM_EMBEDDING_LOCAL", "").strip()


def available() -> bool:
    """True when the optional ``model2vec`` package can be imported."""
    try:
        return importlib.util.find_spec("model2vec") is not None
    except (ImportError, ValueError):
        return False


def _normalise(vector) -> list[float]:
    values = [float(value) for value in vector]
    norm = sum(value * value for value in values) ** 0.5
    return [value / norm for value in values] if norm else values


class StaticEncoder:
    """Thin adapter over a Model2Vec ``StaticModel``."""

    kind = "model2vec"

    def __init__(self, model_id: str | None = None,
                 local_path: str | None = None, model=None) -> None:
        if model is None:
            from model2vec import StaticModel  # optional dependency

            source = local_path or model_id or DEFAULT_MODEL
            model = StaticModel.from_pretrained(source)
        self.model = model
        self.model_id = model_id or DEFAULT_MODEL
        self.local_path = local_path or ""
        self.dim = int(getattr(model, "dim", 0) or 0)

    def encode(self, text: str) -> list[float]:
        vector = self.model.encode([text])[0]
        return _normalise(vector)

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        matrix = self.model.encode(list(texts))
        return [_normalise(row) for row in matrix]


_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str, str], StaticEncoder] = {}
_FAILURES: dict[tuple[str, str, str], str] = {}


def _key() -> tuple[str, str, str]:
    return (configured_backend(), configured_model(), configured_local())


def get_static_encoder() -> StaticEncoder | None:
    """The configured static encoder, or None when the hash leg should run."""
    backend = configured_backend()
    if backend == "hash":
        return None
    if backend == "auto" and not available():
        return None
    key = _key()
    with _LOCK:
        encoder = _CACHE.get(key)
        if encoder is not None:
            return encoder
        try:
            encoder = StaticEncoder(
                model_id=configured_model(),
                local_path=configured_local() or None,
            )
        except Exception as exc:  # a missing model must not break retrieval
            _FAILURES[key] = f"{type(exc).__name__}: {exc}"
            return None
        _CACHE[key] = encoder
        _FAILURES.pop(key, None)
        return encoder


def status() -> dict:
    """Describe the configured backend without triggering a model load."""
    backend = configured_backend()
    key = _key()
    encoder = _CACHE.get(key)
    error = _FAILURES.get(key)
    if backend == "hash":
        active = "hash"
    elif encoder is not None:
        active = "model2vec"
    elif error or not available():
        active = "hash"
    else:
        # Configured and installable, not loaded yet: this is what the next
        # retrieval call will use.
        active = "model2vec"
    return {
        "backend": backend,
        "active": active,
        "model": configured_model(),
        "local": configured_local(),
        "available": available(),
        "loaded": encoder is not None,
        "dim": encoder.dim if encoder is not None else 0,
        "error": error,
    }


def reset() -> None:
    """Drop the cached model (tests and explicit reconfiguration)."""
    with _LOCK:
        _CACHE.clear()
        _FAILURES.clear()
