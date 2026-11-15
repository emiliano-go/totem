"""Optional static embedding backend and its hash fallback."""

from __future__ import annotations

import pytest

from totem_mcp import embeddings
from totem_mcp.ranking import cosine, semantic_vectors


@pytest.fixture(autouse=True)
def _reset_embeddings(monkeypatch):
    embeddings.reset()
    monkeypatch.delenv("TOTEM_EMBEDDING_BACKEND", raising=False)
    monkeypatch.delenv("TOTEM_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("TOTEM_EMBEDDING_LOCAL", raising=False)
    yield
    embeddings.reset()


class FakeStatic:
    dim = 4

    def __init__(self, model_id=None, local_path=None, model=None):
        self.model_id = model_id
        self.local_path = local_path

    def encode(self, text: str):
        return [1.0, 0.0, 0.0, 0.0]

    def encode_many(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def test_hash_backend_never_loads_a_model(monkeypatch):
    monkeypatch.setenv("TOTEM_EMBEDDING_BACKEND", "hash")

    assert embeddings.get_static_encoder() is None
    status = embeddings.status()
    assert status["backend"] == "hash"
    assert status["active"] == "hash"
    assert status["loaded"] is False


def test_model2vec_backend_loads_and_reports(monkeypatch):
    monkeypatch.setenv("TOTEM_EMBEDDING_BACKEND", "model2vec")
    monkeypatch.setenv("TOTEM_EMBEDDING_MODEL", "minishlab/potion-retrieval-32M")
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "StaticEncoder", FakeStatic)

    encoder = embeddings.get_static_encoder()
    assert encoder is not None
    assert encoder.dim == 4
    status = embeddings.status()
    assert status["active"] == "model2vec"
    assert status["loaded"] is True
    assert status["dim"] == 4
    assert status["error"] is None


def test_load_failure_falls_back_to_hash(monkeypatch):
    monkeypatch.setenv("TOTEM_EMBEDDING_BACKEND", "model2vec")
    monkeypatch.setattr(embeddings, "available", lambda: True)

    class Broken:
        def __init__(self, *args, **kwargs):
            raise OSError("model missing")

    monkeypatch.setattr(embeddings, "StaticEncoder", Broken)

    assert embeddings.get_static_encoder() is None
    status = embeddings.status()
    assert status["active"] == "hash"
    assert "model missing" in (status["error"] or "")


def test_semantic_vectors_uses_static_encoder(monkeypatch):
    monkeypatch.setenv("TOTEM_EMBEDDING_BACKEND", "model2vec")
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "StaticEncoder", FakeStatic)

    vectors, query_vector, backend = semantic_vectors(
        ["alpha memory", "beta memory"], "alpha query"
    )
    assert backend == "model2vec"
    assert len(vectors) == 2
    assert abs(cosine(query_vector, vectors[0]) - 1.0) < 1e-9


def test_semantic_vectors_hash_fallback(monkeypatch):
    monkeypatch.setenv("TOTEM_EMBEDDING_BACKEND", "hash")
    vectors, query_vector, backend = semantic_vectors(
        ["alpha memory", "beta memory"], "alpha query"
    )
    assert backend == "hash"
    assert len(vectors) == 2
    assert len(query_vector) == len(vectors[0])
    assert -1.0 <= cosine(query_vector, vectors[0]) <= 1.0


def test_context_reports_backend(fresh_db, monkeypatch):
    from totem_mcp.context import engineering_context
    from totem_mcp.tools import memory_create

    monkeypatch.setenv("TOTEM_EMBEDDING_BACKEND", "hash")
    memory_create(
        fresh_db, type="gotcha", title="Queue retries", statement="Retries use backoff.",
        tags=["queue"],
    )
    result = engineering_context(fresh_db, tags=["queue"], task="retry queue")
    assert result["embeddingBackend"] == "hash"
