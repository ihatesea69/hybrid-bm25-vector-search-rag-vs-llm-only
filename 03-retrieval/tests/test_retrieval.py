from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RETRIEVAL_MAIN = PROJECT_ROOT / "03-retrieval" / "retrieval.py"


def load_retrieval_module():
    spec = importlib.util.spec_from_file_location("medir_phase3_retrieval_test", RETRIEVAL_MAIN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {RETRIEVAL_MAIN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_answer_mode_supports_contextual_aliases():
    module = load_retrieval_module()

    assert module.normalize_answer_mode("contextual-hybrid") == "contextual_hybrid"
    assert module.normalize_answer_mode("contextual-grounded") == "contextual_hybrid_rag"


def test_rrf_fuse_can_keep_multiple_chunks_from_same_document():
    module = load_retrieval_module()
    bm25_rows = [
        {"node_id": "doc-1::0", "doc_id": "doc-1", "title": "A", "score": 0.8},
        {"node_id": "doc-1::1", "doc_id": "doc-1", "title": "A", "score": 0.7},
    ]
    vector_rows = [
        {"node_id": "doc-1::1", "doc_id": "doc-1", "title": "A", "score": 0.9},
        {"node_id": "doc-2::0", "doc_id": "doc-2", "title": "B", "score": 0.6},
    ]

    fused = module.rrf_fuse(bm25_rows, vector_rows, top_k=3, dedupe_by_doc=False)

    assert [row["node_id"] for row in fused] == ["doc-1::1", "doc-1::0", "doc-2::0"]


def test_format_rerank_document_uses_context_summary_for_contextual_mode():
    module = load_retrieval_module()
    row = {
        "title": "Fiber and cholesterol",
        "source_url": "https://example.com/fiber",
        "context_summary": "This chunk explains the mechanism linking soluble fiber to LDL reduction.",
        "contextualized_body": "This chunk explains the mechanism linking soluble fiber to LDL reduction.\n\nFiber binds bile acids.",
        "raw_body": "Fiber binds bile acids.",
    }

    formatted = module.format_rerank_document(row, use_contextual_fields=True)

    assert "Context summary:" in formatted
    assert "Fiber binds bile acids." in formatted
