from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
PHASE2 = PROJECT_ROOT / "02-indexing" / "data"
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
RUNTIME_ENV_VAR = "NUTRITION_PHASE3_IN_VENV"

SCHEMA_SQL = ROOT / "sql" / "schema.sql"
ENV_FILE = PROJECT_ROOT / ".env"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

INDEX_DOCUMENTS = PHASE2 / "index_documents.jsonl"
INDEX_NODES = PHASE2 / "index_nodes.jsonl"
BENCHMARK_QUERIES = PHASE2 / "benchmark_queries.jsonl"
QRELS_TEST = PHASE2 / "benchmark_qrels_test.tsv"

PACKAGE_TO_MODULE = {
    "psycopg[binary]": "psycopg",
    "python-dotenv": "dotenv",
    "llama-index": "llama_index",
    "llama-index-embeddings-openai": "llama_index.embeddings.openai",
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
        values[key.strip()] = value.strip()
    return values


PROJECT_ENV = parse_env_file(ENV_FILE)


def env_str(name: str, default: str | None = None) -> str | None:
    value = PROJECT_ENV.get(name, os.environ.get(name, default))
    return value.strip() if isinstance(value, str) else value


def db_url() -> str:
    direct = env_str("DATABASE_URL")
    if direct:
        return direct
    host = env_str("POSTGRES_HOST", "localhost")
    port = env_str("POSTGRES_PORT", "5432")
    db = env_str("POSTGRES_DB", "medir")
    user = env_str("POSTGRES_USER", "medir")
    password = env_str("POSTGRES_PASSWORD", "medir")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


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
        "import llama_index.embeddings.openai, llama_index.llms.openai"
    )
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", probe],
        env=ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def activate_local_site_packages(install_missing: bool) -> list[str]:
    if not VENV.exists():
        subprocess.run(["uv", "venv", str(VENV), "--python", sys.executable], check=True, env=ENV)

    site_paths = json.loads(
        subprocess.check_output(
            [str(VENV_PYTHON), "-c", "import site, json; print(json.dumps(site.getsitepackages()))"],
            text=True,
            env=ENV,
        ).strip()
    )
    for site_path in site_paths:
        import site
        site.addsitedir(site_path)

    missing = [
        package
        for package, module_name in PACKAGE_TO_MODULE.items()
        if not module_available(module_name)
    ]
    if missing and install_missing:
        subprocess.run(
            ["uv", "pip", "install", "--python", str(VENV_PYTHON), "-r", str(REQUIREMENTS)],
            check=True,
            env=ENV,
        )
    return site_paths


def ensure_running_in_local_venv() -> None:
    if running_in_local_venv() or os.environ.get(RUNTIME_ENV_VAR) == "1":
        return
    activate_local_site_packages(install_missing=True)
    if not venv_has_runtime_modules():
        subprocess.run(
            ["uv", "pip", "install", "--python", str(VENV_PYTHON), "-r", str(REQUIREMENTS)],
            check=True,
            env=ENV,
        )
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
    site_paths = activate_local_site_packages(install_missing=False)
    print(f"Venv ready at: {VENV}")
    print(f"Using site-packages from: {site_paths[-1]}")
    print(f"uv cache dir: {UV_CACHE_DIR}")


@lru_cache(maxsize=1)
def ensure_runtime():
    ensure_running_in_local_venv()
    activate_local_site_packages(install_missing=True)
    from dotenv import load_dotenv
    from llama_index.embeddings.openai import OpenAIEmbedding
    from llama_index.llms.openai import OpenAI
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    load_dotenv(ENV_FILE, override=False)
    return {
        "OpenAIEmbedding": OpenAIEmbedding,
        "OpenAI": OpenAI,
        "psycopg": psycopg,
        "dict_row": dict_row,
        "Jsonb": Jsonb,
    }


def connect():
    rt = ensure_runtime()
    return rt["psycopg"].connect(
        db_url(),
        row_factory=rt["dict_row"],
        autocommit=True,
        connect_timeout=3,
    )


def execute_sql_file(conn, sql_path: Path) -> None:
    statements = [
        part.strip()
        for part in sql_path.read_text(encoding="utf-8").split(";")
        if part.strip()
    ]
    with conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


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


def load_documents() -> list[dict[str, Any]]:
    return load_jsonl(INDEX_DOCUMENTS)


def load_nodes() -> list[dict[str, Any]]:
    return load_jsonl(INDEX_NODES)


def load_judged_query_ids() -> set[str]:
    judged: set[str] = set()
    with QRELS_TEST.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if int(row["score"]) > 0:
                judged.add(row["query-id"])
    return judged


def load_benchmark_queries(limit: int | None = None, judged_only: bool = True) -> list[dict[str, Any]]:
    rows = load_jsonl(BENCHMARK_QUERIES)
    if judged_only:
        judged = load_judged_query_ids()
        rows = [row for row in rows if row.get("_id") in judged]
    return rows[:limit] if limit is not None else rows


def dedupe_rows_by_doc(rows: list[dict[str, Any]], top_k: int | None = None) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        doc_id = row.get("doc_id") or row.get("node_id")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        deduped.append(row)
        if top_k is not None and len(deduped) >= top_k:
            break
    return deduped


def db_init() -> None:
    with connect() as conn:
        execute_sql_file(conn, SCHEMA_SQL)
    print("Database schema initialized.")


def ingest_postgres(force: bool) -> dict[str, Any]:
    rt = ensure_runtime()
    Jsonb = rt["Jsonb"]
    documents = load_documents()
    nodes = load_nodes()
    doc_ids = [row["doc_id"] for row in documents]

    with connect() as conn, conn.cursor() as cur:
        execute_sql_file(conn, SCHEMA_SQL)
        if force and doc_ids:
            cur.execute("DELETE FROM kb_nodes WHERE doc_id = ANY(%(doc_ids)s)", {"doc_ids": doc_ids})
            cur.execute("DELETE FROM kb_documents WHERE doc_id = ANY(%(doc_ids)s)", {"doc_ids": doc_ids})

        for row in documents:
            cur.execute(
                """
                INSERT INTO kb_documents (
                    doc_id, source_id, source_kind, title, text_path, source_uri,
                    mime_type, language, trust_level, tags
                ) VALUES (
                    %(doc_id)s, %(source_id)s, %(source_kind)s, %(title)s, NULL,
                    %(source_uri)s, %(mime_type)s, %(language)s, %(trust_level)s, %(tags)s
                )
                ON CONFLICT (doc_id) DO UPDATE SET
                    source_id = EXCLUDED.source_id,
                    source_kind = EXCLUDED.source_kind,
                    title = EXCLUDED.title,
                    source_uri = EXCLUDED.source_uri,
                    mime_type = EXCLUDED.mime_type,
                    language = EXCLUDED.language,
                    trust_level = EXCLUDED.trust_level,
                    tags = EXCLUDED.tags
                """,
                {**row, "tags": Jsonb(row.get("tags", []))},
            )

        for row in nodes:
            cur.execute(
                """
                INSERT INTO kb_nodes (
                    node_id, doc_id, source_id, title, body, parser, order_idx,
                    parent_node_id, level, token_count, section_type, node_meta
                ) VALUES (
                    %(node_id)s, %(doc_id)s, %(source_id)s, %(title)s, %(body)s,
                    %(parser)s, %(order_idx)s, %(parent_node_id)s, %(level)s,
                    %(token_count)s, %(section_type)s, %(node_meta)s
                )
                ON CONFLICT (node_id) DO UPDATE SET
                    doc_id = EXCLUDED.doc_id,
                    source_id = EXCLUDED.source_id,
                    title = EXCLUDED.title,
                    body = EXCLUDED.body,
                    parser = EXCLUDED.parser,
                    order_idx = EXCLUDED.order_idx,
                    parent_node_id = EXCLUDED.parent_node_id,
                    level = EXCLUDED.level,
                    token_count = EXCLUDED.token_count,
                    section_type = EXCLUDED.section_type,
                    node_meta = EXCLUDED.node_meta
                """,
                {**row, "node_meta": Jsonb(row["node_meta"])},
            )

        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM kb_documents) AS documents,
                (SELECT count(*) FROM kb_nodes) AS nodes
            """
        )
        summary = cur.fetchone()

    return {"documents_indexed": summary["documents"], "nodes_indexed": summary["nodes"]}


def bm25_rows(query_text: str, top_k: int) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                node_id,
                doc_id,
                source_id,
                title,
                body,
                section_type,
                -(body <@> to_bm25query(%(query)s, 'kb_nodes_bm25_idx')) AS score
            FROM kb_nodes
            WHERE (body <@> to_bm25query(%(query)s, 'kb_nodes_bm25_idx')) < -0.0
            ORDER BY body <@> to_bm25query(%(query)s, 'kb_nodes_bm25_idx')
            LIMIT %(scan_k)s
            """,
            {"query": query_text, "scan_k": max(top_k * 4, top_k)},
        )
        return dedupe_rows_by_doc(list(cur.fetchall()), top_k=top_k)


def vector_rows(query_text: str, top_k: int) -> list[dict[str, Any]]:
    rt = ensure_runtime()
    OpenAIEmbedding = rt["OpenAIEmbedding"]
    api_key = env_str("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for vector retrieval.")
    embedder = OpenAIEmbedding(
        model=env_str("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        api_key=api_key,
        api_base=env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    query_vec = vector_literal(embedder.get_query_embedding(query_text))
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                node_id,
                doc_id,
                source_id,
                title,
                body,
                section_type,
                1 - (embedding <=> %(vector)s::vector) AS score
            FROM kb_nodes
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %(vector)s::vector
            LIMIT %(scan_k)s
            """,
            {"vector": query_vec, "scan_k": max(top_k * 4, top_k)},
        )
        return dedupe_rows_by_doc(list(cur.fetchall()), top_k=top_k)


def rrf_fuse(*ranked_lists: list[dict[str, Any]], top_k: int, k: int = 60) -> list[dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    for rank_list in ranked_lists:
        for rank, row in enumerate(rank_list, start=1):
            node_id = row["node_id"]
            payload = scores.setdefault(node_id, {"row": row, "score": 0.0})
            payload["score"] += 1.0 / (k + rank)
    fused = sorted(scores.values(), key=lambda item: item["score"], reverse=True)
    return dedupe_rows_by_doc([{**item["row"], "score": item["score"]} for item in fused], top_k=top_k)


def hybrid_rows(query_text: str, top_k: int) -> list[dict[str, Any]]:
    return rrf_fuse(
        bm25_rows(query_text, top_k * 4),
        vector_rows(query_text, top_k * 4),
        top_k=top_k,
    )


def llm_complete(prompt: str) -> str:
    rt = ensure_runtime()
    OpenAI = rt["OpenAI"]
    llm = OpenAI(
        model=env_str("OPENAI_LLM_MODEL", "gpt-4.1-mini"),
        api_key=env_str("OPENAI_API_KEY"),
        api_base=env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    return str(llm.complete(prompt))


def hybrid_rag_answer(query_text: str, evidence: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    citations = []
    evidence_lines = []
    for idx, row in enumerate(evidence[:5], start=1):
        citations.append(
            {
                "citation_id": idx,
                "node_id": row["node_id"],
                "doc_id": row["doc_id"],
                "source_id": row.get("source_id"),
                "title": row.get("title"),
            }
        )
        snippet = (row.get("body") or "").replace("\n", " ").strip()
        evidence_lines.append(f"[{idx}] {row.get('title') or row['doc_id']}: {snippet[:900]}")

    prompt = (
        "You are answering nutrition-related health questions using retrieved evidence.\n"
        "Use only the evidence below. If the evidence is insufficient, say so.\n"
        "When you use evidence, cite it inline as [1], [2], etc.\n\n"
        f"Question: {query_text}\n\n"
        "Evidence:\n"
        + "\n\n".join(evidence_lines)
        + "\n\nAnswer:"
    )
    return llm_complete(prompt), citations


def llm_only_answer(query_text: str) -> str:
    prompt = (
        "Answer the following nutrition-related health question in a concise factual way. "
        "Do not mention citations because no retrieval context is provided.\n\n"
        f"Question: {query_text}\nAnswer:"
    )
    return llm_complete(prompt)


def insert_retrieval_run(
    batch_id: str,
    mode: str,
    query_id: str | None,
    query_text: str,
    results: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    rt = ensure_runtime()
    Jsonb = rt["Jsonb"]
    payload = {
        "batch_id": batch_id,
        "mode": mode,
        "query_id": query_id,
        "query_text": query_text,
        "results": results,
        "config": config,
    }
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO retrieval_runs (batch_id, mode, query_id, query_text, results, config)
            VALUES (%(batch_id)s::uuid, %(mode)s, %(query_id)s, %(query_text)s, %(results)s, %(config)s)
            """,
            {**payload, "results": Jsonb(results), "config": Jsonb(config)},
        )
    append_jsonl(RESULTS_DIR / f"retrieval_{batch_id}_{mode}.jsonl", payload)


def insert_answer_run(
    batch_id: str,
    mode: str,
    query_id: str | None,
    query_text: str,
    answer_text: str,
    citations: list[dict[str, Any]],
    evidence_bundle: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    rt = ensure_runtime()
    Jsonb = rt["Jsonb"]
    payload = {
        "batch_id": batch_id,
        "mode": mode,
        "query_id": query_id,
        "query_text": query_text,
        "answer_text": answer_text,
        "citations": citations,
        "evidence_bundle": evidence_bundle,
        "config": config,
    }
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO answer_runs (batch_id, mode, query_id, query_text, answer_text, citations, evidence_bundle, config)
            VALUES (%(batch_id)s::uuid, %(mode)s, %(query_id)s, %(query_text)s, %(answer_text)s, %(citations)s, %(evidence_bundle)s, %(config)s)
            """,
            {
                **payload,
                "citations": Jsonb(citations),
                "evidence_bundle": Jsonb(evidence_bundle),
                "config": Jsonb(config),
            },
        )
    append_jsonl(RESULTS_DIR / f"answers_{batch_id}_{mode}.jsonl", payload)


def run_query(
    mode: str,
    query_text: str,
    top_k: int,
    query_id: str | None = None,
    save_run: bool = True,
) -> dict[str, Any]:
    canonical = normalize_answer_mode(mode)
    batch_id = str(uuid.uuid4())

    if canonical == "hybrid":
        results = hybrid_rows(query_text, top_k)
        payload = {
            "batch_id": batch_id,
            "mode": "hybrid",
            "query": query_text,
            "results": results,
        }
        if save_run:
            insert_retrieval_run(batch_id, "hybrid", query_id, query_text, results, {"top_k": top_k})
        return payload

    if canonical == "hybrid_rag":
        retrieval = run_query("hybrid", query_text, top_k=top_k, query_id=query_id, save_run=save_run)
        answer_text, citations = hybrid_rag_answer(query_text, retrieval["results"])
        payload = {
            "batch_id": retrieval["batch_id"],
            "mode": "hybrid_rag",
            "query": query_text,
            "answer": answer_text,
            "citations": citations,
            "evidence_bundle": retrieval["results"],
        }
        if save_run:
            insert_answer_run(
                retrieval["batch_id"],
                "hybrid_rag",
                query_id,
                query_text,
                answer_text,
                citations,
                retrieval["results"],
                {"top_k": top_k},
            )
        return payload

    if canonical == "llm_only":
        answer_text = llm_only_answer(query_text)
        payload = {
            "batch_id": batch_id,
            "mode": "llm_only",
            "query": query_text,
            "answer": answer_text,
        }
        if save_run:
            insert_answer_run(batch_id, "llm_only", query_id, query_text, answer_text, [], [], {"top_k": 0})
        return payload

    raise ValueError(f"Unsupported mode: {mode}")


def run_demo_bundle(
    query_text: str,
    top_k: int = 5,
    query_id: str | None = None,
    save_run: bool = True,
) -> dict[str, Any]:
    batch_id = str(uuid.uuid4())
    timings_ms: dict[str, float] = {}
    total_started = perf_counter()

    hybrid_started = perf_counter()
    hybrid_results = hybrid_rows(query_text, top_k)
    timings_ms["hybrid"] = round((perf_counter() - hybrid_started) * 1000, 2)
    if save_run:
        insert_retrieval_run(batch_id, "hybrid", query_id, query_text, hybrid_results, {"top_k": top_k})

    rag_started = perf_counter()
    rag_answer, rag_citations = hybrid_rag_answer(query_text, hybrid_results)
    timings_ms["hybrid_rag"] = round((perf_counter() - rag_started) * 1000, 2)
    if save_run:
        insert_answer_run(
            batch_id,
            "hybrid_rag",
            query_id,
            query_text,
            rag_answer,
            rag_citations,
            hybrid_results,
            {"top_k": top_k},
        )

    llm_started = perf_counter()
    llm_answer = llm_only_answer(query_text)
    timings_ms["llm_only"] = round((perf_counter() - llm_started) * 1000, 2)
    if save_run:
        insert_answer_run(batch_id, "llm_only", query_id, query_text, llm_answer, [], [], {"top_k": 0})

    timings_ms["total"] = round((perf_counter() - total_started) * 1000, 2)
    return {
        "batch_id": batch_id,
        "query_text": query_text,
        "top_k": top_k,
        "hybrid": {
            "mode": "hybrid",
            "results": hybrid_results,
        },
        "hybrid_rag": {
            "mode": "hybrid_rag",
            "answer": rag_answer,
            "citations": rag_citations,
            "evidence_bundle": hybrid_results,
        },
        "llm_only": {
            "mode": "llm_only",
            "answer": llm_answer,
            "citations": [],
            "evidence_bundle": [],
        },
        "timings_ms": timings_ms,
    }


def batch(limit: int, top_k: int, include_unjudged: bool) -> dict[str, Any]:
    queries = load_benchmark_queries(limit=limit, judged_only=not include_unjudged)
    batch_id = str(uuid.uuid4())

    for row in queries:
        query_id = row.get("_id") or row.get("id") or row.get("query_id")
        query_text = row.get("text") or row.get("query") or ""

        hybrid = run_query("hybrid", query_text, top_k=top_k, query_id=query_id, save_run=False)
        insert_retrieval_run(batch_id, "hybrid", query_id, query_text, hybrid["results"], {"top_k": top_k})

        rag_answer, rag_citations = hybrid_rag_answer(query_text, hybrid["results"])
        insert_answer_run(
            batch_id,
            "hybrid_rag",
            query_id,
            query_text,
            rag_answer,
            rag_citations,
            hybrid["results"],
            {"top_k": top_k},
        )

        llm_answer = llm_only_answer(query_text)
        insert_answer_run(batch_id, "llm_only", query_id, query_text, llm_answer, [], [], {"top_k": 0})

    return {"batch_id": batch_id, "queries": len(queries)}


def clear_result_tables() -> dict[str, int]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE comparison_runs, answer_evaluations, answer_runs, retrieval_runs")
        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM retrieval_runs) AS retrieval_runs,
                (SELECT count(*) FROM answer_runs) AS answer_runs,
                (SELECT count(*) FROM answer_evaluations) AS answer_evaluations,
                (SELECT count(*) FROM comparison_runs) AS comparison_runs
            """
        )
        row = cur.fetchone()
    return dict(row)


def get_retrieval_status() -> dict[str, Any]:
    summary: dict[str, Any] = {
        "db_url": db_url(),
        "schema_sql": SCHEMA_SQL.exists(),
        "index_documents": INDEX_DOCUMENTS.exists(),
        "index_nodes": INDEX_NODES.exists(),
        "benchmark_queries": BENCHMARK_QUERIES.exists(),
        "qrels_test": QRELS_TEST.exists(),
        "results_dir": RESULTS_DIR.as_posix(),
    }

    if INDEX_DOCUMENTS.exists():
        docs = load_jsonl(INDEX_DOCUMENTS)
        summary["index_document_count"] = len(docs)
    if INDEX_NODES.exists():
        summary["index_node_count"] = len(load_jsonl(INDEX_NODES))

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT count(*) FROM kb_documents) AS documents,
                    (SELECT count(*) FROM kb_nodes) AS nodes,
                    (SELECT count(*) FROM kb_nodes WHERE embedding IS NOT NULL) AS embedded_nodes,
                    (SELECT count(*) FROM retrieval_runs) AS retrieval_runs,
                    (SELECT count(*) FROM answer_runs) AS answer_runs
                """
            )
            summary["postgres"] = {"reachable": True, **cur.fetchone()}

            cur.execute("SELECT DISTINCT mode FROM retrieval_runs ORDER BY mode")
            retrieval_modes = [row["mode"] for row in cur.fetchall()]
            cur.execute("SELECT DISTINCT mode FROM answer_runs ORDER BY mode")
            answer_modes = [row["mode"] for row in cur.fetchall()]
            summary["postgres"]["retrieval_modes"] = retrieval_modes
            summary["postgres"]["answer_modes"] = answer_modes
    except Exception as exc:
        summary["postgres"] = {
            "reachable": False,
            "error": str(exc),
            "hint": "Start PostgreSQL first with `docker compose up -d`.",
        }

    return summary


def status() -> None:
    print(json.dumps(get_retrieval_status(), indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 3 retrieval entrypoint.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Create .venv and install requirements with uv.")
    sub.add_parser("db-init", help="Create PostgreSQL schema and indexes.")

    ingest = sub.add_parser("ingest-postgres", help="Ingest phase 2 documents and nodes into PostgreSQL.")
    ingest.add_argument("--force", action="store_true")

    query = sub.add_parser("query", help="Run a single retrieval or answer query.")
    query.add_argument(
        "--mode",
        choices=["hybrid", "hybrid_rag", "hybrid-rag", "llm_only", "llm-only", "grounded", "closed-book"],
        required=True,
    )
    query.add_argument("--text", required=True)
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--query-id", default=None)

    batch_parser = sub.add_parser("batch", help="Run benchmark batch retrieval and answer generation.")
    batch_parser.add_argument("--limit", type=int, default=10)
    batch_parser.add_argument("--top-k", type=int, default=5)
    batch_parser.add_argument("--include-unjudged", action="store_true")

    sub.add_parser("clear-results", help="Clear retrieval/answer/evaluation runtime tables.")
    sub.add_parser("status", help="Print phase 3 runtime status.")

    args = parser.parse_args()

    try:
        if args.command == "bootstrap":
            bootstrap()
            return 0

        ensure_running_in_local_venv()

        if args.command == "db-init":
            db_init()
        elif args.command == "ingest-postgres":
            print(json.dumps(ingest_postgres(force=args.force), indent=2))
        elif args.command == "query":
            print(json.dumps(run_query(args.mode, args.text, args.top_k, query_id=args.query_id), indent=2, ensure_ascii=False))
        elif args.command == "batch":
            print(json.dumps(batch(limit=args.limit, top_k=args.top_k, include_unjudged=args.include_unjudged), indent=2))
        elif args.command == "clear-results":
            print(json.dumps(clear_result_tables(), indent=2))
        elif args.command == "status":
            status()
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        payload = {"command": args.command, "ok": False, "error": str(exc)}
        if args.command in {"db-init", "ingest-postgres", "query", "batch", "status"}:
            payload["hint"] = "Ensure PostgreSQL is running with `docker compose up -d`."
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
