from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
UV_CACHE_DIR = ROOT / ".uv-cache"
VENV = ROOT / ".venv"
VENV_PYTHON = (
    VENV / "Scripts" / "python.exe"
    if sys.platform.startswith("win")
    else VENV / "bin" / "python"
)
ENV = dict(os.environ)
ENV["UV_CACHE_DIR"] = str(UV_CACHE_DIR)
RUNTIME_ENV_VAR = "MEDIR_PHASE4_IN_VENV"

RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
QRELS_TEST = PROJECT_ROOT / "02-indexing" / "data" / "benchmark_qrels_test.tsv"
ENV_FILE = PROJECT_ROOT / ".env"

PACKAGE_TO_MODULE = {
    "psycopg[binary]": "psycopg",
    "python-dotenv": "dotenv",
    "deepeval": "deepeval",
}

ANSWER_RELEVANCY_THRESHOLD = 0.5
FAITHFULNESS_THRESHOLD = 0.5
CORRECTNESS_THRESHOLD = 0.6
PAIRWISE_THRESHOLD = 0.6

CORRECTNESS_EVALUATION_STEPS = [
    "Read the input question, the actual output, and the expected output derived from retrieved evidence.",
    "Check whether the actual output answers the question directly and stays within the scope of the evidence-backed expected output.",
    "Penalize unsupported claims, contradictions, overconfident medical assertions, and omission of uncertainty when the expected output is inconclusive.",
    "Reward answers that are accurate, cautious, and aligned with the evidence-grounded expected output.",
]

PAIRWISE_EVALUATION_STEPS = [
    "Compare the actual output against the expected output as two competing answers to the same question.",
    "Use the retrieval context as the grounding source whenever it is available.",
    "Prefer the answer that is more evidence-grounded, more medically cautious, more directly responsive, and less speculative.",
    "Give a high score only when the actual output is clearly better than the expected output; give a low score when the expected output is stronger or the actual output is unsupported.",
]


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value:
            values[key.strip()] = value
    return values


PROJECT_ENV = parse_env_file(ENV_FILE)


def env_str(name: str, default: str | None = None) -> str | None:
    value = PROJECT_ENV.get(name, os.environ.get(name, default))
    return value.strip() if isinstance(value, str) else value


def module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def running_in_local_venv() -> bool:
    return Path(sys.executable).resolve() == VENV_PYTHON.resolve()


def venv_has_runtime_modules() -> bool:
    if not VENV_PYTHON.exists():
        return False
    probe = "import dotenv, psycopg, deepeval"
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", probe],
        env=ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def provision_local_venv(install_missing: bool) -> None:
    if not VENV.exists():
        subprocess.run(["uv", "venv", str(VENV), "--python", sys.executable], check=True, env=ENV)

    if install_missing and not venv_has_runtime_modules():
        subprocess.run(
            ["uv", "pip", "install", "--python", str(VENV_PYTHON), "-r", str(REQUIREMENTS)],
            check=True,
            env=ENV,
        )


def ensure_running_in_local_venv() -> None:
    if running_in_local_venv() or os.environ.get(RUNTIME_ENV_VAR) == "1":
        return
    provision_local_venv(install_missing=True)
    child_env = dict(ENV)
    child_env.update(os.environ)
    child_env[RUNTIME_ENV_VAR] = "1"
    result = subprocess.run(
        [str(VENV_PYTHON), str(Path(__file__)), *sys.argv[1:]],
        env=child_env,
        check=False,
    )
    raise SystemExit(result.returncode)


def bootstrap() -> None:
    if not VENV.exists():
        subprocess.run(["uv", "venv", str(VENV), "--python", sys.executable], check=True, env=ENV)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(VENV_PYTHON), "-r", str(REQUIREMENTS)],
        check=True,
        env=ENV,
    )
    print(f"Venv ready at: {VENV}")
    print(f"Using interpreter: {VENV_PYTHON}")
    print(f"uv cache dir: {UV_CACHE_DIR}")


@lru_cache(maxsize=1)
def ensure_runtime():
    provision_local_venv(install_missing=True)
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=False)
    from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
    from deepeval.metrics.g_eval.utils import Rubric
    from deepeval.models import GPTModel
    from deepeval.test_case import LLMTestCase, LLMTestCaseParams
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    return {
        "AnswerRelevancyMetric": AnswerRelevancyMetric,
        "FaithfulnessMetric": FaithfulnessMetric,
        "GEval": GEval,
        "GPTModel": GPTModel,
        "LLMTestCase": LLMTestCase,
        "LLMTestCaseParams": LLMTestCaseParams,
        "Rubric": Rubric,
        "psycopg": psycopg,
        "dict_row": dict_row,
        "Jsonb": Jsonb,
    }


def db_url() -> str:
    direct = PROJECT_ENV.get("DATABASE_URL", os.environ.get("DATABASE_URL"))
    if direct:
        return direct
    host = PROJECT_ENV.get("POSTGRES_HOST", os.environ.get("POSTGRES_HOST", "localhost"))
    port = PROJECT_ENV.get("POSTGRES_PORT", os.environ.get("POSTGRES_PORT", "5432"))
    db = PROJECT_ENV.get("POSTGRES_DB", os.environ.get("POSTGRES_DB", "medir"))
    user = PROJECT_ENV.get("POSTGRES_USER", os.environ.get("POSTGRES_USER", "medir"))
    password = PROJECT_ENV.get("POSTGRES_PASSWORD", os.environ.get("POSTGRES_PASSWORD", "medir"))
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def connect():
    rt = ensure_runtime()
    return rt["psycopg"].connect(
        db_url(),
        row_factory=rt["dict_row"],
        autocommit=True,
        connect_timeout=3,
    )


def normalize_answer_mode(mode: str) -> str:
    aliases = {
        "grounded": "hybrid_rag",
        "hybrid-rag": "hybrid_rag",
        "hybrid_rag": "hybrid_rag",
        "contextual-hybrid": "contextual_hybrid",
        "contextual_hybrid": "contextual_hybrid",
        "contextual-grounded": "contextual_hybrid_rag",
        "contextual-hybrid-rag": "contextual_hybrid_rag",
        "contextual_hybrid_rag": "contextual_hybrid_rag",
        "closed-book": "llm_only",
        "llm-only": "llm_only",
        "llm_only": "llm_only",
    }
    return aliases.get(mode, mode)


def evaluation_model_name() -> str:
    return env_str("OPENAI_LLM_MODEL", "gpt-4.1-mini") or "gpt-4.1-mini"


def evaluation_base_url() -> str:
    return env_str("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1"


def build_deepeval_model():
    api_key = env_str("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for DeepEval-based evaluation.")
    rt = ensure_runtime()
    GPTModel = rt["GPTModel"]
    return GPTModel(
        model=evaluation_model_name(),
        api_key=api_key,
        base_url=evaluation_base_url(),
    )


def extract_contexts(bundle: list[dict[str, Any]] | None) -> list[str]:
    contexts: list[str] = []
    for item in bundle or []:
        text = item.get("text") or item.get("body") or ""
        cleaned = str(text).strip()
        if cleaned:
            contexts.append(cleaned)
    return contexts


def reference_answer_from_contexts(contexts: list[str]) -> str | None:
    selected = [context for context in contexts[:3] if context]
    return "\n\n".join(selected) if selected else None


def normalize_metric_payload(
    *,
    score: float | None,
    passing: bool,
    feedback: str,
    threshold: float,
    model_name: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    normalized_score = round(float(score), 4) if score is not None else None
    return {
        "score": normalized_score,
        "passing": passing,
        "feedback": feedback,
        "threshold": threshold,
        "model": model_name,
        "raw": raw,
    }


def metric_error_payload(
    *,
    threshold: float,
    model_name: str,
    error: Exception | str,
    score: float | None = None,
    short_circuit: bool = False,
) -> dict[str, Any]:
    message = str(error)
    return normalize_metric_payload(
        score=score,
        passing=False,
        feedback=message,
        threshold=threshold,
        model_name=model_name,
        raw={
            "error": message,
            "error_type": type(error).__name__ if isinstance(error, Exception) else "RuntimeError",
            "short_circuit": short_circuit,
        },
    )


def metric_payload_from_instance(metric, model_name: str) -> dict[str, Any]:
    score = getattr(metric, "score", None)
    passing = bool(getattr(metric, "success", False))
    feedback = str(getattr(metric, "reason", "") or "")
    threshold = float(getattr(metric, "threshold", 0.0) or 0.0)
    evaluation_model = getattr(metric, "evaluation_model", None)
    return normalize_metric_payload(
        score=score,
        passing=passing,
        feedback=feedback,
        threshold=threshold,
        model_name=str(evaluation_model or model_name),
        raw={
            "score": score,
            "success": getattr(metric, "success", None),
            "reason": getattr(metric, "reason", None),
            "threshold": threshold,
            "evaluation_model": str(evaluation_model or model_name),
        },
    )


def load_qrels(path: Path) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            qrels.setdefault(row["query-id"], {})[row["corpus-id"]] = int(row["score"])
    return qrels


def unique_doc_ids(ranked: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for doc_id in ranked:
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
    return out


def chunk_diagnostics(results: list[dict[str, Any]], top_k: int) -> dict[str, float]:
    top_rows = results[:top_k]
    unique_docs = unique_doc_ids([str(row.get("doc_id") or row.get("node_id") or "") for row in top_rows])
    duplicate_chunks = max(len(top_rows) - len(unique_docs), 0)
    return {
        "unique_docs@k": float(len(unique_docs)),
        "duplicate_chunks@k": float(duplicate_chunks),
    }


def average_precision(ranked: list[str], relevant: set[str]) -> float:
    if not relevant:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for rank, doc_id in enumerate(ranked, start=1):
        if doc_id not in relevant:
            continue
        hits += 1
        precision_sum += hits / rank
    return precision_sum / len(relevant)


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def reciprocal_rank_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    for i, doc_id in enumerate(ranked[:k], start=1):
        if doc_id in relevant:
            return 1.0 / i
    return 0.0


def dcg_at_k(ranked: list[str], gains: dict[str, int], k: int) -> float:
    import math

    dcg = 0.0
    for i, doc_id in enumerate(ranked[:k], start=1):
        rel = gains.get(doc_id, 0)
        dcg += rel / math.log2(i + 1)
    return dcg


def ndcg_at_k(ranked: list[str], gains: dict[str, int], k: int) -> float:
    dcg = dcg_at_k(ranked, gains, k)
    ideal_docs = sorted(gains, key=gains.get, reverse=True)
    idcg = dcg_at_k(ideal_docs, gains, k)
    return dcg / idcg if idcg else 0.0


def load_json_artifact(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def load_system_summary() -> dict[str, Any]:
    return load_json_artifact(RESULTS_DIR / "system_summary.json", {"summary": {}, "failure_cases": {}})


def load_metrics(mode: str) -> dict[str, Any]:
    return load_json_artifact(
        RESULTS_DIR / f"retrieval_metrics_{mode}.json",
        {"summary": {}, "per_query": []},
    )


def load_answer_eval(mode: str) -> list[dict[str, Any]]:
    canonical = normalize_answer_mode(mode)
    return load_json_artifact(RESULTS_DIR / f"answer_eval_{canonical}.json", [])


def pairwise_artifact_path(left_mode: str, right_mode: str) -> Path:
    return RESULTS_DIR / f"pairwise_{left_mode}_vs_{right_mode}.json"


def load_pairwise_summary(left_mode: str = "hybrid_rag", right_mode: str = "llm_only") -> dict[str, Any]:
    rows = load_json_artifact(pairwise_artifact_path(left_mode, right_mode), [])
    if not rows:
        summary = {"rows": 0, "left_wins": 0, "left_win_rate": 0.0}
        if left_mode == "hybrid_rag" and right_mode == "llm_only":
            summary["hybrid_rag_wins"] = 0
            summary["hybrid_rag_win_rate"] = 0.0
        return summary
    left_wins = sum(
        1
        for row in rows
        if row.get("preferred_left") is True or row.get("payload", {}).get("preferred_left") is True
    )
    summary = {
        "rows": len(rows),
        "left_wins": left_wins,
        "left_win_rate": round(left_wins / len(rows), 4),
    }
    if left_mode == "hybrid_rag" and right_mode == "llm_only":
        summary["hybrid_rag_wins"] = left_wins
        summary["hybrid_rag_win_rate"] = round(left_wins / len(rows), 4)
    return summary


def build_correctness_metric(model):
    rt = ensure_runtime()
    GEval = rt["GEval"]
    LLMTestCaseParams = rt["LLMTestCaseParams"]
    Rubric = rt["Rubric"]
    return GEval(
        name="Correctness",
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        evaluation_steps=CORRECTNESS_EVALUATION_STEPS,
        rubric=[
            Rubric(score_range=(0, 2), expected_outcome="The answer is unsupported, contradictory, or medically unsafe."),
            Rubric(score_range=(3, 5), expected_outcome="The answer is partially correct but misses key evidence or is too assertive."),
            Rubric(score_range=(6, 8), expected_outcome="The answer is mostly correct, grounded, and appropriately cautious."),
            Rubric(score_range=(9, 10), expected_outcome="The answer is directly correct, evidence-aligned, and clearly calibrated about uncertainty."),
        ],
        model=model,
        threshold=CORRECTNESS_THRESHOLD,
        async_mode=False,
    )


def build_pairwise_metric(model):
    rt = ensure_runtime()
    GEval = rt["GEval"]
    LLMTestCaseParams = rt["LLMTestCaseParams"]
    Rubric = rt["Rubric"]
    return GEval(
        name="PairwisePreference",
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
            LLMTestCaseParams.RETRIEVAL_CONTEXT,
        ],
        evaluation_steps=PAIRWISE_EVALUATION_STEPS,
        rubric=[
            Rubric(score_range=(0, 2), expected_outcome="The expected output is clearly stronger or the actual output is unsupported."),
            Rubric(score_range=(3, 5), expected_outcome="The two answers are close, mixed, or the actual output is only weakly preferable."),
            Rubric(score_range=(6, 8), expected_outcome="The actual output is meaningfully better grounded, safer, and more directly responsive."),
            Rubric(score_range=(9, 10), expected_outcome="The actual output is clearly superior with strong grounding and careful medical phrasing."),
        ],
        model=model,
        threshold=PAIRWISE_THRESHOLD,
        async_mode=False,
    )


def load_retrieval_contexts(batch_id: str, query_ids: list[str]) -> dict[str, list[str]]:
    filtered_ids = [query_id for query_id in query_ids if query_id]
    if not filtered_ids:
        return {}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT query_id, results
            FROM retrieval_runs
            WHERE batch_id = %(batch_id)s::uuid
              AND mode = 'hybrid'
              AND query_id = ANY(%(query_ids)s)
            """,
            {"batch_id": batch_id, "query_ids": filtered_ids},
        )
        rows = list(cur.fetchall())
    return {
        row["query_id"]: extract_contexts(row["results"])
        for row in rows
    }


def measure_metric(metric, test_case, model_name: str) -> dict[str, Any]:
    try:
        metric.measure(test_case, _show_indicator=False, _log_metric_to_confident=False)
        return metric_payload_from_instance(metric, model_name)
    except Exception as exc:
        threshold = float(getattr(metric, "threshold", 0.0) or 0.0)
        return metric_error_payload(threshold=threshold, model_name=model_name, error=exc)


def latest_batch_id(table: str, mode: str | None = None) -> str | None:
    query = f"SELECT batch_id::text FROM {table}"
    params: dict[str, Any] = {}
    if mode:
        query += " WHERE mode = %(mode)s"
        params["mode"] = mode
    query += " ORDER BY created_at DESC LIMIT 1"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
        return row["batch_id"] if row else None


def retrieval_metrics(mode: str, batch_id: str | None, top_k: int) -> dict[str, Any]:
    batch_id = batch_id or latest_batch_id("retrieval_runs", mode)
    if not batch_id:
        raise RuntimeError(f"No retrieval batch found for mode={mode}.")

    qrels = load_qrels(QRELS_TEST)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT query_id, query_text, results
            FROM retrieval_runs
            WHERE batch_id = %(batch_id)s::uuid AND mode = %(mode)s
            ORDER BY created_at ASC
            """,
            {"batch_id": batch_id, "mode": mode},
        )
        rows = list(cur.fetchall())

    per_query = []
    skipped_unjudged = 0
    contextual_chunk_stats: list[dict[str, float]] = []
    for row in rows:
        query_id = row["query_id"]
        relevant = {doc_id for doc_id, score in qrels.get(query_id, {}).items() if score > 0}
        gains = qrels.get(query_id, {})
        if not relevant:
            skipped_unjudged += 1
            continue
        ranked = unique_doc_ids([result["doc_id"] for result in row["results"] if result.get("doc_id")])
        payload = {
            "query_id": query_id,
            "query_text": row["query_text"],
            "recall@10": recall_at_k(ranked, relevant, top_k),
            "mrr@10": reciprocal_rank_at_k(ranked, relevant, top_k),
            "ndcg@10": ndcg_at_k(ranked, gains, top_k),
            "map": average_precision(ranked, relevant),
        }
        if mode == "contextual_hybrid":
            diagnostics = chunk_diagnostics(row["results"], top_k)
            payload.update({
                "unique_docs@k": diagnostics["unique_docs@k"],
                "duplicate_chunks@k": diagnostics["duplicate_chunks@k"],
            })
            contextual_chunk_stats.append(diagnostics)
        per_query.append(payload)

    summary = {
        "batch_id": batch_id,
        "mode": mode,
        "rows": len(per_query),
        "skipped_unjudged": skipped_unjudged,
        "recall@10": round(sum(r["recall@10"] for r in per_query) / len(per_query), 6) if per_query else 0.0,
        "mrr@10": round(sum(r["mrr@10"] for r in per_query) / len(per_query), 6) if per_query else 0.0,
        "ndcg@10": round(sum(r["ndcg@10"] for r in per_query) / len(per_query), 6) if per_query else 0.0,
        "map": round(sum(r["map"] for r in per_query) / len(per_query), 6) if per_query else 0.0,
    }
    if mode == "contextual_hybrid":
        summary["unique_docs@k"] = round(
            sum(row["unique_docs@k"] for row in contextual_chunk_stats) / len(contextual_chunk_stats),
            4,
        ) if contextual_chunk_stats else 0.0
        summary["duplicate_chunks@k"] = round(
            sum(row["duplicate_chunks@k"] for row in contextual_chunk_stats) / len(contextual_chunk_stats),
            4,
        ) if contextual_chunk_stats else 0.0
    (RESULTS_DIR / f"retrieval_metrics_{mode}.json").write_text(
        json.dumps({"summary": summary, "per_query": per_query}, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return summary


def run_answer_eval_for_rows(
    rows: list[dict[str, Any]],
    retrieval_context_map: dict[str, list[str]],
) -> list[dict[str, Any]]:
    rt = ensure_runtime()
    AnswerRelevancyMetric = rt["AnswerRelevancyMetric"]
    FaithfulnessMetric = rt["FaithfulnessMetric"]
    LLMTestCase = rt["LLMTestCase"]

    model = build_deepeval_model()
    model_name = evaluation_model_name()
    out: list[dict[str, Any]] = []

    for row in rows:
        direct_contexts = extract_contexts(row["evidence_bundle"])
        fallback_contexts = retrieval_context_map.get(row["query_id"], [])
        correctness_contexts = direct_contexts or fallback_contexts
        reference_answer = reference_answer_from_contexts(correctness_contexts)

        faithfulness_metric = FaithfulnessMetric(
            threshold=FAITHFULNESS_THRESHOLD,
            model=model,
            async_mode=False,
        )
        relevancy_metric = AnswerRelevancyMetric(
            threshold=ANSWER_RELEVANCY_THRESHOLD,
            model=model,
            async_mode=False,
        )
        correctness_metric = build_correctness_metric(model)

        query_text = row["query_text"]
        answer_text = row["answer_text"]

        if direct_contexts:
            faithfulness_case = LLMTestCase(
                input=query_text,
                actual_output=answer_text,
                retrieval_context=direct_contexts,
            )
            faithfulness_payload = measure_metric(faithfulness_metric, faithfulness_case, model_name)
        else:
            faithfulness_payload = metric_error_payload(
                threshold=FAITHFULNESS_THRESHOLD,
                model_name=model_name,
                error="No retrieval context available for grounding-based evaluation.",
                score=0.0,
                short_circuit=True,
            )

        relevancy_case = LLMTestCase(
            input=query_text,
            actual_output=answer_text,
        )
        relevancy_payload = measure_metric(relevancy_metric, relevancy_case, model_name)

        if reference_answer:
            correctness_case = LLMTestCase(
                input=query_text,
                actual_output=answer_text,
                expected_output=reference_answer,
            )
            correctness_payload = measure_metric(correctness_metric, correctness_case, model_name)
        else:
            correctness_payload = metric_error_payload(
                threshold=CORRECTNESS_THRESHOLD,
                model_name=model_name,
                error="No evidence-backed reference answer available for correctness evaluation.",
                score=None,
                short_circuit=True,
            )

        out.append(
            {
                "query_id": row["query_id"],
                "query_text": query_text,
                "mode": row["mode"],
                "faithfulness": faithfulness_payload,
                "correctness": correctness_payload,
                "relevancy": relevancy_payload,
            }
        )
    return out


def answer_eval(mode: str, batch_id: str | None, limit: int) -> dict[str, Any]:
    mode = normalize_answer_mode(mode)
    batch_id = batch_id or latest_batch_id("answer_runs", mode)
    if not batch_id:
        raise RuntimeError(f"No answer batch found for mode={mode}.")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT batch_id::text AS batch_id, query_id, query_text, mode, answer_text, citations, evidence_bundle
            FROM answer_runs
            WHERE batch_id = %(batch_id)s::uuid AND mode = %(mode)s
            ORDER BY created_at ASC
            LIMIT %(limit)s
            """,
            {"batch_id": batch_id, "mode": mode, "limit": limit},
        )
        rows = list(cur.fetchall())

    retrieval_context_map = load_retrieval_contexts(
        batch_id,
        [row["query_id"] for row in rows if row.get("query_id")],
    )
    eval_rows = run_answer_eval_for_rows(rows, retrieval_context_map)
    out_path = RESULTS_DIR / f"answer_eval_{mode}.json"
    out_path.write_text(json.dumps(eval_rows, indent=2, ensure_ascii=True), encoding="utf-8")

    rt = ensure_runtime()
    Jsonb = rt["Jsonb"]
    with connect() as conn, conn.cursor() as cur:
        for row in eval_rows:
            for evaluator_name in ("faithfulness", "correctness", "relevancy"):
                payload = row[evaluator_name]
                cur.execute(
                    """
                    INSERT INTO answer_evaluations (batch_id, mode, query_id, evaluator, score, passing, feedback, payload)
                    VALUES (%(batch_id)s::uuid, %(mode)s, %(query_id)s, %(evaluator)s, %(score)s, %(passing)s, %(feedback)s, %(payload)s)
                    """,
                    {
                        "batch_id": batch_id,
                        "mode": row["mode"],
                        "query_id": row["query_id"],
                        "evaluator": evaluator_name,
                        "score": payload.get("score"),
                        "passing": payload.get("passing"),
                        "feedback": payload.get("feedback"),
                        "payload": Jsonb(payload),
                    },
                )
    return {"batch_id": batch_id, "mode": mode, "rows": len(eval_rows)}


def compare(left_mode: str, right_mode: str, batch_id: str | None, limit: int) -> dict[str, Any]:
    left_mode = normalize_answer_mode(left_mode)
    right_mode = normalize_answer_mode(right_mode)
    rt = ensure_runtime()
    LLMTestCase = rt["LLMTestCase"]
    Jsonb = rt["Jsonb"]

    batch_id = batch_id or latest_batch_id("answer_runs", left_mode)
    if not batch_id:
        raise RuntimeError("No answer batch found for comparison.")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT query_id, query_text, mode, answer_text, evidence_bundle
            FROM answer_runs
            WHERE batch_id = %(batch_id)s::uuid AND mode IN (%(left)s, %(right)s)
            ORDER BY created_at ASC
            """,
            {"batch_id": batch_id, "left": left_mode, "right": right_mode},
        )
        rows = list(cur.fetchall())

    by_query: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_query.setdefault(row["query_id"], {})[row["mode"]] = row

    retrieval_context_map = load_retrieval_contexts(
        batch_id,
        [query_id for query_id in by_query if query_id],
    )
    model = build_deepeval_model()
    model_name = evaluation_model_name()
    evaluator = build_pairwise_metric(model)
    comparisons = []
    with connect() as conn, conn.cursor() as cur:
        for query_id, pair in list(by_query.items())[:limit]:
            if left_mode not in pair or right_mode not in pair:
                continue
            left = pair[left_mode]
            right = pair[right_mode]
            contexts = extract_contexts(left["evidence_bundle"]) or retrieval_context_map.get(query_id, [])
            test_case = LLMTestCase(
                input=left["query_text"],
                actual_output=left["answer_text"],
                expected_output=right["answer_text"],
                retrieval_context=contexts,
            )
            payload = measure_metric(evaluator, test_case, model_name)
            preferred_left = payload.get("passing")
            comparisons.append(
                {
                    "query_id": query_id,
                    "query_text": left["query_text"],
                    "left_mode": left_mode,
                    "right_mode": right_mode,
                    "preferred_left": preferred_left,
                    "score": payload.get("score"),
                    "reasoning": payload.get("feedback"),
                    "payload": {**payload, "preferred_left": preferred_left},
                }
            )
            cur.execute(
                """
                INSERT INTO comparison_runs (batch_id, query_id, left_mode, right_mode, preferred_left, score, reasoning, payload)
                VALUES (%(batch_id)s::uuid, %(query_id)s, %(left_mode)s, %(right_mode)s, %(preferred_left)s, %(score)s, %(reasoning)s, %(payload)s)
                """,
                {
                    "batch_id": batch_id,
                    "query_id": query_id,
                    "left_mode": left_mode,
                    "right_mode": right_mode,
                    "preferred_left": preferred_left,
                    "score": payload.get("score"),
                    "reasoning": payload.get("feedback"),
                    "payload": Jsonb({**payload, "preferred_left": preferred_left}),
                },
            )
    (RESULTS_DIR / f"pairwise_{left_mode}_vs_{right_mode}.json").write_text(
        json.dumps(comparisons, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return {"batch_id": batch_id, "comparisons": len(comparisons), "left_mode": left_mode, "right_mode": right_mode}


def report() -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    failure_cases: dict[str, Any] = {}
    for mode in ("hybrid", "contextual_hybrid"):
        path = RESULTS_DIR / f"retrieval_metrics_{mode}.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            summaries[f"retrieval_{mode}"] = payload["summary"]
            failure_cases[f"retrieval_{mode}"] = sorted(
                payload["per_query"],
                key=lambda row: (row["ndcg@10"], row["recall@10"], row["mrr@10"]),
            )[:5]
    for mode in ("hybrid_rag", "contextual_hybrid_rag", "llm_only"):
        path = RESULTS_DIR / f"answer_eval_{mode}.json"
        if path.exists():
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not rows:
                continue
            def mean_score(key: str) -> float:
                vals = [row[key].get("score") for row in rows if row[key].get("score") is not None]
                return round(sum(vals) / len(vals), 4) if vals else 0.0
            summaries[f"answer_{mode}"] = {
                "rows": len(rows),
                "faithfulness": mean_score("faithfulness"),
                "correctness": mean_score("correctness"),
                "relevancy": mean_score("relevancy"),
            }
            failure_cases[f"answer_{mode}"] = sorted(
                rows,
                key=lambda row: (
                    row["faithfulness"].get("score") if row["faithfulness"].get("score") is not None else -1.0,
                    row["correctness"].get("score") if row["correctness"].get("score") is not None else -1.0,
                    row["relevancy"].get("score") if row["relevancy"].get("score") is not None else -1.0,
                ),
            )[:5]
    for left_mode, right_mode in (
        ("hybrid_rag", "llm_only"),
        ("contextual_hybrid_rag", "hybrid_rag"),
        ("contextual_hybrid_rag", "llm_only"),
    ):
        pairwise_path = pairwise_artifact_path(left_mode, right_mode)
        if not pairwise_path.exists():
            continue
        rows = json.loads(pairwise_path.read_text(encoding="utf-8"))
        if not rows:
            continue
        left_wins = sum(
            1
            for row in rows
            if row.get("preferred_left") is True or row.get("payload", {}).get("preferred_left") is True
        )
        summary_key = f"pairwise_{left_mode}_vs_{right_mode}"
        summaries[summary_key] = {
            "rows": len(rows),
            "left_wins": left_wins,
            "left_win_rate": round(left_wins / len(rows), 4),
        }
        if left_mode == "hybrid_rag" and right_mode == "llm_only":
            summaries[summary_key]["hybrid_rag_wins"] = left_wins
            summaries[summary_key]["hybrid_rag_win_rate"] = round(left_wins / len(rows), 4)
    payload = {"summary": summaries, "failure_cases": failure_cases}
    (RESULTS_DIR / "system_summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return payload


def get_evaluation_status() -> dict[str, Any]:
    summary: dict[str, Any] = {
        "db_url": db_url(),
        "qrels_test": QRELS_TEST.exists(),
        "results_dir": RESULTS_DIR.as_posix(),
        "artifacts": {
            "retrieval_metrics_hybrid": (RESULTS_DIR / "retrieval_metrics_hybrid.json").exists(),
            "retrieval_metrics_contextual_hybrid": (RESULTS_DIR / "retrieval_metrics_contextual_hybrid.json").exists(),
            "answer_eval_hybrid_rag": (RESULTS_DIR / "answer_eval_hybrid_rag.json").exists(),
            "answer_eval_contextual_hybrid_rag": (RESULTS_DIR / "answer_eval_contextual_hybrid_rag.json").exists(),
            "answer_eval_llm_only": (RESULTS_DIR / "answer_eval_llm_only.json").exists(),
            "pairwise_hybrid_rag_vs_llm_only": (RESULTS_DIR / "pairwise_hybrid_rag_vs_llm_only.json").exists(),
            "pairwise_contextual_hybrid_rag_vs_hybrid_rag": (RESULTS_DIR / "pairwise_contextual_hybrid_rag_vs_hybrid_rag.json").exists(),
            "pairwise_contextual_hybrid_rag_vs_llm_only": (RESULTS_DIR / "pairwise_contextual_hybrid_rag_vs_llm_only.json").exists(),
            "system_summary": (RESULTS_DIR / "system_summary.json").exists(),
        },
    }
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT count(*) FROM retrieval_runs) AS retrieval_runs,
                    (SELECT count(*) FROM answer_runs) AS answer_runs,
                    (SELECT count(*) FROM answer_evaluations) AS answer_evaluations,
                    (SELECT count(*) FROM comparison_runs) AS comparison_runs
                """
            )
            summary.update(cur.fetchone())
    except Exception as exc:
        summary["db_error"] = f"{type(exc).__name__}: {exc}"
    return summary


def status() -> None:
    print(json.dumps(get_evaluation_status(), indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4 evaluation entrypoint.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Create .venv and install requirements with uv.")

    metrics = sub.add_parser("retrieval-metrics", help="Compute retrieval metrics from retrieval_runs.")
    metrics.add_argument("--mode", choices=["hybrid", "contextual_hybrid"], default="hybrid")
    metrics.add_argument("--batch-id", default=None)
    metrics.add_argument("--top-k", type=int, default=10)

    ans = sub.add_parser("answer-eval", help="Run answer-level evaluators with DeepEval.")
    ans.add_argument(
        "--mode",
        choices=[
            "hybrid_rag",
            "hybrid-rag",
            "contextual_hybrid_rag",
            "contextual-hybrid-rag",
            "contextual-grounded",
            "llm_only",
            "llm-only",
            "grounded",
            "closed-book",
        ],
        default="hybrid_rag",
    )
    ans.add_argument("--batch-id", default=None)
    ans.add_argument("--limit", type=int, default=10)

    comp = sub.add_parser("compare", help="Run DeepEval-based pairwise comparison between two answer modes.")
    comp.add_argument("--left-mode", default="hybrid_rag")
    comp.add_argument("--right-mode", default="llm_only")
    comp.add_argument("--batch-id", default=None)
    comp.add_argument("--limit", type=int, default=10)

    sub.add_parser("report", help="Aggregate outputs into a system summary.")
    sub.add_parser("status", help="Print evaluation status.")

    args = parser.parse_args()

    if args.command == "bootstrap":
        bootstrap()
        return 0

    ensure_running_in_local_venv()

    if args.command == "retrieval-metrics":
        print(json.dumps(retrieval_metrics(args.mode, args.batch_id, args.top_k), indent=2))
    elif args.command == "answer-eval":
        print(json.dumps(answer_eval(args.mode, args.batch_id, args.limit), indent=2))
    elif args.command == "compare":
        print(json.dumps(compare(args.left_mode, args.right_mode, args.batch_id, args.limit), indent=2))
    elif args.command == "report":
        report()
    elif args.command == "status":
        status()
    else:
        parser.error(f"Unknown command: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
