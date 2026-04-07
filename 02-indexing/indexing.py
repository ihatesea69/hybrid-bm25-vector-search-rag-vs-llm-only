from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import site
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


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
CONTEXT_CACHE_JSON = DATA / "contextual_context_cache.json"

SCHEMA_SQL = PROJECT_ROOT / "03-retrieval" / "sql" / "schema.sql"
TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
CONTEXT_PROMPT_VERSION = "contextual-retrieval-v1"
PACKAGE_TO_MODULE = {
    "datasets": "datasets",
    "llama-index": "llama_index",
    "llama-index-embeddings-openai": "llama_index.embeddings.openai",
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
        "import datasets, dotenv, openai, psycopg; "
        "import llama_index, llama_index.embeddings.openai"
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


def env_int(name: str, default: int) -> int:
    value = env_str(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = env_str(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def chunk_target_tokens() -> int:
    return max(env_int("CONTEXTUAL_CHUNK_TARGET_TOKENS", 700), 200)


def chunk_overlap_tokens() -> int:
    return max(env_int("CONTEXTUAL_CHUNK_OVERLAP_TOKENS", 100), 0)


def context_max_output_tokens() -> int:
    return max(env_int("CONTEXTUAL_CONTEXT_MAX_TOKENS", 120), 32)


def context_document_char_limit() -> int:
    return max(env_int("CONTEXTUAL_DOCUMENT_CHAR_LIMIT", 16000), 4000)


def contextualization_enabled() -> bool:
    return env_bool("CONTEXTUAL_RETRIEVAL_ENABLED", default=True)


def context_model_name() -> str:
    return (
        env_str("CONTEXTUAL_RETRIEVAL_LLM_MODEL")
        or env_str("OPENAI_LLM_MODEL", "gpt-4.1-mini")
        or "gpt-4.1-mini"
    )


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
    from openai import OpenAI as OpenAIClient
    from llama_index.embeddings.openai import OpenAIEmbedding
    import psycopg
    from psycopg.rows import dict_row

    load_dotenv(ENV_FILE, override=False)
    return {
        "OpenAIClient": OpenAIClient,
        "OpenAIEmbedding": OpenAIEmbedding,
        "load_dataset": load_dataset,
        "psycopg": psycopg,
        "dict_row": dict_row,
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


def validate_inputs() -> None:
    missing = [path.as_posix() for path in INPUT_FILES if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing phase 1 JSONL inputs:\n" + "\n".join(f"- {path}" for path in missing)
        )


def slug(value: str) -> str:
    return (
        str(value)
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .strip("_")
    )


def infer_section_type(row: dict[str, Any]) -> str | None:
    section_type = row.get("section_type")
    if section_type:
        return str(section_type)
    structure = row.get("structure", {}) or {}
    if isinstance(structure, dict) and structure.get("topic_key"):
        return str(structure["topic_key"])
    matched = row.get("matched_keywords") or []
    if matched:
        return slug(matched[0])
    return None


def flatten_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": row["doc_id"],
        "source_id": row["source_id"],
        "title": row.get("title") or row["doc_id"],
        "source_url": row.get("source_url"),
        "content": row.get("content") or "",
        "matched_keywords": row.get("matched_keywords", []),
        "section_type": infer_section_type(row),
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


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "")]


def estimate_token_count(text: str) -> int:
    return len(tokenize(text))


def split_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in text.split("\n\n") if part.strip()]


def split_sentences(text: str) -> list[str]:
    sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(text) if part.strip()]
    return sentences or ([text.strip()] if text.strip() else [])


def normalize_inline_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def hard_split_by_tokens(text: str, max_tokens: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []

    windows: list[str] = []
    start = 0
    while start < len(words):
        windows.append(" ".join(words[start:start + max_tokens]).strip())
        start += max_tokens
    return [window for window in windows if window]


def split_segment_to_fit(segment: str, max_tokens: int) -> list[str]:
    if estimate_token_count(segment) <= max_tokens:
        return [segment]

    sentences = split_sentences(segment)
    if len(sentences) <= 1:
        return hard_split_by_tokens(segment, max_tokens)

    windows: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        sentence_tokens = estimate_token_count(sentence)
        if sentence_tokens > max_tokens:
            if current:
                windows.append(" ".join(current).strip())
                current = []
                current_tokens = 0
            windows.extend(hard_split_by_tokens(sentence, max_tokens))
            continue

        if current and current_tokens + sentence_tokens > max_tokens:
            windows.append(" ".join(current).strip())
            current = [sentence]
            current_tokens = sentence_tokens
        else:
            current.append(sentence)
            current_tokens += sentence_tokens

    if current:
        windows.append(" ".join(current).strip())

    return [window for window in windows if window]


def trailing_text_for_overlap(text: str, overlap_tokens: int) -> str:
    if overlap_tokens <= 0:
        return ""
    words = (text or "").split()
    if len(words) <= overlap_tokens:
        return " ".join(words)
    return " ".join(words[-overlap_tokens:])


def build_document_chunks(
    text: str,
    *,
    target_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    target_tokens = target_tokens or chunk_target_tokens()
    overlap_tokens = overlap_tokens if overlap_tokens is not None else chunk_overlap_tokens()
    if estimate_token_count(cleaned) <= target_tokens:
        return [cleaned]

    paragraph_segments = split_paragraphs(cleaned)
    raw_segments = paragraph_segments if len(paragraph_segments) > 1 else split_sentences(cleaned)
    if not raw_segments:
        return [cleaned]

    segments: list[str] = []
    max_segment_tokens = int(target_tokens * 1.2)
    for segment in raw_segments:
        normalized = normalize_inline_whitespace(segment)
        if not normalized:
            continue
        segments.extend(split_segment_to_fit(normalized, max_segment_tokens))

    if not segments:
        return [cleaned]

    base_chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for segment in segments:
        segment_tokens = estimate_token_count(segment)
        if current and current_tokens + segment_tokens > target_tokens:
            base_chunks.append(" ".join(current).strip())
            current = [segment]
            current_tokens = segment_tokens
        else:
            current.append(segment)
            current_tokens += segment_tokens

    if current:
        base_chunks.append(" ".join(current).strip())

    if len(base_chunks) <= 1 or overlap_tokens <= 0:
        return base_chunks

    chunks: list[str] = []
    for index, chunk in enumerate(base_chunks):
        if index == 0:
            chunks.append(chunk)
            continue
        overlap_text = trailing_text_for_overlap(base_chunks[index - 1], overlap_tokens)
        if overlap_text and not chunk.startswith(overlap_text):
            chunks.append(f"{overlap_text} {chunk}".strip())
        else:
            chunks.append(chunk)
    return chunks


def contextual_cache_key(
    *,
    model: str,
    doc_id: str,
    title: str,
    source_url: str | None,
    document_text: str,
    chunk_text: str,
    chunk_index: int,
    chunk_count: int,
) -> str:
    payload = json.dumps(
        {
            "prompt_version": CONTEXT_PROMPT_VERSION,
            "model": model,
            "doc_id": doc_id,
            "title": title,
            "source_url": source_url or "",
            "document_text": document_text,
            "chunk_text": chunk_text,
            "chunk_index": chunk_index,
            "chunk_count": chunk_count,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_context_cache() -> dict[str, str]:
    if not CONTEXT_CACHE_JSON.exists():
        return {}
    try:
        data = json.loads(CONTEXT_CACHE_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def save_context_cache(cache: dict[str, str]) -> None:
    CONTEXT_CACHE_JSON.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def document_excerpt(text: str, char_limit: int) -> str:
    if len(text) <= char_limit:
        return text
    head = text[: int(char_limit * 0.7)].strip()
    tail = text[-int(char_limit * 0.25):].strip()
    return f"{head}\n...\n{tail}"


class ContextualSummaryProvider:
    def __init__(self) -> None:
        self.enabled = contextualization_enabled()
        self.cache = load_context_cache()
        self.cache_dirty = False
        self.model = context_model_name()
        self.client = None
        if self.enabled:
            api_key = env_str("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required for contextual chunk generation.")
            rt = ensure_runtime()
            self.client = rt["OpenAIClient"](
                api_key=api_key,
                base_url=env_str("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            )

    def __call__(
        self,
        row: dict[str, Any],
        chunk_text: str,
        chunk_index: int,
        chunk_count: int,
    ) -> str:
        if not self.enabled or self.client is None:
            return ""

        cache_key = contextual_cache_key(
            model=self.model,
            doc_id=str(row["doc_id"]),
            title=str(row["title"]),
            source_url=row.get("source_url"),
            document_text=str(row["content"]),
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
        )
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        excerpt = document_excerpt(str(row["content"]), context_document_char_limit())
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=context_max_output_tokens(),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You create short retrieval context for document chunks. "
                        "Write 2-3 concise sentences that explain how the chunk fits inside the full document. "
                        "Mention the local topic, the broader document topic, and any specific entities or claims that "
                        "help lexical and semantic retrieval. Do not use bullets and do not quote the chunk verbatim."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Document title: {row['title']}\n"
                        f"Source URL: {row.get('source_url') or 'N/A'}\n"
                        f"Chunk index: {chunk_index + 1} of {chunk_count}\n\n"
                        f"Full document excerpt:\n{excerpt}\n\n"
                        f"Chunk text:\n{chunk_text}\n\n"
                        "Return only the contextual summary."
                    ),
                },
            ],
        )
        summary = normalize_inline_whitespace(response.choices[0].message.content or "")
        self.cache[cache_key] = summary
        self.cache_dirty = True
        return summary

    def flush(self) -> None:
        if self.cache_dirty:
            save_context_cache(self.cache)


def build_contextualized_body(context_summary: str, raw_body: str) -> str:
    summary = normalize_inline_whitespace(context_summary)
    if not summary:
        return raw_body
    return f"{summary}\n\n{raw_body}".strip()


def build_documents_and_nodes_from_source_rows(
    source_rows: list[dict[str, Any]],
    context_provider: Callable[[dict[str, Any], str, int, int], str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents_out: list[dict[str, Any]] = []
    nodes_out: list[dict[str, Any]] = []

    for row in source_rows:
        document_text = (row.get("content") or "").strip()
        chunks = build_document_chunks(document_text)
        chunk_count = len(chunks)
        document_token_count = estimate_token_count(document_text)

        documents_out.append(
            {
                "doc_id": row["doc_id"],
                "source_id": row["source_id"],
                "title": row["title"],
                "source_url": row.get("source_url"),
                "document_text": document_text,
                "document_token_count": document_token_count,
                "chunk_count": chunk_count,
                "section_type": row.get("section_type"),
                "matched_keywords": row.get("matched_keywords", []),
            }
        )

        for chunk_index, raw_body in enumerate(chunks):
            node_id = f"{row['doc_id']}::{chunk_index}"
            context_summary = (
                context_provider(row, raw_body, chunk_index, chunk_count)
                if context_provider is not None
                else ""
            )
            contextualized_body = build_contextualized_body(context_summary, raw_body)
            nodes_out.append(
                {
                    "node_id": node_id,
                    "doc_id": row["doc_id"],
                    "source_id": row["source_id"],
                    "title": row["title"],
                    "source_url": row.get("source_url"),
                    "body": raw_body,
                    "raw_body": raw_body,
                    "context_summary": context_summary,
                    "contextualized_body": contextualized_body,
                    "section_type": row.get("section_type"),
                    "chunk_index": chunk_index,
                    "chunk_count": chunk_count,
                    "token_count": estimate_token_count(raw_body),
                    "char_count": len(raw_body),
                }
            )

    return documents_out, nodes_out


def prepared_artifacts_are_current() -> bool:
    if not DOCUMENTS_JSONL.exists() or not NODES_JSONL.exists():
        return False

    documents = load_jsonl(DOCUMENTS_JSONL)
    nodes = load_jsonl(NODES_JSONL)
    if not documents or not nodes:
        return False

    document_keys = {
        "doc_id",
        "source_id",
        "title",
        "source_url",
        "document_text",
        "document_token_count",
        "chunk_count",
    }
    node_keys = {
        "node_id",
        "doc_id",
        "source_id",
        "title",
        "source_url",
        "body",
        "raw_body",
        "context_summary",
        "contextualized_body",
        "chunk_index",
        "chunk_count",
        "token_count",
        "char_count",
    }
    return document_keys.issubset(documents[0]) and node_keys.issubset(nodes[0])


def prepare_documents_and_nodes(force: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if DOCUMENTS_JSONL.exists() and NODES_JSONL.exists() and not force and prepared_artifacts_are_current():
        return load_jsonl(DOCUMENTS_JSONL), load_jsonl(NODES_JSONL)

    source_rows = load_phase1_records()
    provider = ContextualSummaryProvider()
    try:
        documents_out, nodes_out = build_documents_and_nodes_from_source_rows(source_rows, provider)
    finally:
        provider.flush()

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
    raw_vectors = embedder.get_text_embedding_batch([row["raw_body"] for row in nodes], show_progress=True)
    contextual_vectors = embedder.get_text_embedding_batch(
        [row["contextualized_body"] for row in nodes],
        show_progress=True,
    )

    with connect() as conn, conn.cursor() as cur:
        for row, raw_vector, contextual_vector in zip(nodes, raw_vectors, contextual_vectors, strict=False):
            cur.execute(
                """
                UPDATE kb_nodes
                SET embedding = %(embedding)s::vector,
                    contextual_embedding = %(contextual_embedding)s::vector
                WHERE node_id = %(node_id)s
                """,
                {
                    "node_id": row["node_id"],
                    "embedding": vector_literal(raw_vector),
                    "contextual_embedding": vector_literal(contextual_vector),
                },
            )
        cur.execute(
            """
            SELECT
                count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded_nodes,
                count(*) FILTER (WHERE contextual_embedding IS NOT NULL) AS contextual_embedded_nodes
            FROM kb_nodes
            """
        )
        embedded = cur.fetchone()

    return {
        "embedded_nodes": embedded["embedded_nodes"],
        "contextual_embedded_nodes": embedded["contextual_embedded_nodes"],
        "embedding_model": env_str("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
    }


def write_summary_file(payload: dict[str, Any]) -> None:
    INDEXING_SUMMARY.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def status() -> None:
    payload: dict[str, Any] = {
        "inputs": {path.name: path.exists() for path in INPUT_FILES},
        "prepared_documents": DOCUMENTS_JSONL.exists(),
        "prepared_nodes": NODES_JSONL.exists(),
        "benchmark_queries": BENCHMARK_QUERIES.exists(),
        "qrels_test": QRELS_TEST.exists(),
        "context_cache_entries": len(load_context_cache()),
    }

    if DOCUMENTS_JSONL.exists():
        docs = load_jsonl(DOCUMENTS_JSONL)
        payload["prepared_document_count"] = len(docs)
        payload["prepared_document_chunk_total"] = sum(int(row.get("chunk_count", 0)) for row in docs)
        source_breakdown: dict[str, int] = {}
        for row in docs:
            source_breakdown[row["source_id"]] = source_breakdown.get(row["source_id"], 0) + 1
        payload["source_breakdown"] = source_breakdown

    if NODES_JSONL.exists():
        nodes = load_jsonl(NODES_JSONL)
        payload["prepared_node_count"] = len(nodes)
        payload["avg_node_tokens"] = round(
            sum(int(row.get("token_count", 0)) for row in nodes) / len(nodes),
            2,
        ) if nodes else 0.0

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    (SELECT count(*) FROM kb_documents) AS documents,
                    (SELECT count(*) FROM kb_nodes) AS nodes,
                    (SELECT count(*) FROM kb_nodes WHERE embedding IS NOT NULL) AS embedded_nodes,
                    (SELECT count(*) FROM kb_nodes WHERE contextual_embedding IS NOT NULL) AS contextual_embedded_nodes
                """
            )
            row = cur.fetchone()
            payload["postgres"] = {
                "reachable": True,
                "documents": row["documents"],
                "nodes": row["nodes"],
                "embedded_nodes": row["embedded_nodes"],
                "contextual_embedded_nodes": row["contextual_embedded_nodes"],
            }
            cur.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname IN (
                    'kb_nodes_bm25_idx',
                    'kb_nodes_contextual_bm25_idx',
                    'kb_nodes_embedding_idx',
                    'kb_nodes_contextual_embedding_idx'
                  )
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
