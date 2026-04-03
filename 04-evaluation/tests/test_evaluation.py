from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_MAIN = PROJECT_ROOT / "04-evaluation" / "evaluation.py"


def load_evaluation_module():
    spec = importlib.util.spec_from_file_location("medir_phase4_evaluation_test", EVALUATION_MAIN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {EVALUATION_MAIN}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_pairwise_summary_supports_new_row_shape(tmp_path):
    module = load_evaluation_module()
    module.RESULTS_DIR = tmp_path
    (tmp_path / "pairwise_hybrid_rag_vs_llm_only.json").write_text(
        json.dumps(
            [
                {"query_id": "q1", "preferred_left": True, "payload": {"preferred_left": True}},
                {"query_id": "q2", "preferred_left": False, "payload": {"preferred_left": False}},
                {"query_id": "q3", "preferred_left": True, "payload": {"preferred_left": True}},
            ]
        ),
        encoding="utf-8",
    )

    summary = module.load_pairwise_summary()

    assert summary == {
        "rows": 3,
        "hybrid_rag_wins": 2,
        "hybrid_rag_win_rate": 0.6667,
    }


def test_report_preserves_dashboard_summary_contract(tmp_path):
    module = load_evaluation_module()
    module.RESULTS_DIR = tmp_path

    (tmp_path / "retrieval_metrics_hybrid.json").write_text(
        json.dumps(
            {
                "summary": {"rows": 2, "recall@10": 0.5, "mrr@10": 0.75, "ndcg@10": 0.6, "map": 0.55},
                "per_query": [
                    {"query_id": "q1", "query_text": "foo", "recall@10": 0.0, "mrr@10": 0.0, "ndcg@10": 0.0, "map": 0.0},
                    {"query_id": "q2", "query_text": "bar", "recall@10": 1.0, "mrr@10": 1.0, "ndcg@10": 1.0, "map": 1.0},
                ],
            }
        ),
        encoding="utf-8",
    )
    for mode in ("hybrid_rag", "llm_only"):
        (tmp_path / f"answer_eval_{mode}.json").write_text(
            json.dumps(
                [
                    {
                        "query_id": "q1",
                        "query_text": "foo",
                        "mode": mode,
                        "faithfulness": {"score": 0.2},
                        "correctness": {"score": 0.8},
                        "relevancy": {"score": 0.6},
                    },
                    {
                        "query_id": "q2",
                        "query_text": "bar",
                        "mode": mode,
                        "faithfulness": {"score": 0.4},
                        "correctness": {"score": 0.9},
                        "relevancy": {"score": 0.7},
                    },
                ]
            ),
            encoding="utf-8",
        )
    (tmp_path / "pairwise_hybrid_rag_vs_llm_only.json").write_text(
        json.dumps(
            [
                {"query_id": "q1", "preferred_left": True, "payload": {"preferred_left": True}},
                {"query_id": "q2", "preferred_left": False, "payload": {"preferred_left": False}},
            ]
        ),
        encoding="utf-8",
    )

    payload = module.report()

    assert "retrieval_hybrid" in payload["summary"]
    assert "answer_hybrid_rag" in payload["summary"]
    assert "answer_llm_only" in payload["summary"]
    assert "pairwise_hybrid_rag_vs_llm_only" in payload["summary"]
    assert "retrieval_hybrid" in payload["failure_cases"]
    assert "answer_hybrid_rag" in payload["failure_cases"]
