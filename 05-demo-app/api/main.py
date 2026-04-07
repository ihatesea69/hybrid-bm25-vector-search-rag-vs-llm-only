from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PHASE1_DIR = PROJECT_ROOT / "01-data-preparation"
PHASE2_DIR = PROJECT_ROOT / "02-indexing" / "data"

PHASE1_FILES = {
    "nfcorpus": PHASE1_DIR / "nfcorpus_nutrition.jsonl",
    "crawl": PHASE1_DIR / "nutrition_crawl.jsonl",
    "pubmed": PHASE1_DIR / "pubmed_kb.jsonl",
}

PHASE2_FILES = {
    "documents": PHASE2_DIR / "index_documents.jsonl",
    "nodes": PHASE2_DIR / "index_nodes.jsonl",
    "benchmark_queries": PHASE2_DIR / "benchmark_queries.jsonl",
    "qrels": PHASE2_DIR / "benchmark_qrels_test.tsv",
}


def load_module(name: str, relative_path: str) -> ModuleType:
    module_path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


retrieval_module = load_module("medir_phase3_retrieval", "03-retrieval/retrieval.py")
evaluation_module = load_module("medir_phase4_evaluation", "04-evaluation/evaluation.py")


class QueryRequest(BaseModel):
    query_text: str = Field(alias="queryText", min_length=1, max_length=500)
    top_k: int = Field(alias="topK", default=20, ge=1, le=20)
    reranker_enabled: bool = Field(alias="rerankerEnabled", default=False)
    reranker_provider: str = Field(alias="rerankerProvider", default="cohere", min_length=1, max_length=32)
    reranker_model: str = Field(alias="rerankerModel", default="rerank-v4.0-fast", min_length=1, max_length=128)
    reranker_candidate_k: int = Field(alias="rerankerCandidateK", default=20, ge=1, le=100)

    model_config = {"populate_by_name": True}

    @field_validator("query_text")
    @classmethod
    def strip_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("queryText must not be empty.")
        return cleaned

    @field_validator("reranker_provider", "reranker_model")
    @classmethod
    def strip_reranker_fields(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Reranker field must not be empty.")
        return cleaned


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def count_tsv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    return max(len(path.read_text(encoding="utf-8").splitlines()) - 1, 0)


def phase_status(ok: bool, partial: bool = False) -> str:
    if ok:
        return "ready"
    if partial:
        return "partial"
    return "degraded"


def serialize_result_row(row: dict[str, Any]) -> dict[str, Any]:
    raw_body = (row.get("raw_body") or row.get("body") or row.get("text") or "").replace("\n", " ").strip()
    context_summary = (row.get("context_summary") or "").replace("\n", " ").strip()
    bm25_meta = row.get("bm25_meta")
    vector_meta = row.get("vector_meta")
    reranker_meta = row.get("reranker_meta")
    return {
        "nodeId": row.get("node_id"),
        "docId": row.get("doc_id"),
        "sourceId": row.get("source_id"),
        "title": row.get("title") or row.get("doc_id"),
        "score": round(float(row.get("score", 0.0)), 6),
        "snippet": raw_body[:320],
        "rawBody": raw_body,
        "contextSummary": context_summary or None,
        "sectionType": row.get("section_type"),
        "retrievalPath": row.get("retrieval_path"),
        "chunkIndex": row.get("chunk_index"),
        "chunkCount": row.get("chunk_count"),
        "tokenCount": row.get("token_count"),
        "charCount": row.get("char_count"),
        "bm25Meta": bm25_meta if isinstance(bm25_meta, dict) else None,
        "vectorMeta": vector_meta if isinstance(vector_meta, dict) else None,
        "rerankerMeta": reranker_meta if isinstance(reranker_meta, dict) else None,
    }


def serialize_citation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "citationId": row.get("citation_id"),
        "nodeId": row.get("node_id"),
        "docId": row.get("doc_id"),
        "sourceId": row.get("source_id"),
        "title": row.get("title") or row.get("doc_id"),
    }


def summarize_kb() -> dict[str, Any]:
    retrieval_status = retrieval_module.get_retrieval_status()
    evaluation_status = evaluation_module.get_evaluation_status()
    postgres = retrieval_status.get("postgres", {})
    return {
        "status": "ok" if postgres.get("reachable") else "degraded",
        "documents": postgres.get("documents", retrieval_status.get("index_document_count", 0)),
        "nodes": postgres.get("nodes", retrieval_status.get("index_node_count", 0)),
        "embeddedNodes": postgres.get("embedded_nodes", 0),
        "contextualEmbeddedNodes": postgres.get("contextual_embedded_nodes", 0),
        "retrievalRuns": postgres.get("retrieval_runs", 0),
        "answerRuns": postgres.get("answer_runs", 0),
        "availableModes": ["hybrid", "contextual_hybrid", "hybrid_rag", "contextual_hybrid_rag", "llm_only"],
        "artifacts": evaluation_status.get("artifacts", {}),
    }


def summarize_pipeline_phases() -> dict[str, Any]:
    retrieval_status = retrieval_module.get_retrieval_status()
    evaluation_status = evaluation_module.get_evaluation_status()
    system_summary = evaluation_module.load_system_summary()

    phase1_counts = {name: count_jsonl_rows(path) for name, path in PHASE1_FILES.items()}
    phase2_counts = {
        "documents": count_jsonl_rows(PHASE2_FILES["documents"]),
        "nodes": count_jsonl_rows(PHASE2_FILES["nodes"]),
        "benchmark_queries": count_jsonl_rows(PHASE2_FILES["benchmark_queries"]),
        "qrels": count_tsv_rows(PHASE2_FILES["qrels"]),
    }

    postgres = retrieval_status.get("postgres", {})
    eval_summary = system_summary.get("summary", {})

    phase1_ready = all(path.exists() for path in PHASE1_FILES.values())
    phase2_ready = all(path.exists() for path in PHASE2_FILES.values())
    phase3_ready = bool(postgres.get("reachable"))
    phase4_ready = all(evaluation_status.get("artifacts", {}).values())
    phase4_partial = any(evaluation_status.get("artifacts", {}).values())

    phases = [
        {
            "id": "phase1",
            "title": "Phase 1 · Dataset Construction",
            "status": phase_status(phase1_ready),
            "summary": "Build the nutrition-focused working corpus from benchmark, authoritative web sources, and PubMed literature.",
            "stats": [
                {"label": "NFCorpus rows", "value": str(phase1_counts["nfcorpus"])},
                {"label": "Web crawl rows", "value": str(phase1_counts["crawl"])},
                {"label": "PubMed rows", "value": str(phase1_counts["pubmed"])},
            ],
            "outputs": [
                "nfcorpus_nutrition.jsonl",
                "nutrition_crawl.jsonl",
                "pubmed_kb.jsonl",
            ],
            "details": [
                "Each JSONL row keeps only the fields used to build retrieval-ready records: ids, source, text content, keyword matches, and section type.",
                "The crawl set acts as authoritative seed material, while NFCorpus provides benchmark backbone coverage.",
                "PubMed rows add research-oriented passages for harder retrieval and answer grounding cases.",
            ],
        },
        {
            "id": "phase2",
            "title": "Phase 2 · Knowledge Base Preparation",
            "status": phase_status(phase2_ready, partial=phase2_counts["documents"] > 0 or phase2_counts["nodes"] > 0),
            "summary": "Normalize documents into retrieval-ready records, export benchmark artifacts, and prepare database-facing knowledge base files.",
            "stats": [
                {"label": "Prepared documents", "value": str(phase2_counts["documents"])},
                {"label": "Prepared nodes", "value": str(phase2_counts["nodes"])},
                {"label": "Benchmark queries", "value": str(phase2_counts["benchmark_queries"])},
                {"label": "Qrels rows", "value": str(phase2_counts["qrels"])},
            ],
            "outputs": [
                "index_documents.jsonl",
                "index_nodes.jsonl",
                "benchmark_queries.jsonl",
                "benchmark_qrels_test.tsv",
            ],
            "details": [
                "Documents are now split into adaptive chunks with overlap, and each chunk stores both raw text and contextualized text for retrieval.",
                "Benchmark artifacts are exported separately so retrieval and evaluation can run repeatedly without rebuilding Phase 1.",
                "The contextual summary for each chunk is generated from the surrounding document and cached so re-indexing does not always repeat LLM calls.",
            ],
        },
        {
            "id": "phase3",
            "title": "Phase 3 · Retrieval and Answering",
            "status": phase_status(phase3_ready, partial=bool(postgres)),
            "summary": "Run BM25, vector search, optional reranking, final fusion, and answer generation for hybrid_rag and llm_only modes.",
            "stats": [
                {"label": "Indexed docs", "value": str(postgres.get("documents", retrieval_status.get("index_document_count", 0)))},
                {"label": "Indexed nodes", "value": str(postgres.get("nodes", retrieval_status.get("index_node_count", 0)))},
                {"label": "Embedded nodes", "value": str(postgres.get("embedded_nodes", 0))},
                {"label": "Contextual embeds", "value": str(postgres.get("contextual_embedded_nodes", 0))},
                {"label": "Retrieval runs", "value": str(postgres.get("retrieval_runs", 0))},
                {"label": "Answer runs", "value": str(postgres.get("answer_runs", 0))},
            ],
            "outputs": [
                "retrieval_runs",
                "answer_runs",
                "live /demo/query responses",
            ],
            "details": [
                "Hybrid retrieval combines BM25 lexical search and pgvector semantic search, then fuses branch rankings with RRF.",
                "Contextual retrieval adds chunk-level context summaries so BM25, embeddings, and reranking operate on contextualized chunk text without replacing the raw evidence.",
                "The runtime can optionally apply Cohere reranking before final fusion, while keeping fallback to the original flow.",
                "Hybrid RAG, contextual_hybrid_rag, and llm_only are all stored side by side for comparison.",
            ],
        },
        {
            "id": "phase4",
            "title": "Phase 4 · Evaluation",
            "status": phase_status(phase4_ready, partial=phase4_partial),
            "summary": "Score retrieval quality and answer quality, then aggregate outputs into dashboard-friendly summaries and failure cases.",
            "stats": [
                {"label": "Retrieval rows", "value": str(eval_summary.get("retrieval_hybrid", {}).get("rows", "N/A"))},
                {"label": "Recall@10", "value": str(eval_summary.get("retrieval_hybrid", {}).get("recall@10", "N/A"))},
                {"label": "Contextual Recall@10", "value": str(eval_summary.get("retrieval_contextual_hybrid", {}).get("recall@10", "N/A"))},
                {"label": "MRR@10", "value": str(eval_summary.get("retrieval_hybrid", {}).get("mrr@10", "N/A"))},
                {"label": "NDCG@10", "value": str(eval_summary.get("retrieval_hybrid", {}).get("ndcg@10", "N/A"))},
                {"label": "Hybrid correctness", "value": str(eval_summary.get("answer_hybrid_rag", {}).get("correctness", "N/A"))},
                {"label": "Contextual correctness", "value": str(eval_summary.get("answer_contextual_hybrid_rag", {}).get("correctness", "N/A"))},
                {"label": "Pairwise win rate", "value": str(eval_summary.get("pairwise_contextual_hybrid_rag_vs_hybrid_rag", {}).get("left_win_rate", "N/A"))},
            ],
            "outputs": [
                "retrieval_metrics_hybrid.json",
                "answer_eval_hybrid_rag.json",
                "answer_eval_llm_only.json",
                "pairwise_hybrid_rag_vs_llm_only.json",
                "system_summary.json",
            ],
            "details": [
                "Retrieval metrics stay benchmark-driven: Recall@10, MRR@10, NDCG@10, and MAP are computed from qrels.",
                "Answer-level metrics and pairwise comparison are stored separately so the dashboard can show both averages and failure cases.",
                "The demo reads system_summary.json directly, so this phase is the reporting layer that turns raw experiment logs into presentation-ready signals.",
            ],
        },
    ]
    return {"updatedAt": utc_now(), "phases": phases}


app = FastAPI(
    title="MedIR Demo API",
    version="1.0.0",
    description="Local demo API for the MedIR benchmark app.",
)


@app.get("/health")
def health() -> dict[str, Any]:
    retrieval_status = retrieval_module.get_retrieval_status()
    evaluation_status = evaluation_module.get_evaluation_status()
    postgres = retrieval_status.get("postgres", {})
    is_ok = bool(postgres.get("reachable"))
    return {
        "ok": is_ok,
        "timestamp": utc_now(),
        "retrieval": retrieval_status,
        "evaluation": evaluation_status,
    }


@app.get("/kb/summary")
def kb_summary() -> dict[str, Any]:
    return summarize_kb()


@app.post("/demo/query")
def demo_query(payload: QueryRequest) -> dict[str, Any]:
    bundle = retrieval_module.run_demo_bundle(
        payload.query_text,
        payload.top_k,
        reranker_config={
            "enabled": payload.reranker_enabled,
            "provider": payload.reranker_provider,
            "model": payload.reranker_model,
            "candidate_k": payload.reranker_candidate_k,
        },
    )
    hybrid_results = [serialize_result_row(row) for row in bundle["hybrid"]["results"]]
    contextual_results = [serialize_result_row(row) for row in bundle["contextual_hybrid"]["results"]]
    evidence_bundle = [serialize_result_row(row) for row in bundle["hybrid_rag"]["evidence_bundle"]]
    contextual_evidence_bundle = [serialize_result_row(row) for row in bundle["contextual_hybrid_rag"]["evidence_bundle"]]
    citations = [serialize_citation(row) for row in bundle["hybrid_rag"]["citations"]]
    contextual_citations = [serialize_citation(row) for row in bundle["contextual_hybrid_rag"]["citations"]]
    return {
        "batchId": bundle["batch_id"],
        "queryText": bundle["query_text"],
        "topK": bundle["top_k"],
        "hybrid": {
            "mode": "hybrid",
            "results": hybrid_results,
            "config": bundle["hybrid"]["config"],
        },
        "contextualHybrid": {
            "mode": "contextual_hybrid",
            "results": contextual_results,
            "config": bundle["contextual_hybrid"]["config"],
        },
        "hybridRag": {
            "mode": "hybrid_rag",
            "answerText": bundle["hybrid_rag"]["answer"],
            "citations": citations,
            "evidenceBundle": evidence_bundle,
        },
        "contextualHybridRag": {
            "mode": "contextual_hybrid_rag",
            "answerText": bundle["contextual_hybrid_rag"]["answer"],
            "citations": contextual_citations,
            "evidenceBundle": contextual_evidence_bundle,
        },
        "llmOnly": {
            "mode": "llm_only",
            "answerText": bundle["llm_only"]["answer"],
        },
        "timingsMs": bundle["timings_ms"],
    }


@app.get("/demo/summary")
def demo_summary() -> dict[str, Any]:
    return evaluation_module.load_system_summary()


@app.get("/demo/phases")
def demo_phases() -> dict[str, Any]:
    return summarize_pipeline_phases()


@app.get("/demo/failure-cases")
def demo_failure_cases() -> dict[str, Any]:
    payload = evaluation_module.load_system_summary()
    return payload.get("failure_cases", {})
