from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_MAIN = PROJECT_ROOT / "05-demo-app" / "api" / "main.py"


def load_api_module():
    spec = importlib.util.spec_from_file_location("medir_demo_api_main", API_MAIN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load API module from {API_MAIN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_module():
    module = load_api_module()
    module.retrieval_module = SimpleNamespace(
        get_retrieval_status=lambda: {
            "index_document_count": 12,
            "index_node_count": 34,
            "postgres": {
                "reachable": True,
                "documents": 12,
                "nodes": 34,
                "embedded_nodes": 34,
                "retrieval_runs": 5,
                "answer_runs": 8,
                "retrieval_modes": ["hybrid"],
                "answer_modes": ["hybrid_rag", "llm_only"],
            },
        },
        run_demo_bundle=lambda query_text, top_k: {
            "batch_id": "demo-batch",
            "query_text": query_text,
            "top_k": top_k,
            "hybrid": {
                "results": [
                    {
                        "node_id": "node-1",
                        "doc_id": "doc-1",
                        "source_id": "src-1",
                        "title": "Healthy fats",
                        "body": "Omega-3 fatty acids may support cardiovascular health.",
                        "score": 0.8123456,
                        "section_type": "guideline",
                    }
                ]
            },
            "hybrid_rag": {
                "answer": "Use evidence-backed dietary guidance.",
                "citations": [
                    {
                        "citation_id": 1,
                        "node_id": "node-1",
                        "doc_id": "doc-1",
                        "source_id": "src-1",
                        "title": "Healthy fats",
                    }
                ],
                "evidence_bundle": [
                    {
                        "node_id": "node-1",
                        "doc_id": "doc-1",
                        "source_id": "src-1",
                        "title": "Healthy fats",
                        "body": "Omega-3 fatty acids may support cardiovascular health.",
                        "score": 0.8123456,
                        "section_type": "guideline",
                    }
                ],
            },
            "llm_only": {"answer": "General nutrition advice."},
            "timings_ms": {"hybrid": 8.0, "hybrid_rag": 120.0, "llm_only": 80.0, "total": 208.0},
        },
    )
    module.evaluation_module = SimpleNamespace(
        get_evaluation_status=lambda: {
            "artifacts": {
                "retrieval_metrics_hybrid": True,
                "answer_eval_hybrid_rag": True,
                "answer_eval_llm_only": True,
                "pairwise_hybrid_rag_vs_llm_only": True,
                "system_summary": True,
            }
        },
        load_system_summary=lambda: {
            "summary": {"retrieval_hybrid": {"recall@10": 0.42}},
            "failure_cases": {"retrieval_hybrid": [{"query_id": "q1"}]},
        },
    )
    return module


def test_demo_query_normalizes_response_shape():
    module = build_module()
    client = TestClient(module.app)

    response = client.post("/demo/query", json={"queryText": "How much omega-3 should I eat?", "topK": 3})

    assert response.status_code == 200
    payload = response.json()
    assert payload["batchId"] == "demo-batch"
    assert payload["queryText"] == "How much omega-3 should I eat?"
    assert payload["topK"] == 3
    assert payload["hybrid"]["results"][0]["nodeId"] == "node-1"
    assert payload["hybrid"]["results"][0]["snippet"].startswith("Omega-3")
    assert payload["hybridRag"]["citations"][0]["citationId"] == 1
    assert payload["llmOnly"]["answerText"] == "General nutrition advice."


def test_demo_query_validation_rejects_empty_text():
    module = build_module()
    client = TestClient(module.app)

    response = client.post("/demo/query", json={"queryText": "   ", "topK": 0})

    assert response.status_code == 422


def test_health_and_failure_cases_survive_missing_artifacts():
    module = load_api_module()
    module.retrieval_module = SimpleNamespace(
        get_retrieval_status=lambda: {
            "index_document_count": 0,
            "index_node_count": 0,
            "postgres": {"reachable": False, "error": "database offline"},
        }
    )
    module.evaluation_module = SimpleNamespace(
        get_evaluation_status=lambda: {
            "artifacts": {
                "retrieval_metrics_hybrid": False,
                "answer_eval_hybrid_rag": False,
                "answer_eval_llm_only": False,
                "pairwise_hybrid_rag_vs_llm_only": False,
                "system_summary": False,
            }
        },
        load_system_summary=lambda: {"summary": {}, "failure_cases": {}},
    )
    client = TestClient(module.app)

    health = client.get("/health")
    failures = client.get("/demo/failure-cases")

    assert health.status_code == 200
    assert health.json()["ok"] is False
    assert failures.status_code == 200
    assert failures.json() == {}


def test_runtime_helpers_are_exposed_for_demo_backend():
    retrieval_spec = importlib.util.spec_from_file_location(
        "medir_phase3_retrieval_smoke",
        PROJECT_ROOT / "03-retrieval" / "retrieval.py",
    )
    evaluation_spec = importlib.util.spec_from_file_location(
        "medir_phase4_evaluation_smoke",
        PROJECT_ROOT / "04-evaluation" / "evaluation.py",
    )
    if retrieval_spec is None or retrieval_spec.loader is None:
        raise RuntimeError("Unable to load retrieval.py")
    if evaluation_spec is None or evaluation_spec.loader is None:
        raise RuntimeError("Unable to load evaluation.py")

    retrieval_module = importlib.util.module_from_spec(retrieval_spec)
    evaluation_module = importlib.util.module_from_spec(evaluation_spec)
    retrieval_spec.loader.exec_module(retrieval_module)
    evaluation_spec.loader.exec_module(evaluation_module)

    assert hasattr(retrieval_module, "get_retrieval_status")
    assert hasattr(retrieval_module, "run_demo_bundle")
    assert hasattr(evaluation_module, "get_evaluation_status")
    assert hasattr(evaluation_module, "load_system_summary")
    assert hasattr(evaluation_module, "load_metrics")
    assert hasattr(evaluation_module, "load_answer_eval")
    assert hasattr(evaluation_module, "load_pairwise_summary")
