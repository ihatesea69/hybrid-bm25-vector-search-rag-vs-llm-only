from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL_MAIN = PROJECT_ROOT / "03-retrieval" / "retrieval.py"


def load_retrieval_module():
    spec = importlib.util.spec_from_file_location("medir_phase3_retrieval_test", RETRIEVAL_MAIN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {RETRIEVAL_MAIN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_row(node_id: str, doc_id: str, score: float, title: str) -> dict:
    return {
        "node_id": node_id,
        "doc_id": doc_id,
        "source_id": "src",
        "title": title,
        "body": f"Body for {title}",
        "section_type": "test",
        "score": score,
    }


def test_hybrid_search_disabled_keeps_existing_flow(monkeypatch):
    module = load_retrieval_module()
    module.PROJECT_ENV = {"RERANKER_ENABLED": "false"}

    bm25 = [sample_row("n1", "d1", 11.0, "BM25 A"), sample_row("n2", "d2", 10.0, "BM25 B")]
    vector = [sample_row("n3", "d3", 0.8, "Vector C"), sample_row("n2", "d2", 0.7, "Vector B")]

    monkeypatch.setattr(module, "bm25_rows", lambda query, top_k: bm25)
    monkeypatch.setattr(module, "vector_rows", lambda query, top_k: vector)

    payload = module.hybrid_search("fiber cholesterol", 2)

    assert [row["doc_id"] for row in payload["results"]] == ["d2", "d1"]
    assert payload["config"]["reranker"]["enabled"] is False
    assert payload["config"]["reranker"]["final_retrieval_path"] == "hybrid"


def test_hybrid_search_enabled_reranks_each_branch_before_fusion(monkeypatch):
    module = load_retrieval_module()
    module.PROJECT_ENV = {
        "RERANKER_ENABLED": "true",
        "RERANKER_PROVIDER": "cohere",
        "COHERE_API_KEY": "test-key",
        "COHERE_RERANK_MODEL": "rerank-v4.0-fast",
        "RERANKER_CANDIDATE_K": "20",
    }

    bm25 = [
        sample_row("n1", "d1", 9.0, "BM25 A"),
        sample_row("n2", "d2", 8.0, "BM25 B"),
    ]
    vector = [
        sample_row("n3", "d3", 0.9, "Vector C"),
        sample_row("n2", "d2", 0.8, "Vector B"),
    ]

    class FakeClient:
        def __init__(self):
            self.calls = []

        def rerank(self, **kwargs):
            self.calls.append(kwargs)
            documents = kwargs["documents"]
            if "BM25" in documents[0]:
                return SimpleNamespace(
                    results=[
                        SimpleNamespace(index=1, relevance_score=0.95),
                        SimpleNamespace(index=0, relevance_score=0.20),
                    ]
                )
            return SimpleNamespace(
                results=[
                    SimpleNamespace(index=1, relevance_score=0.91),
                    SimpleNamespace(index=0, relevance_score=0.15),
                ]
            )

    fake_client = FakeClient()
    monkeypatch.setattr(module, "bm25_rows", lambda query, top_k: bm25)
    monkeypatch.setattr(module, "vector_rows", lambda query, top_k: vector)
    monkeypatch.setattr(module, "build_cohere_client", lambda: fake_client)

    payload = module.hybrid_search("fiber cholesterol", 2)

    assert [row["doc_id"] for row in payload["results"]] == ["d2", "d1"]
    assert payload["results"][0]["score"] > payload["results"][1]["score"]
    assert payload["results"][0]["retrieval_path"] == "hybrid_rerank"
    assert payload["results"][0]["bm25_meta"]["rerank_rank"] == 1
    assert payload["results"][0]["vector_meta"]["rerank_rank"] == 1
    assert payload["config"]["reranker"]["bm25"]["applied"] is True
    assert payload["config"]["reranker"]["vector"]["applied"] is True
    assert payload["config"]["reranker"]["final_retrieval_path"] == "hybrid_rerank"


def test_rerank_branch_missing_api_key_falls_back(monkeypatch):
    module = load_retrieval_module()
    module.PROJECT_ENV = {
        "RERANKER_ENABLED": "true",
        "RERANKER_PROVIDER": "cohere",
        "COHERE_RERANK_MODEL": "rerank-v4.0-fast",
    }
    rows = [sample_row("n1", "d1", 1.0, "Doc A")]

    reranked, config = module.rerank_branch_rows("query", rows, branch_name="bm25")

    assert reranked[0]["bm25_meta"]["fallback"] is True
    assert "COHERE_API_KEY" in reranked[0]["bm25_meta"]["error"]
    assert config["fallback"] is True


def test_rerank_branch_provider_error_falls_back(monkeypatch):
    module = load_retrieval_module()
    module.PROJECT_ENV = {
        "RERANKER_ENABLED": "true",
        "RERANKER_PROVIDER": "cohere",
        "COHERE_API_KEY": "test-key",
        "COHERE_RERANK_MODEL": "rerank-v4.0-fast",
    }

    class BrokenClient:
        def rerank(self, **kwargs):
            raise RuntimeError("reranker unavailable")

    monkeypatch.setattr(module, "build_cohere_client", lambda: BrokenClient())
    rows = [sample_row("n1", "d1", 1.0, "Doc A")]

    reranked, config = module.rerank_branch_rows("query", rows, branch_name="vector")

    assert reranked[0]["vector_meta"]["fallback"] is True
    assert reranked[0]["vector_meta"]["error"] == "reranker unavailable"
    assert config["fallback"] is True
