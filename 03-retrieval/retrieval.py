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
    "cohere": "cohere",
    "psycopg[binary]": "psycopg",
    "python-dotenv": "dotenv",
    "llama-index": "llama_index",
    "llama-index-embeddings-openai": "llama_index.embeddings.openai",
    "llama-index-llms-openai": "llama_index.llms.openai",
}

DEFAULT_RERANKER_PROVIDER = "cohere"
DEFAULT_COHERE_RERANK_MODEL = "rerank-v4.0-fast"
DEFAULT_RERANKER_CANDIDATE_K = 20
DEFAULT_COHERE_MAX_TOKENS_PER_DOC = 2000
DEFAULT_CONTEXTUAL_CANDIDATE_K = 150


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


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


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
        "import cohere, dotenv, psycopg, llama_index; "
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
    import cohere
    from dotenv import load_dotenv
    from llama_index.embeddings.openai import OpenAIEmbedding
    from llama_index.llms.openai import OpenAI
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    load_dotenv(ENV_FILE, override=False)
    return {
        "cohere": cohere,
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
    if not path.exists():
        return []
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


def reranker_enabled() -> bool:
    return env_bool("RERANKER_ENABLED", default=False)


def reranker_provider() -> str:
    return (env_str("RERANKER_PROVIDER", DEFAULT_RERANKER_PROVIDER) or DEFAULT_RERANKER_PROVIDER).lower()


def reranker_candidate_k() -> int:
    return max(env_int("RERANKER_CANDIDATE_K", DEFAULT_RERANKER_CANDIDATE_K), 1)


def contextual_candidate_k() -> int:
    return max(env_int("CONTEXTUAL_RETRIEVAL_CANDIDATE_K", DEFAULT_CONTEXTUAL_CANDIDATE_K), 1)


def cohere_rerank_model() -> str:
    return env_str("COHERE_RERANK_MODEL", DEFAULT_COHERE_RERANK_MODEL) or DEFAULT_COHERE_RERANK_MODEL


def cohere_max_tokens_per_doc() -> int:
    return max(env_int("COHERE_MAX_TOKENS_PER_DOC", DEFAULT_COHERE_MAX_TOKENS_PER_DOC), 1)


def reranker_base_config(override: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = reranker_enabled()
    provider = reranker_provider()
    config: dict[str, Any] = {
        "enabled": enabled,
        "provider": provider,
        "candidate_k": reranker_candidate_k(),
    }
    if provider == "cohere":
        config["model"] = cohere_rerank_model()
    if override:
        if override.get("enabled") is not None:
            config["enabled"] = bool(override["enabled"])
        provider_override = override.get("provider")
        if provider_override:
            config["provider"] = str(provider_override).strip().lower()
        candidate_k_override = override.get("candidate_k")
        if candidate_k_override is not None:
            config["candidate_k"] = max(int(candidate_k_override), 1)
        if config["provider"] == "cohere":
            config["model"] = str(override.get("model") or config.get("model") or cohere_rerank_model()).strip()
    return config


def format_rerank_document(row: dict[str, Any], *, use_contextual_fields: bool = False) -> str:
    title = str(row.get("title") or row.get("doc_id") or "").strip()
    source_url = str(row.get("source_url") or "").strip()
    context_summary = str(row.get("context_summary") or "").strip()
    body_field = "contextualized_body" if use_contextual_fields else "body"
    body = str(row.get(body_field) or row.get("raw_body") or row.get("body") or "").strip()

    parts: list[str] = []
    if title:
        parts.append(f"Title: {title}")
    if source_url:
        parts.append(f"Source URL: {source_url}")
    if use_contextual_fields and context_summary:
        parts.append(f"Context summary: {context_summary}")
    if body:
        parts.append(f"Body: {body}")
    return "\n\n".join(parts).strip()


def build_cohere_client():
    api_key = env_str("COHERE_API_KEY")
    if not api_key:
        raise RuntimeError("COHERE_API_KEY is required when reranker is enabled.")
    rt = ensure_runtime()
    CohereClientV2 = rt["cohere"].ClientV2
    base_url = env_str("COHERE_BASE_URL")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return CohereClientV2(**kwargs)


def attach_branch_metadata(
    rows: list[dict[str, Any]],
    *,
    branch_name: str,
    retrieval_path: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        branch_meta = {
            "original_rank": rank,
            "original_score": float(row.get("score", 0.0)),
            "rerank_rank": rank,
            "rerank_score": None,
            "provider": None,
            "model": None,
            "applied": False,
            "fallback": False,
            "error": None,
        }
        out.append(
            {
                **row,
                "retrieval_path": retrieval_path,
                f"{branch_name}_meta": branch_meta,
                "reranker_meta": {
                    "provider": None,
                    "model": None,
                    "candidate_k": len(rows),
                    "enabled": False,
                    "applied": False,
                    "branch": branch_name,
                },
            }
        )
    return out


def rerank_branch_rows(
    query_text: str,
    rows: list[dict[str, Any]],
    *,
    branch_name: str,
    retrieval_path: str = "hybrid",
    use_contextual_fields: bool = False,
    reranker_config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base_config = reranker_base_config(reranker_config)
    model_name = str(base_config.get("model") or cohere_rerank_model()).strip()
    branch_config: dict[str, Any] = {
        "branch": branch_name,
        "provider": base_config["provider"],
        "model": model_name if base_config["provider"] == "cohere" else base_config.get("model"),
        "candidate_k": len(rows),
        "enabled": base_config["enabled"],
        "applied": False,
        "fallback": False,
        "error": None,
    }
    prepared_rows = attach_branch_metadata(rows, branch_name=branch_name, retrieval_path=retrieval_path)
    if not rows or not base_config["enabled"]:
        return prepared_rows, branch_config

    if base_config["provider"] != "cohere":
        branch_config["fallback"] = True
        branch_config["error"] = f"Unsupported reranker provider: {base_config['provider']}"
        return prepared_rows, branch_config

    try:
        client = build_cohere_client()
        response = client.rerank(
            model=model_name,
            query=query_text,
            documents=[format_rerank_document(row, use_contextual_fields=use_contextual_fields) for row in rows],
            top_n=len(rows),
            max_tokens_per_doc=cohere_max_tokens_per_doc(),
        )
        reranked_rows: list[dict[str, Any]] = []
        for rerank_rank, item in enumerate(response.results, start=1):
            source_row = prepared_rows[item.index]
            branch_meta = {
                "original_rank": source_row[f"{branch_name}_meta"]["original_rank"],
                "original_score": float(source_row.get("score", 0.0)),
                "rerank_rank": rerank_rank,
                "rerank_score": float(item.relevance_score),
                "provider": "cohere",
                "model": model_name,
                "applied": True,
                "fallback": False,
                "error": None,
            }
            reranked_rows.append(
                {
                    **source_row,
                    "retrieval_path": f"{retrieval_path}_rerank",
                    f"{branch_name}_meta": branch_meta,
                    "reranker_meta": {
                        "provider": "cohere",
                        "model": model_name,
                        "candidate_k": len(rows),
                        "enabled": True,
                        "applied": True,
                        "branch": branch_name,
                    },
                }
            )
        branch_config.update({"applied": True})
        return reranked_rows, branch_config
    except Exception as exc:
        branch_config["fallback"] = True
        branch_config["error"] = str(exc)
        fallback_rows: list[dict[str, Any]] = []
        for row in prepared_rows:
            branch_meta = dict(row[f"{branch_name}_meta"])
            branch_meta["fallback"] = True
            branch_meta["error"] = str(exc)
            fallback_rows.append(
                {
                    **row,
                    f"{branch_name}_meta": branch_meta,
                    "reranker_meta": {
                        "provider": "cohere",
                        "model": model_name,
                        "candidate_k": len(rows),
                        "enabled": True,
                        "applied": False,
                        "branch": branch_name,
                        "fallback": True,
                        "error": str(exc),
                    },
                }
            )
        return fallback_rows, branch_config


def load_documents() -> list[dict[str, Any]]:
    rows = load_jsonl(INDEX_DOCUMENTS)
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload.setdefault("title", payload.get("doc_id"))
        payload.setdefault("source_url", None)
        payload.setdefault("document_text", "")
        payload.setdefault("document_token_count", 0)
        payload.setdefault("chunk_count", 1)
        payload.setdefault("section_type", None)
        out.append(payload)
    return out


def load_nodes() -> list[dict[str, Any]]:
    rows = load_jsonl(INDEX_NODES)
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        body = payload.get("raw_body") or payload.get("body") or ""
        payload.setdefault("source_url", None)
        payload["body"] = body
        payload.setdefault("raw_body", body)
        payload.setdefault("context_summary", "")
        payload.setdefault("contextualized_body", body)
        payload.setdefault("chunk_index", 0)
        payload.setdefault("chunk_count", 1)
        payload.setdefault("token_count", 0)
        payload.setdefault("char_count", len(body))
        out.append(payload)
    return out


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


def select_node_columns(score_sql: str) -> str:
    return f"""
        SELECT
            node_id,
            doc_id,
            source_id,
            title,
            source_url,
            body,
            COALESCE(NULLIF(raw_body, ''), body) AS raw_body,
            context_summary,
            COALESCE(NULLIF(contextualized_body, ''), body) AS contextualized_body,
            section_type,
            chunk_index,
            chunk_count,
            token_count,
            char_count,
            {score_sql} AS score
        FROM kb_nodes
    """


def contextual_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload["body"] = payload.get("raw_body") or payload.get("body")
    payload["raw_body"] = payload.get("raw_body") or payload.get("body")
    payload["contextualized_body"] = payload.get("contextualized_body") or payload.get("body")
    return payload


def db_init() -> None:
    with connect() as conn:
        execute_sql_file(conn, SCHEMA_SQL)
    print("Database schema initialized.")


def ingest_postgres(force: bool) -> dict[str, Any]:
    documents = load_documents()
    nodes = load_nodes()

    with connect() as conn, conn.cursor() as cur:
        execute_sql_file(conn, SCHEMA_SQL)
        if force:
            cur.execute("TRUNCATE TABLE kb_nodes, kb_documents")

        for row in documents:
            cur.execute(
                """
                INSERT INTO kb_documents (
                    doc_id, source_id, title, source_url, document_text, document_token_count, chunk_count, section_type
                ) VALUES (
                    %(doc_id)s, %(source_id)s, %(title)s, %(source_url)s, %(document_text)s, %(document_token_count)s,
                    %(chunk_count)s, %(section_type)s
                )
                ON CONFLICT (doc_id) DO UPDATE SET
                    source_id = EXCLUDED.source_id,
                    title = EXCLUDED.title,
                    source_url = EXCLUDED.source_url,
                    document_text = EXCLUDED.document_text,
                    document_token_count = EXCLUDED.document_token_count,
                    chunk_count = EXCLUDED.chunk_count,
                    section_type = EXCLUDED.section_type
                """,
                row,
            )

        for row in nodes:
            cur.execute(
                """
                INSERT INTO kb_nodes (
                    node_id, doc_id, source_id, title, source_url, body, raw_body, context_summary,
                    contextualized_body, section_type, chunk_index, chunk_count, token_count, char_count
                ) VALUES (
                    %(node_id)s, %(doc_id)s, %(source_id)s, %(title)s, %(source_url)s, %(body)s, %(raw_body)s,
                    %(context_summary)s, %(contextualized_body)s, %(section_type)s, %(chunk_index)s,
                    %(chunk_count)s, %(token_count)s, %(char_count)s
                )
                ON CONFLICT (node_id) DO UPDATE SET
                    doc_id = EXCLUDED.doc_id,
                    source_id = EXCLUDED.source_id,
                    title = EXCLUDED.title,
                    source_url = EXCLUDED.source_url,
                    body = EXCLUDED.body,
                    raw_body = EXCLUDED.raw_body,
                    context_summary = EXCLUDED.context_summary,
                    contextualized_body = EXCLUDED.contextualized_body,
                    section_type = EXCLUDED.section_type,
                    chunk_index = EXCLUDED.chunk_index,
                    chunk_count = EXCLUDED.chunk_count,
                    token_count = EXCLUDED.token_count,
                    char_count = EXCLUDED.char_count
                """,
                row,
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
            select_node_columns("-(body <@> to_bm25query(%(query)s, 'kb_nodes_bm25_idx'))")
            + """
            WHERE (body <@> to_bm25query(%(query)s, 'kb_nodes_bm25_idx')) < -0.0
            ORDER BY body <@> to_bm25query(%(query)s, 'kb_nodes_bm25_idx')
            LIMIT %(scan_k)s
            """,
            {"query": query_text, "scan_k": max(top_k * 4, top_k)},
        )
        return dedupe_rows_by_doc([contextual_row(row) for row in cur.fetchall()], top_k=top_k)


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
            select_node_columns("1 - (embedding <=> %(vector)s::vector)")
            + """
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %(vector)s::vector
            LIMIT %(scan_k)s
            """,
            {"vector": query_vec, "scan_k": max(top_k * 4, top_k)},
        )
        return dedupe_rows_by_doc([contextual_row(row) for row in cur.fetchall()], top_k=top_k)


def contextual_bm25_rows(query_text: str, candidate_k: int) -> list[dict[str, Any]]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            select_node_columns("-(contextualized_body <@> to_bm25query(%(query)s, 'kb_nodes_contextual_bm25_idx'))")
            + """
            WHERE (contextualized_body <@> to_bm25query(%(query)s, 'kb_nodes_contextual_bm25_idx')) < -0.0
            ORDER BY contextualized_body <@> to_bm25query(%(query)s, 'kb_nodes_contextual_bm25_idx')
            LIMIT %(scan_k)s
            """,
            {"query": query_text, "scan_k": candidate_k},
        )
        return [contextual_row(row) for row in cur.fetchall()]


def contextual_vector_rows(query_text: str, candidate_k: int) -> list[dict[str, Any]]:
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
            select_node_columns("1 - (contextual_embedding <=> %(vector)s::vector)")
            + """
            WHERE contextual_embedding IS NOT NULL
            ORDER BY contextual_embedding <=> %(vector)s::vector
            LIMIT %(scan_k)s
            """,
            {"vector": query_vec, "scan_k": candidate_k},
        )
        return [contextual_row(row) for row in cur.fetchall()]


def merge_result_metadata(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key in ("bm25_meta", "vector_meta"):
        if incoming.get(key) is not None:
            merged[key] = incoming[key]
    incoming_reranker = incoming.get("reranker_meta") or {}
    existing_reranker = merged.get("reranker_meta")
    if not isinstance(existing_reranker, dict):
        merged["reranker_meta"] = incoming_reranker
    elif incoming_reranker.get("enabled"):
        merged["reranker_meta"] = {**existing_reranker, **incoming_reranker}
    incoming_path = incoming.get("retrieval_path")
    if isinstance(incoming_path, str) and incoming_path.endswith("_rerank"):
        merged["retrieval_path"] = incoming_path
    return merged


def rrf_fuse(
    *ranked_lists: list[dict[str, Any]],
    top_k: int,
    k: int = 60,
    dedupe_by_doc: bool = True,
) -> list[dict[str, Any]]:
    scores: dict[str, dict[str, Any]] = {}
    for rank_list in ranked_lists:
        for rank, row in enumerate(rank_list, start=1):
            node_id = row["node_id"]
            payload = scores.setdefault(node_id, {"row": row, "score": 0.0})
            payload["row"] = merge_result_metadata(payload["row"], row)
            payload["score"] += 1.0 / (k + rank)
    fused = sorted(scores.values(), key=lambda item: item["score"], reverse=True)
    ranked = [{**item["row"], "score": item["score"]} for item in fused]
    if dedupe_by_doc:
        return dedupe_rows_by_doc(ranked, top_k=top_k)
    return ranked[:top_k]


def hybrid_search(query_text: str, top_k: int, reranker_config: dict[str, Any] | None = None) -> dict[str, Any]:
    base_config = reranker_base_config(reranker_config)
    if not base_config["enabled"]:
        results = rrf_fuse(
            bm25_rows(query_text, top_k * 4),
            vector_rows(query_text, top_k * 4),
            top_k=top_k,
        )
        for row in results:
            row.setdefault("retrieval_path", "hybrid")
        return {
            "results": results,
            "config": {
                "top_k": top_k,
                "candidate_k": None,
                "reranker": {
                    **base_config,
                    "bm25": {
                        "branch": "bm25",
                        "enabled": False,
                        "applied": False,
                        "fallback": False,
                        "error": None,
                    },
                    "vector": {
                        "branch": "vector",
                        "enabled": False,
                        "applied": False,
                        "fallback": False,
                        "error": None,
                    },
                    "final_retrieval_path": "hybrid",
                },
            },
        }

    candidate_k = max(top_k, base_config["candidate_k"])
    bm25_candidates = bm25_rows(query_text, candidate_k)
    vector_candidates = vector_rows(query_text, candidate_k)
    bm25_results, bm25_config = rerank_branch_rows(
        query_text,
        bm25_candidates,
        branch_name="bm25",
        retrieval_path="hybrid",
        reranker_config=reranker_config,
    )
    vector_results, vector_config = rerank_branch_rows(
        query_text,
        vector_candidates,
        branch_name="vector",
        retrieval_path="hybrid",
        reranker_config=reranker_config,
    )
    fused = rrf_fuse(
        bm25_results,
        vector_results,
        top_k=top_k,
    )
    retrieval_path = "hybrid_rerank" if bm25_config["applied"] or vector_config["applied"] else "hybrid"
    for row in fused:
        row["retrieval_path"] = retrieval_path if row.get("retrieval_path") == "hybrid_rerank" else row.get("retrieval_path", retrieval_path)
    return {
        "results": fused,
        "config": {
            "top_k": top_k,
            "candidate_k": candidate_k,
            "reranker": {
                **base_config,
                "bm25": bm25_config,
                "vector": vector_config,
                "final_retrieval_path": retrieval_path,
            },
        },
    }


def contextual_hybrid_search(query_text: str, top_k: int, reranker_config: dict[str, Any] | None = None) -> dict[str, Any]:
    base_config = reranker_base_config(reranker_config)
    candidate_k = max(top_k, contextual_candidate_k(), int(base_config.get("candidate_k") or 1))

    if not base_config["enabled"]:
        results = rrf_fuse(
            contextual_bm25_rows(query_text, candidate_k),
            contextual_vector_rows(query_text, candidate_k),
            top_k=top_k,
            dedupe_by_doc=False,
        )
        for row in results:
            row.setdefault("retrieval_path", "contextual_hybrid")
        return {
            "results": results,
            "config": {
                "top_k": top_k,
                "candidate_k": candidate_k,
                "chunk_level": True,
                "reranker": {
                    **base_config,
                    "bm25": {
                        "branch": "bm25",
                        "enabled": False,
                        "applied": False,
                        "fallback": False,
                        "error": None,
                    },
                    "vector": {
                        "branch": "vector",
                        "enabled": False,
                        "applied": False,
                        "fallback": False,
                        "error": None,
                    },
                    "final_retrieval_path": "contextual_hybrid",
                },
            },
        }

    bm25_candidates = contextual_bm25_rows(query_text, candidate_k)
    vector_candidates = contextual_vector_rows(query_text, candidate_k)
    bm25_results, bm25_config = rerank_branch_rows(
        query_text,
        bm25_candidates,
        branch_name="bm25",
        retrieval_path="contextual_hybrid",
        use_contextual_fields=True,
        reranker_config=reranker_config,
    )
    vector_results, vector_config = rerank_branch_rows(
        query_text,
        vector_candidates,
        branch_name="vector",
        retrieval_path="contextual_hybrid",
        use_contextual_fields=True,
        reranker_config=reranker_config,
    )
    fused = rrf_fuse(
        bm25_results,
        vector_results,
        top_k=top_k,
        dedupe_by_doc=False,
    )
    retrieval_path = (
        "contextual_hybrid_rerank"
        if bm25_config["applied"] or vector_config["applied"]
        else "contextual_hybrid"
    )
    for row in fused:
        row["retrieval_path"] = (
            retrieval_path
            if str(row.get("retrieval_path") or "").endswith("_rerank")
            else row.get("retrieval_path", retrieval_path)
        )
    return {
        "results": fused,
        "config": {
            "top_k": top_k,
            "candidate_k": candidate_k,
            "chunk_level": True,
            "reranker": {
                **base_config,
                "bm25": bm25_config,
                "vector": vector_config,
                "final_retrieval_path": retrieval_path,
            },
        },
    }


def hybrid_rows(query_text: str, top_k: int) -> list[dict[str, Any]]:
    return hybrid_search(query_text, top_k)["results"]


def contextual_hybrid_rows(query_text: str, top_k: int) -> list[dict[str, Any]]:
    return contextual_hybrid_search(query_text, top_k)["results"]


def llm_complete(prompt: str) -> str:
    rt = ensure_runtime()
    OpenAI = rt["OpenAI"]
    llm = OpenAI(
        model=env_str("OPENAI_LLM_MODEL", "gpt-4.1-mini"),
        api_key=env_str("OPENAI_API_KEY"),
        api_base=env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    return str(llm.complete(prompt))


def rag_answer(
    query_text: str,
    evidence: list[dict[str, Any]],
    *,
    evidence_limit: int,
) -> tuple[str, list[dict[str, Any]]]:
    citations = []
    evidence_lines = []
    for idx, row in enumerate(evidence[:evidence_limit], start=1):
        citations.append(
            {
                "citation_id": idx,
                "node_id": row["node_id"],
                "doc_id": row["doc_id"],
                "source_id": row.get("source_id"),
                "title": row.get("title"),
            }
        )
        snippet = (row.get("raw_body") or row.get("body") or "").replace("\n", " ").strip()
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


def hybrid_rag_answer(query_text: str, evidence: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    return rag_answer(query_text, evidence, evidence_limit=5)


def contextual_hybrid_rag_answer(query_text: str, evidence: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    return rag_answer(query_text, evidence, evidence_limit=20)


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
        search = hybrid_search(query_text, top_k)
        results = search["results"]
        payload = {
            "batch_id": batch_id,
            "mode": "hybrid",
            "query": query_text,
            "results": results,
        }
        if save_run:
            insert_retrieval_run(batch_id, "hybrid", query_id, query_text, results, search["config"])
        return payload

    if canonical == "contextual_hybrid":
        search = contextual_hybrid_search(query_text, top_k)
        results = search["results"]
        payload = {
            "batch_id": batch_id,
            "mode": "contextual_hybrid",
            "query": query_text,
            "results": results,
        }
        if save_run:
            insert_retrieval_run(batch_id, "contextual_hybrid", query_id, query_text, results, search["config"])
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
                {"top_k": top_k, "retrieval_mode": "hybrid"},
            )
        return payload

    if canonical == "contextual_hybrid_rag":
        retrieval = run_query("contextual_hybrid", query_text, top_k=top_k, query_id=query_id, save_run=save_run)
        answer_text, citations = contextual_hybrid_rag_answer(query_text, retrieval["results"])
        payload = {
            "batch_id": retrieval["batch_id"],
            "mode": "contextual_hybrid_rag",
            "query": query_text,
            "answer": answer_text,
            "citations": citations,
            "evidence_bundle": retrieval["results"],
        }
        if save_run:
            insert_answer_run(
                retrieval["batch_id"],
                "contextual_hybrid_rag",
                query_id,
                query_text,
                answer_text,
                citations,
                retrieval["results"],
                {"top_k": top_k, "retrieval_mode": "contextual_hybrid"},
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
    top_k: int = 20,
    query_id: str | None = None,
    save_run: bool = True,
    reranker_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    batch_id = str(uuid.uuid4())
    timings_ms: dict[str, float] = {}
    total_started = perf_counter()

    hybrid_started = perf_counter()
    hybrid_search_payload = hybrid_search(query_text, top_k, reranker_config=reranker_config)
    hybrid_results = hybrid_search_payload["results"]
    timings_ms["hybrid"] = round((perf_counter() - hybrid_started) * 1000, 2)
    if save_run:
        insert_retrieval_run(batch_id, "hybrid", query_id, query_text, hybrid_results, hybrid_search_payload["config"])

    contextual_started = perf_counter()
    contextual_search_payload = contextual_hybrid_search(query_text, top_k, reranker_config=reranker_config)
    contextual_results = contextual_search_payload["results"]
    timings_ms["contextual_hybrid"] = round((perf_counter() - contextual_started) * 1000, 2)
    if save_run:
        insert_retrieval_run(
            batch_id,
            "contextual_hybrid",
            query_id,
            query_text,
            contextual_results,
            contextual_search_payload["config"],
        )

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
            {"top_k": top_k, "retrieval_mode": "hybrid"},
        )

    contextual_rag_started = perf_counter()
    contextual_rag_answer, contextual_rag_citations = contextual_hybrid_rag_answer(query_text, contextual_results)
    timings_ms["contextual_hybrid_rag"] = round((perf_counter() - contextual_rag_started) * 1000, 2)
    if save_run:
        insert_answer_run(
            batch_id,
            "contextual_hybrid_rag",
            query_id,
            query_text,
            contextual_rag_answer,
            contextual_rag_citations,
            contextual_results,
            {"top_k": top_k, "retrieval_mode": "contextual_hybrid"},
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
            "config": hybrid_search_payload["config"],
        },
        "contextual_hybrid": {
            "mode": "contextual_hybrid",
            "results": contextual_results,
            "config": contextual_search_payload["config"],
        },
        "hybrid_rag": {
            "mode": "hybrid_rag",
            "answer": rag_answer,
            "citations": rag_citations,
            "evidence_bundle": hybrid_results,
        },
        "contextual_hybrid_rag": {
            "mode": "contextual_hybrid_rag",
            "answer": contextual_rag_answer,
            "citations": contextual_rag_citations,
            "evidence_bundle": contextual_results,
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

        hybrid = hybrid_search(query_text, top_k)
        insert_retrieval_run(batch_id, "hybrid", query_id, query_text, hybrid["results"], hybrid["config"])

        contextual = contextual_hybrid_search(query_text, top_k)
        insert_retrieval_run(
            batch_id,
            "contextual_hybrid",
            query_id,
            query_text,
            contextual["results"],
            contextual["config"],
        )

        rag_answer, rag_citations = hybrid_rag_answer(query_text, hybrid["results"])
        insert_answer_run(
            batch_id,
            "hybrid_rag",
            query_id,
            query_text,
            rag_answer,
            rag_citations,
            hybrid["results"],
            {"top_k": top_k, "retrieval_mode": "hybrid"},
        )

        contextual_rag_answer, contextual_rag_citations = contextual_hybrid_rag_answer(query_text, contextual["results"])
        insert_answer_run(
            batch_id,
            "contextual_hybrid_rag",
            query_id,
            query_text,
            contextual_rag_answer,
            contextual_rag_citations,
            contextual["results"],
            {"top_k": top_k, "retrieval_mode": "contextual_hybrid"},
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
                    (SELECT count(*) FROM kb_nodes WHERE contextual_embedding IS NOT NULL) AS contextual_embedded_nodes,
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
        choices=[
            "hybrid",
            "contextual_hybrid",
            "contextual-hybrid",
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
        required=True,
    )
    query.add_argument("--text", required=True)
    query.add_argument("--top-k", type=int, default=20)
    query.add_argument("--query-id", default=None)

    batch_parser = sub.add_parser("batch", help="Run benchmark batch retrieval and answer generation.")
    batch_parser.add_argument("--limit", type=int, default=10)
    batch_parser.add_argument("--top-k", type=int, default=20)
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
