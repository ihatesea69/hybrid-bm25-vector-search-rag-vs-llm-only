from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INDEXING_MAIN = PROJECT_ROOT / "02-indexing" / "indexing.py"


def load_indexing_module():
    spec = importlib.util.spec_from_file_location("medir_phase2_indexing_test", INDEXING_MAIN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {INDEXING_MAIN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_document_chunks_keeps_short_doc_as_single_chunk():
    module = load_indexing_module()
    text = "Dietary fiber can help lower cholesterol when it is eaten regularly."

    chunks = module.build_document_chunks(text, target_tokens=80, overlap_tokens=10)

    assert chunks == [text]


def test_build_document_chunks_splits_long_doc_with_overlap():
    module = load_indexing_module()
    sentence = "Dietary fiber helps digestion and can support cholesterol control."
    text = " ".join([sentence] * 40)

    chunks = module.build_document_chunks(text, target_tokens=40, overlap_tokens=8)

    assert len(chunks) >= 2
    first_tail = chunks[0].split()[-8:]
    second_prefix = chunks[1].split()[:8]
    assert first_tail == second_prefix


def test_build_documents_and_nodes_from_source_rows_adds_contextual_fields():
    module = load_indexing_module()
    row = {
        "doc_id": "doc-1",
        "source_id": "test-source",
        "title": "Fiber and cholesterol",
        "source_url": "https://example.com/fiber",
        "content": " ".join(["Fiber can lower cholesterol."] * 60),
        "matched_keywords": ["fiber"],
        "section_type": "fiber",
    }

    def fake_context_provider(_row, _chunk_text, chunk_index, chunk_count):
        return f"context {chunk_index + 1}/{chunk_count}"

    documents, nodes = module.build_documents_and_nodes_from_source_rows([row], fake_context_provider)

    assert documents[0]["doc_id"] == "doc-1"
    assert documents[0]["chunk_count"] == len(nodes)
    assert nodes[0]["node_id"] == "doc-1::0"
    assert all(node["raw_body"] == node["body"] for node in nodes)
    assert all(node["context_summary"].startswith("context") for node in nodes)
    assert all(node["contextualized_body"].startswith(node["context_summary"]) for node in nodes)


def test_contextual_cache_key_is_stable():
    module = load_indexing_module()
    kwargs = {
        "model": "gpt-4.1-mini",
        "doc_id": "doc-1",
        "title": "Fiber and cholesterol",
        "source_url": "https://example.com/fiber",
        "document_text": "Whole document text",
        "chunk_text": "Chunk text",
        "chunk_index": 0,
        "chunk_count": 2,
    }

    key_a = module.contextual_cache_key(**kwargs)
    key_b = module.contextual_cache_key(**kwargs)

    assert key_a == key_b
