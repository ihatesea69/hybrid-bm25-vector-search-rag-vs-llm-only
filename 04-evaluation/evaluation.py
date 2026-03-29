from __future__ import annotations

import argparse
import asyncio
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
    "llama-index": "llama_index",
    "llama-index-llms-openai": "llama_index.llms.openai",
}


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
    probe = (
        "import dotenv, psycopg, llama_index; "
        "import llama_index.llms.openai, llama_index.core.evaluation"
    )
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
    from llama_index.core.evaluation import (
        CorrectnessEvaluator,
        FaithfulnessEvaluator,
        PairwiseComparisonEvaluator,
        RelevancyEvaluator,
    )
    from llama_index.llms.openai import OpenAI
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    load_dotenv(ENV_FILE, override=False)
    return {
        "CorrectnessEvaluator": CorrectnessEvaluator,
        "FaithfulnessEvaluator": FaithfulnessEvaluator,
        "PairwiseComparisonEvaluator": PairwiseComparisonEvaluator,
        "RelevancyEvaluator": RelevancyEvaluator,
        "OpenAI": OpenAI,
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
        "closed-book": "llm_only",
        "llm-only": "llm_only",
        "llm_only": "llm_only",
    }
    return aliases.get(mode, mode)


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


def load_pairwise_summary() -> dict[str, Any]:
    rows = load_json_artifact(RESULTS_DIR / "pairwise_hybrid_rag_vs_llm_only.json", [])
    if not rows:
        return {"rows": 0, "hybrid_rag_wins": 0, "hybrid_rag_win_rate": 0.0}
    left_wins = sum(1 for row in rows if row.get("payload", {}).get("score") == 1.0)
    return {
        "rows": len(rows),
        "hybrid_rag_wins": left_wins,
        "hybrid_rag_win_rate": round(left_wins / len(rows), 4),
    }


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
    for row in rows:
        query_id = row["query_id"]
        relevant = {doc_id for doc_id, score in qrels.get(query_id, {}).items() if score > 0}
        gains = qrels.get(query_id, {})
        if not relevant:
            skipped_unjudged += 1
            continue
        ranked = unique_doc_ids([result["doc_id"] for result in row["results"] if result.get("doc_id")])
        per_query.append(
            {
                "query_id": query_id,
                "query_text": row["query_text"],
                "recall@10": recall_at_k(ranked, relevant, top_k),
                "mrr@10": reciprocal_rank_at_k(ranked, relevant, top_k),
                "ndcg@10": ndcg_at_k(ranked, gains, top_k),
                "map": average_precision(ranked, relevant),
            }
        )

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
    (RESULTS_DIR / f"retrieval_metrics_{mode}.json").write_text(
        json.dumps({"summary": summary, "per_query": per_query}, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    return summary


async def run_answer_eval_for_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rt = ensure_runtime()
    OpenAI = rt["OpenAI"]
    FaithfulnessEvaluator = rt["FaithfulnessEvaluator"]
    CorrectnessEvaluator = rt["CorrectnessEvaluator"]
    RelevancyEvaluator = rt["RelevancyEvaluator"]

    llm = OpenAI(
        model=env_str("OPENAI_LLM_MODEL", "gpt-4.1-mini"),
        api_key=env_str("OPENAI_API_KEY"),
        api_base=env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    faith = FaithfulnessEvaluator(llm=llm)
    correctness = CorrectnessEvaluator(llm=llm)
    relevancy = RelevancyEvaluator(llm=llm)

    out = []
    for row in rows:
        contexts = [
            item.get("text") or item.get("body", "")
            for item in row["evidence_bundle"]
        ]
        reference_answer = "\n\n".join(contexts[:3]) if contexts else None
        q = row["query_text"]
        a = row["answer_text"]
        out.append(
            {
                "query_id": row["query_id"],
                "mode": row["mode"],
                "faithfulness": (await faith.aevaluate(query=q, response=a, contexts=contexts)).model_dump(),
                "correctness": (await correctness.aevaluate(query=q, response=a, contexts=contexts, reference=reference_answer)).model_dump(),
                "relevancy": (await relevancy.aevaluate(query=q, response=a, contexts=contexts)).model_dump(),
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

    eval_rows = asyncio.run(run_answer_eval_for_rows(rows))
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
    OpenAI = rt["OpenAI"]
    PairwiseComparisonEvaluator = rt["PairwiseComparisonEvaluator"]
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

    llm = OpenAI(
        model=env_str("OPENAI_LLM_MODEL", "gpt-4.1-mini"),
        api_key=env_str("OPENAI_API_KEY"),
        api_base=env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    evaluator = PairwiseComparisonEvaluator(llm=llm)
    comparisons = []
    with connect() as conn, conn.cursor() as cur:
        for query_id, pair in list(by_query.items())[:limit]:
            if left_mode not in pair or right_mode not in pair:
                continue
            left = pair[left_mode]
            right = pair[right_mode]
            result = asyncio.run(
                evaluator.aevaluate(
                    query=left["query_text"],
                    response=left["answer_text"],
                    second_response=right["answer_text"],
                    reference="\n\n".join(
                        item.get("text") or item.get("body", "")
                        for item in left["evidence_bundle"][:3]
                    ),
                )
            )
            payload = result.model_dump()
            comparisons.append({"query_id": query_id, "payload": payload})
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
                    "preferred_left": payload.get("passing"),
                    "score": payload.get("score"),
                    "reasoning": payload.get("feedback"),
                    "payload": Jsonb(payload),
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
    for mode in ("hybrid",):
        path = RESULTS_DIR / f"retrieval_metrics_{mode}.json"
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            summaries[f"retrieval_{mode}"] = payload["summary"]
            failure_cases[f"retrieval_{mode}"] = sorted(
                payload["per_query"],
                key=lambda row: (row["ndcg@10"], row["recall@10"], row["mrr@10"]),
            )[:5]
    for mode in ("hybrid_rag", "llm_only"):
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
    pairwise_path = RESULTS_DIR / "pairwise_hybrid_rag_vs_llm_only.json"
    if pairwise_path.exists():
        rows = json.loads(pairwise_path.read_text(encoding="utf-8"))
        if rows:
            left_wins = sum(1 for row in rows if row["payload"].get("score") == 1.0)
            summaries["pairwise_hybrid_rag_vs_llm_only"] = {
                "rows": len(rows),
                "hybrid_rag_wins": left_wins,
                "hybrid_rag_win_rate": round(left_wins / len(rows), 4),
            }
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
            "answer_eval_hybrid_rag": (RESULTS_DIR / "answer_eval_hybrid_rag.json").exists(),
            "answer_eval_llm_only": (RESULTS_DIR / "answer_eval_llm_only.json").exists(),
            "pairwise_hybrid_rag_vs_llm_only": (RESULTS_DIR / "pairwise_hybrid_rag_vs_llm_only.json").exists(),
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
    metrics.add_argument("--mode", choices=["hybrid"], default="hybrid")
    metrics.add_argument("--batch-id", default=None)
    metrics.add_argument("--top-k", type=int, default=10)

    ans = sub.add_parser("answer-eval", help="Run answer-level evaluators with OpenAI judge.")
    ans.add_argument(
        "--mode",
        choices=["hybrid_rag", "hybrid-rag", "llm_only", "llm-only", "grounded", "closed-book"],
        default="hybrid_rag",
    )
    ans.add_argument("--batch-id", default=None)
    ans.add_argument("--limit", type=int, default=10)

    comp = sub.add_parser("compare", help="Run pairwise comparison between two answer modes.")
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
