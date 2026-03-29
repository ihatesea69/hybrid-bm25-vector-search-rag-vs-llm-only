from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    top_k: int = Field(alias="topK", default=5, ge=1, le=10)

    model_config = {"populate_by_name": True}

    @field_validator("query_text")
    @classmethod
    def strip_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("queryText must not be empty.")
        return cleaned


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def serialize_result_row(row: dict[str, Any]) -> dict[str, Any]:
    body = (row.get("body") or row.get("text") or "").replace("\n", " ").strip()
    return {
        "nodeId": row.get("node_id"),
        "docId": row.get("doc_id"),
        "sourceId": row.get("source_id"),
        "title": row.get("title") or row.get("doc_id"),
        "score": round(float(row.get("score", 0.0)), 6),
        "snippet": body[:320],
        "sectionType": row.get("section_type"),
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
        "retrievalRuns": postgres.get("retrieval_runs", 0),
        "answerRuns": postgres.get("answer_runs", 0),
        "availableModes": ["hybrid", "hybrid_rag", "llm_only"],
        "artifacts": evaluation_status.get("artifacts", {}),
    }


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
    bundle = retrieval_module.run_demo_bundle(payload.query_text, payload.top_k)
    hybrid_results = [serialize_result_row(row) for row in bundle["hybrid"]["results"]]
    evidence_bundle = [serialize_result_row(row) for row in bundle["hybrid_rag"]["evidence_bundle"]]
    citations = [serialize_citation(row) for row in bundle["hybrid_rag"]["citations"]]
    return {
        "batchId": bundle["batch_id"],
        "queryText": bundle["query_text"],
        "topK": bundle["top_k"],
        "hybrid": {
            "mode": "hybrid",
            "results": hybrid_results,
        },
        "hybridRag": {
            "mode": "hybrid_rag",
            "answerText": bundle["hybrid_rag"]["answer"],
            "citations": citations,
            "evidenceBundle": evidence_bundle,
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


@app.get("/demo/failure-cases")
def demo_failure_cases() -> dict[str, Any]:
    payload = evaluation_module.load_system_summary()
    return payload.get("failure_cases", {})
