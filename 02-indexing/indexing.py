from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import site
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
PHASE1 = PROJECT_ROOT / "01-data-preparation"
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
ENV_FILE = PROJECT_ROOT / ".env"
UV_CACHE_DIR = ROOT / ".uv-cache"
VENV = ROOT / ".venv"
VENV_PYTHON = (
    VENV / "Scripts" / "python.exe"
    if sys.platform.startswith("win")
    else VENV / "bin" / "python"
)
ENV = dict(os.environ)
ENV["UV_CACHE_DIR"] = str(UV_CACHE_DIR)
RUNTIME_ENV_VAR = "NUTRITION_PHASE2_IN_VENV"

INPUT_FILES = [
    PHASE1 / "nfcorpus_nutrition.jsonl",
    PHASE1 / "nutrition_crawl.jsonl",
    PHASE1 / "pubmed_kb.jsonl",
]

DOCUMENTS_JSONL = DATA / "index_documents.jsonl"
NODES_JSONL = DATA / "index_nodes.jsonl"
BENCHMARK_QUERIES = DATA / "benchmark_queries.jsonl"
QRELS_TEST = DATA / "benchmark_qrels_test.tsv"
INDEXING_SUMMARY = DATA / "indexing_summary.json"

SCHEMA_SQL = PROJECT_ROOT / "03-retrieval" / "sql" / "schema.sql"
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

PACKAGE_TO_MODULE = {
    "datasets": "datasets",
    "llama-index": "llama_index",
    "llama-index-embeddings-openai": "llama_index.embeddings.openai",
    "numpy": "numpy",
    "openai": "openai",
    "psycopg[binary]": "psycopg",
    "python-dotenv": "dotenv",
}


def module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def activate_local_site_packages(install_missing: bool) -> Path:
    if not VENV.exists():
        subprocess.run(
            ["uv", "venv", str(VENV), "--python", sys.executable],
            check=True,
            env=ENV,
        )

    site_paths = subprocess.check_output(
        [str(VENV_PYTHON), "-c", "import site, json; print(json.dumps(site.getsitepackages()))"],
        text=True,
        env=ENV,
    ).strip()
    for site_path in json.loads(site_paths):
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
        for site_path in json.loads(site_paths):
            site.addsitedir(site_path)

    return Path(json.loads(site_paths)[-1])


def bootstrap() -> None:
    site_packages = activate_local_site_packages(install_missing=True)
    print(f"Venv ready at: {VENV}")
    print(f"Using site-packages from: {site_packages}")
    print(f"uv cache dir: {UV_CACHE_DIR}")


def running_in_local_venv() -> bool:
    return Path(sys.executable).resolve() == VENV_PYTHON.resolve()


def venv_has_runtime_modules() -> bool:
    if not VENV_PYTHON.exists():
        return False
    probe = (
        "import datasets, dotenv, numpy, psycopg; "
        "import llama_index, llama_index.core, llama_index.embeddings.openai"
    )
    result = subprocess.run(
        [str(VENV_PYTHON), "-c", probe],
        env=ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


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


def ensure_runtime():
    ensure_running_in_local_venv()
    activate_local_site_packages(install_missing=True)
    from datasets import load_dataset
    from dotenv import load_dotenv
    from llama_index.core import Document
    from llama_index.core.schema import TextNode
    from llama_index.embeddings.openai import OpenAIEmbedding
    import numpy as np
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    load_dotenv(ENV_FILE, override=False)
    return {
        "Document": Document,
        "TextNode": TextNode,
        "OpenAIEmbedding": OpenAIEmbedding,
        "load_dataset": load_dataset,
        "np": np,
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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


def validate_inputs() -> None:
    missing = [path.as_posix() for path in INPUT_FILES if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing phase 1 JSONL inputs:\n" + "\n".join(f"- {path}" for path in missing)
        )


def source_class(row: dict[str, Any]) -> str:
    source_id = row.get("source_id", "")
    if source_id == "beir_nfcorpus":
        return "benchmark"
    if source_id.startswith("pubmed"):
        return "research"
    return "authoritative_seed"


def flatten_record(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata", {}) or {}
    lifecycle = row.get("lifecycle", {}) or {}
    structure = row.get("structure", {}) or {}
    return {
        "doc_id": row["doc_id"],
        "source_id": row["source_id"],
        "source_kind": row.get("source_kind") or source_class(row),
        "title": row.get("title") or row["doc_id"],
        "source_url": row.get("source_url"),
        "content": row.get("content") or "",
        "mime_type": row.get("mime_type") or metadata.get("mime_type") or "text/plain",
        "language": row.get("language") or metadata.get("language") or "en",
        "matched_keywords": row.get("matched_keywords", []),
        "structure": structure,
        "metadata": metadata,
        "lifecycle": lifecycle,
    }


def load_phase1_records() -> list[dict[str, Any]]:
    validate_inputs()
    combined: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    for path in INPUT_FILES:
        for raw in load_jsonl(path):
            row = flatten_record(raw)
            doc_id = row["doc_id"]
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            combined.append(row)
    return combined


def prepare_documents_and_nodes(force: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if DOCUMENTS_JSONL.exists() and NODES_JSONL.exists() and not force:
        return load_jsonl(DOCUMENTS_JSONL), load_jsonl(NODES_JSONL)

    rt = ensure_runtime()
    Document = rt["Document"]
    TextNode = rt["TextNode"]

    source_rows = load_phase1_records()
    documents_out: list[dict[str, Any]] = []
    nodes_out: list[dict[str, Any]] = []

    for row in source_rows:
        Document(text=row["content"], metadata={"doc_id": row["doc_id"], "source_id": row["source_id"]})
        node_id = f"{row['doc_id']}::0"
        node_meta = {
            "matched_keywords": row["matched_keywords"],
            "structure": row["structure"],
            "metadata": row["metadata"],
            "lifecycle": row["lifecycle"],
        }
        TextNode(text=row["content"], id_=node_id, metadata=node_meta)

        documents_out.append(
            {
                "doc_id": row["doc_id"],
                "source_id": row["source_id"],
                "source_kind": row["source_kind"],
                "title": row["title"],
                "source_uri": row["source_url"],
                "mime_type": row["mime_type"],
                "language": row["language"],
                "trust_level": "high" if row["source_kind"] != "research" else "medium",
                "tags": row["matched_keywords"],
            }
        )
        nodes_out.append(
            {
                "node_id": node_id,
                "doc_id": row["doc_id"],
                "source_id": row["source_id"],
                "title": row["title"],
                "body": row["content"],
                "parser": "single_record",
                "order_idx": 1,
                "parent_node_id": None,
                "level": 0,
                "token_count": len(tokenize(row["content"])),
                "section_type": row["structure"].get("topic_key"),
                "node_meta": node_meta,
            }
        )

    write_jsonl(DOCUMENTS_JSONL, documents_out)
    write_jsonl(NODES_JSONL, nodes_out)
    return documents_out, nodes_out


def export_benchmark(force: bool) -> dict[str, int]:
    if BENCHMARK_QUERIES.exists() and QRELS_TEST.exists() and not force:
        return {
            "queries": len(load_jsonl(BENCHMARK_QUERIES)),
            "qrels_test": max(len(QRELS_TEST.read_text(encoding="utf-8").splitlines()) - 1, 0),
        }

    rt = ensure_runtime()
    load_dataset = rt["load_dataset"]

    prepared_docs, _prepared_nodes = prepare_documents_and_nodes(force=False)
    available_doc_ids = {row["doc_id"] for row in prepared_docs if row["source_id"] == "beir_nfcorpus"}

    queries = [dict(row) for row in load_dataset("BeIR/nfcorpus", "queries", split="queries")]
    qrels_test = [dict(row) for row in load_dataset("BeIR/nfcorpus-qrels", split="test")]

    filtered_qrels = [row for row in qrels_test if row["corpus-id"] in available_doc_ids]
    valid_query_ids = {row["query-id"] for row in filtered_qrels}
    filtered_queries = [row for row in queries if row["_id"] in valid_query_ids]

    write_jsonl(BENCHMARK_QUERIES, filtered_queries)
    write_tsv(QRELS_TEST, filtered_qrels)
    return {"queries": len(filtered_queries), "qrels_test": len(filtered_qrels)}


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


def index_postgres(force: bool) -> dict[str, Any]:
    documents, nodes = prepare_documents_and_nodes(force=False)
    with connect() as conn, conn.cursor() as cur:
        execute_sql_file(conn, SCHEMA_SQL)
        for row in documents:
            cur.execute(
                """
                INSERT INTO kb_documents (
                    doc_id, source_id, source_kind, title, text_path, source_uri, mime_type,
                    language, trust_level, tags
                ) VALUES (
                    %(doc_id)s, %(source_id)s, %(source_kind)s, %(title)s, NULL, %(source_uri)s,
                    %(mime_type)s, %(language)s, %(trust_level)s, %(tags)s
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
                {**row, "tags": json.dumps(row["tags"])},
            )

        if force:
            source_ids = sorted({row["source_id"] for row in nodes})
            cur.execute("DELETE FROM kb_nodes WHERE source_id = ANY(%(source_ids)s)", {"source_ids": source_ids})

        rt = ensure_runtime()
        Jsonb = rt["Jsonb"]
        for row in nodes:
            cur.execute(
                """
                INSERT INTO kb_nodes (
                    node_id, doc_id, source_id, title, body, parser, order_idx, parent_node_id,
                    level, token_count, section_type, node_meta
                ) VALUES (
                    %(node_id)s, %(doc_id)s, %(source_id)s, %(title)s, %(body)s, %(parser)s,
                    %(order_idx)s, %(parent_node_id)s, %(level)s, %(token_count)s,
                    %(section_type)s, %(node_meta)s
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


def index_vector(force: bool) -> dict[str, Any]:
    _documents, nodes = prepare_documents_and_nodes(force=False)
    api_key = env_str("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for vector indexing.")

    rt = ensure_runtime()
    OpenAIEmbedding = rt["OpenAIEmbedding"]
    embedder = OpenAIEmbedding(
        model=env_str("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        api_key=api_key,
        api_base=env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        embed_batch_size=100,
    )
    vectors = embedder.get_text_embedding_batch([row["body"] for row in nodes], show_progress=True)

    with connect() as conn, conn.cursor() as cur:
        for row, vector in zip(nodes, vectors, strict=False):
            cur.execute(
                """
                UPDATE kb_nodes
                SET embedding = %(embedding)s::vector
                WHERE node_id = %(node_id)s
                """,
                {"node_id": row["node_id"], "embedding": vector_literal(vector)},
            )
        cur.execute("SELECT count(*) AS embedded FROM kb_nodes WHERE embedding IS NOT NULL")
        embedded = cur.fetchone()["embedded"]

    return {"embedded_nodes": embedded, "embedding_model": env_str("OPENAI_EMBED_MODEL", "text-embedding-3-small")}


def write_summary_file(payload: dict[str, Any]) -> None:
    INDEXING_SUMMARY.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def status() -> None:
    payload: dict[str, Any] = {
        "inputs": {path.name: path.exists() for path in INPUT_FILES},
        "prepared_documents": DOCUMENTS_JSONL.exists(),
        "prepared_nodes": NODES_JSONL.exists(),
        "benchmark_queries": BENCHMARK_QUERIES.exists(),
        "qrels_test": QRELS_TEST.exists(),
    }

    if DOCUMENTS_JSONL.exists():
        docs = load_jsonl(DOCUMENTS_JSONL)
        payload["prepared_document_count"] = len(docs)
        source_breakdown: dict[str, int] = {}
        for row in docs:
            source_breakdown[row["source_id"]] = source_breakdown.get(row["source_id"], 0) + 1
        payload["source_breakdown"] = source_breakdown

    if NODES_JSONL.exists():
        payload["prepared_node_count"] = len(load_jsonl(NODES_JSONL))

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT count(*) FROM kb_documents) AS documents,
                    (SELECT count(*) FROM kb_nodes) AS nodes,
                    (SELECT count(*) FROM kb_nodes WHERE embedding IS NOT NULL) AS embedded_nodes
                """
            )
            row = cur.fetchone()
            payload["postgres"] = {
                "reachable": True,
                "documents": row["documents"],
                "nodes": row["nodes"],
                "embedded_nodes": row["embedded_nodes"],
            }
            cur.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN ('kb_nodes_bm25_idx', 'kb_nodes_embedding_idx')
                ORDER BY indexname
                """
            )
            payload["postgres"]["indexes"] = [r["indexname"] for r in cur.fetchall()]
    except Exception as exc:
        payload["postgres"] = {
            "reachable": False,
            "error": str(exc),
            "hint": "Start PostgreSQL before running index-postgres/index-vector.",
        }

    print(json.dumps(payload, indent=2, ensure_ascii=False))


def run_all(force: bool) -> None:
    bootstrap()
    docs, nodes = prepare_documents_and_nodes(force=force)
    benchmark = export_benchmark(force=force)
    payload = {
        "prepare_records": {"documents": len(docs), "nodes": len(nodes)},
        "export_benchmark": benchmark,
    }
    try:
        payload["index_postgres"] = index_postgres(force=force)
        payload["index_vector"] = index_vector(force=force)
    except Exception as exc:
        payload["index_postgres"] = {
            "skipped": True,
            "error": str(exc),
            "hint": "Run Docker/PostgreSQL, then re-run index-postgres and index-vector.",
        }
    write_summary_file(payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 2 indexing entrypoint.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Create .venv and install requirements with uv.")

    prep_parser = sub.add_parser("prepare-records", help="Read phase 1 JSONL and prepare document/node JSONL.")
    prep_parser.add_argument("--force", action="store_true")

    benchmark_parser = sub.add_parser("export-benchmark", help="Export NFCorpus queries and qrels for downstream use.")
    benchmark_parser.add_argument("--force", action="store_true")

    pg_parser = sub.add_parser("index-postgres", help="Create schema and upsert documents/nodes into PostgreSQL.")
    pg_parser.add_argument("--force", action="store_true")

    vector_parser = sub.add_parser("index-vector", help="Embed nodes with LlamaIndex and write vectors into PostgreSQL.")
    vector_parser.add_argument("--force", action="store_true")

    kb_parser = sub.add_parser("build-kb", help="Alias for prepare-records plus export-benchmark.")
    kb_parser.add_argument("--force", action="store_true")

    all_parser = sub.add_parser("all", help="Run the full phase 2 pipeline.")
    all_parser.add_argument("--force", action="store_true")

    sub.add_parser("status", help="Print a quick status snapshot for phase 2.")

    args = parser.parse_args()

    try:
        if args.command == "bootstrap":
            bootstrap()
        elif args.command == "prepare-records":
            docs, nodes = prepare_documents_and_nodes(force=args.force)
            print(json.dumps({"documents": len(docs), "nodes": len(nodes)}, indent=2))
        elif args.command == "export-benchmark":
            print(json.dumps(export_benchmark(force=args.force), indent=2))
        elif args.command == "index-postgres":
            print(json.dumps(index_postgres(force=args.force), indent=2))
        elif args.command == "index-vector":
            print(json.dumps(index_vector(force=args.force), indent=2))
        elif args.command == "build-kb":
            docs, nodes = prepare_documents_and_nodes(force=args.force)
            benchmark = export_benchmark(force=args.force)
            print(json.dumps({"documents": len(docs), "nodes": len(nodes), **benchmark}, indent=2))
        elif args.command == "all":
            run_all(force=args.force)
        elif args.command == "status":
            status()
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        payload = {
            "command": args.command,
            "ok": False,
            "error": str(exc),
        }
        if args.command in {"index-postgres", "index-vector", "all", "status"}:
            payload["hint"] = "Start PostgreSQL first with `docker compose up -d`."
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
