from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.use("Agg")


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "eda"

RAW_CORPORA = {
    "nfcorpus_nutrition": ROOT / "01-data-preparation" / "nfcorpus_nutrition.jsonl",
    "nutrition_crawl": ROOT / "01-data-preparation" / "nutrition_crawl.jsonl",
    "pubmed_kb": ROOT / "01-data-preparation" / "pubmed_kb.jsonl",
}

BENCHMARK_QUERIES = ROOT / "02-indexing" / "data" / "benchmark_queries.jsonl"
BENCHMARK_QRELS = ROOT / "02-indexing" / "data" / "benchmark_qrels_test.tsv"
INDEX_DOCUMENTS = ROOT / "02-indexing" / "data" / "index_documents.jsonl"
INDEX_NODES = ROOT / "02-indexing" / "data" / "index_nodes.jsonl"
RETRIEVAL_RESULTS_DIR = ROOT / "03-retrieval" / "results"
EVALUATION_RESULTS_DIR = ROOT / "04-evaluation" / "results"


@dataclass
class ReportArtifacts:
    markdown_path: Path
    summary_path: Path
    image_paths: list[Path]


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def words(text: str | None) -> int:
    return len((text or "").split())


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    series = sorted(values)
    if len(series) == 1:
        return float(series[0])
    idx = (len(series) - 1) * quantile
    lower = int(idx)
    upper = min(lower + 1, len(series) - 1)
    weight = idx - lower
    return float(series[lower] * (1 - weight) + series[upper] * weight)


def describe_numeric(values: list[float | int]) -> dict[str, float | int]:
    if not values:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "min": 0.0,
            "p95": 0.0,
            "max": 0.0,
        }
    numeric = [float(v) for v in values]
    return {
        "count": len(numeric),
        "mean": sum(numeric) / len(numeric),
        "median": float(median(numeric)),
        "min": float(min(numeric)),
        "p95": percentile(numeric, 0.95),
        "max": float(max(numeric)),
    }


def fmt_num(value: float | int, digits: int = 2) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.{digits}f}"


def markdown_table(rows: list[dict], headers: list[str]) -> str:
    if not rows:
        return "_No data._"
    header_line = "| " + " | ".join(headers) + " |"
    divider_line = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(col, "")) for col in headers) + " |")
    return "\n".join([header_line, divider_line, *body])


def save_bar_chart(
    values: dict[str, float | int],
    path: Path,
    title: str,
    ylabel: str,
    max_items: int | None = None,
    rotate: int = 25,
) -> None:
    items = list(values.items())
    if max_items is not None:
        items = items[:max_items]
    labels = [item[0] for item in items]
    series = [item[1] for item in items]
    plt.figure(figsize=(10, 5))
    plt.bar(labels, series, color="#2563eb")
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=rotate, ha="right")
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def save_histogram(values: list[float | int], path: Path, title: str, xlabel: str) -> None:
    plt.figure(figsize=(10, 5))
    bins = min(20, max(5, len(set(values)) if values else 5))
    plt.hist(values, bins=bins, color="#059669", edgecolor="white")
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel("Count")
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def save_boxplot(groups: dict[str, list[float | int]], path: Path, title: str, ylabel: str) -> None:
    labels = list(groups.keys())
    values = [groups[label] for label in labels]
    plt.figure(figsize=(10, 5))
    plt.boxplot(values, tick_labels=labels, patch_artist=True)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xticks(rotation=20, ha="right")
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def save_retrieval_metric_histograms(per_query: pd.DataFrame, path: Path) -> None:
    metric_names = ["recall@10", "mrr@10", "ndcg@10", "map"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for axis, metric in zip(axes.flatten(), metric_names):
        axis.hist(per_query[metric], bins=20, color="#7c3aed", edgecolor="white")
        axis.set_title(metric)
        axis.set_xlabel("Score")
        axis.set_ylabel("Queries")
    fig.suptitle("Retrieval Metric Distributions")
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def profile_raw_corpora() -> tuple[pd.DataFrame, dict]:
    corpus_frames: list[pd.DataFrame] = []
    inventory_rows: list[dict] = []

    for corpus_name, path in RAW_CORPORA.items():
        frame = pd.DataFrame(read_jsonl(path))
        frame["corpus_name"] = corpus_name
        frame["title_words"] = frame["title"].map(words)
        frame["content_words"] = frame["content"].map(words)
        frame["keyword_count"] = frame["matched_keywords"].map(lambda items: len(items or []))
        frame["has_url"] = frame["source_url"].map(bool)
        corpus_frames.append(frame)

        content_stats = describe_numeric(frame["content_words"].tolist())
        inventory_rows.append(
            {
                "dataset": corpus_name,
                "rows": int(len(frame)),
                "unique_doc_ids": int(frame["doc_id"].nunique()),
                "unique_sources": int(frame["source_id"].nunique()),
                "avg_content_words": round(content_stats["mean"], 1),
                "median_content_words": round(content_stats["median"], 1),
                "p95_content_words": round(content_stats["p95"], 1),
                "missing_titles": int((frame["title"].fillna("").str.strip() == "").sum()),
                "missing_urls": int((~frame["has_url"]).sum()),
            }
        )

    combined = pd.concat(corpus_frames, ignore_index=True)
    combined["normalized_title"] = combined["title"].fillna("").str.lower().str.strip()

    duplicate_doc_ids = int(combined["doc_id"].duplicated().sum())
    duplicate_titles = int(combined["normalized_title"].duplicated().sum())
    source_distribution = combined["source_id"].value_counts().to_dict()
    section_distribution = combined["section_type"].value_counts().head(12).to_dict()
    keyword_distribution = Counter(
        keyword
        for keywords in combined["matched_keywords"]
        for keyword in (keywords or [])
    )

    summary = {
        "inventory": inventory_rows,
        "total_rows": int(len(combined)),
        "unique_doc_ids": int(combined["doc_id"].nunique()),
        "duplicate_doc_ids": duplicate_doc_ids,
        "duplicate_titles": duplicate_titles,
        "source_distribution": source_distribution,
        "section_distribution_top12": section_distribution,
        "keyword_distribution_top15": dict(keyword_distribution.most_common(15)),
        "content_word_stats": describe_numeric(combined["content_words"].tolist()),
        "keyword_count_stats": describe_numeric(combined["keyword_count"].tolist()),
        "longest_documents": combined.nlargest(10, "content_words")[
            ["doc_id", "corpus_name", "source_id", "section_type", "content_words", "title"]
        ].to_dict(orient="records"),
        "dataframe": combined,
    }
    return combined, summary


def profile_benchmark(raw_docs: pd.DataFrame) -> dict:
    queries = pd.DataFrame(read_jsonl(BENCHMARK_QUERIES))
    queries["query_words"] = queries["text"].map(words)

    qrels = pd.read_csv(BENCHMARK_QRELS, sep="\t")
    qrel_per_query = (
        qrels.groupby("query-id")
        .agg(
            judged_docs=("corpus-id", "size"),
            unique_docs=("corpus-id", "nunique"),
            total_relevance=("score", "sum"),
            max_relevance=("score", "max"),
        )
        .reset_index()
    )

    qrels_with_source = qrels.merge(
        raw_docs[["doc_id", "source_id", "corpus_name"]],
        left_on="corpus-id",
        right_on="doc_id",
        how="left",
    )

    judged_source_distribution = (
        qrels_with_source["source_id"].fillna("missing_in_raw").value_counts().to_dict()
    )
    missing_qrel_docs = int(qrels_with_source["source_id"].isna().sum())

    return {
        "queries_rows": int(len(queries)),
        "unique_queries": int(queries["_id"].nunique()),
        "query_word_stats": describe_numeric(queries["query_words"].tolist()),
        "qrels_rows": int(len(qrels)),
        "qrels_unique_docs": int(qrels["corpus-id"].nunique()),
        "qrels_per_query_stats": describe_numeric(qrel_per_query["judged_docs"].tolist()),
        "missing_queries_in_qrels": int(
            len(set(queries["_id"]) - set(qrel_per_query["query-id"]))
        ),
        "judged_source_distribution": judged_source_distribution,
        "missing_qrel_docs_in_raw": missing_qrel_docs,
        "top_queries_by_judged_docs": qrel_per_query.nlargest(10, "judged_docs").to_dict(orient="records"),
        "queries_df": queries,
        "qrels_df": qrels,
        "qrel_per_query_df": qrel_per_query,
    }


def profile_indexing(raw_docs: pd.DataFrame) -> dict:
    index_documents = pd.DataFrame(read_jsonl(INDEX_DOCUMENTS))
    index_nodes = pd.DataFrame(read_jsonl(INDEX_NODES))
    index_nodes["body_words"] = index_nodes["body"].map(words)

    raw_doc_ids = set(raw_docs["doc_id"])
    indexed_doc_ids = set(index_documents["doc_id"])
    node_per_doc = index_nodes.groupby("doc_id").size()

    return {
        "index_documents_rows": int(len(index_documents)),
        "index_nodes_rows": int(len(index_nodes)),
        "unique_index_documents": int(index_documents["doc_id"].nunique()),
        "unique_index_nodes": int(index_nodes["node_id"].nunique()),
        "node_per_doc_stats": describe_numeric(node_per_doc.tolist()),
        "body_word_stats": describe_numeric(index_nodes["body_words"].tolist()),
        "source_distribution": index_nodes["source_id"].value_counts().to_dict(),
        "section_distribution_top12": index_nodes["section_type"].value_counts().head(12).to_dict(),
        "missing_from_index": sorted(raw_doc_ids - indexed_doc_ids),
        "unexpected_index_docs": sorted(indexed_doc_ids - raw_doc_ids),
    }


def profile_retrieval_artifacts() -> dict:
    retrieval_rows: list[dict] = []
    retrieval_hits: list[dict] = []
    answer_rows: list[dict] = []

    for path in sorted(RETRIEVAL_RESULTS_DIR.glob("retrieval_*_hybrid.jsonl")):
        for row in read_jsonl(path):
            results = row.get("results", [])
            retrieval_rows.append(
                {
                    "file": path.name,
                    "batch_id": row.get("batch_id"),
                    "mode": row.get("mode"),
                    "query_id": row.get("query_id"),
                    "query_text": row.get("query_text"),
                    "result_count": len(results),
                    "top_k": (row.get("config") or {}).get("top_k"),
                    "top1_source_id": results[0].get("source_id") if results else None,
                    "top1_score": results[0].get("score") if results else None,
                }
            )
            for rank, result in enumerate(results, start=1):
                retrieval_hits.append(
                    {
                        "batch_id": row.get("batch_id"),
                        "query_id": row.get("query_id"),
                        "rank": rank,
                        "source_id": result.get("source_id"),
                        "section_type": result.get("section_type"),
                        "score": result.get("score"),
                    }
                )

    for path in sorted(RETRIEVAL_RESULTS_DIR.glob("answers_*.jsonl")):
        for row in read_jsonl(path):
            citations = row.get("citations") or []
            evidence_bundle = row.get("evidence_bundle") or []
            answer_rows.append(
                {
                    "file": path.name,
                    "batch_id": row.get("batch_id"),
                    "mode": row.get("mode"),
                    "query_id": row.get("query_id"),
                    "answer_words": words(row.get("answer_text")),
                    "citation_count": len(citations),
                    "evidence_count": len(evidence_bundle),
                }
            )

    retrieval_df = pd.DataFrame(retrieval_rows)
    retrieval_hits_df = pd.DataFrame(retrieval_hits)
    answers_df = pd.DataFrame(answer_rows)

    batch_rows = []
    if not retrieval_df.empty:
        batch_rows = (
            retrieval_df.groupby("batch_id")
            .agg(rows=("query_id", "size"), unique_queries=("query_id", "nunique"))
            .sort_values(["rows", "unique_queries"], ascending=False)
            .head(10)
            .reset_index()
            .to_dict(orient="records")
        )

    repeated_queries = {}
    if not retrieval_df.empty:
        repeated_queries = (
            retrieval_df["query_id"].value_counts().loc[lambda series: series > 1].head(10).to_dict()
        )

    answer_summary_by_mode = []
    if not answers_df.empty:
        answer_summary_by_mode = (
            answers_df.groupby("mode")
            .agg(
                rows=("query_id", "size"),
                unique_queries=("query_id", "nunique"),
                avg_answer_words=("answer_words", "mean"),
                avg_citations=("citation_count", "mean"),
                avg_evidence_count=("evidence_count", "mean"),
            )
            .round(2)
            .reset_index()
            .to_dict(orient="records")
        )

    return {
        "retrieval_rows": int(len(retrieval_df)),
        "retrieval_unique_queries": int(retrieval_df["query_id"].nunique()) if not retrieval_df.empty else 0,
        "retrieval_unique_batches": int(retrieval_df["batch_id"].nunique()) if not retrieval_df.empty else 0,
        "result_count_stats": describe_numeric(retrieval_df["result_count"].tolist()) if not retrieval_df.empty else describe_numeric([]),
        "top1_source_distribution": retrieval_df["top1_source_id"].value_counts().to_dict() if not retrieval_df.empty else {},
        "retrieved_hit_source_distribution": retrieval_hits_df["source_id"].value_counts().to_dict() if not retrieval_hits_df.empty else {},
        "retrieved_section_distribution_top12": retrieval_hits_df["section_type"].value_counts().head(12).to_dict() if not retrieval_hits_df.empty else {},
        "largest_batches_top10": batch_rows,
        "repeated_queries_top10": repeated_queries,
        "answers_rows": int(len(answers_df)),
        "answers_unique_batches": int(answers_df["batch_id"].nunique()) if not answers_df.empty else 0,
        "answer_summary_by_mode": answer_summary_by_mode,
        "retrieval_df": retrieval_df,
        "answers_df": answers_df,
    }


def extract_eval_metric(score_block: dict | float | int | None) -> float | None:
    if isinstance(score_block, (float, int)):
        return float(score_block)
    if isinstance(score_block, dict):
        raw = score_block.get("score")
        if isinstance(raw, (float, int)):
            return float(raw)
    return None


def profile_evaluation_results() -> dict:
    retrieval_metrics = json.loads((EVALUATION_RESULTS_DIR / "retrieval_metrics_hybrid.json").read_text(encoding="utf-8"))
    system_summary = json.loads((EVALUATION_RESULTS_DIR / "system_summary.json").read_text(encoding="utf-8"))
    answer_eval_hybrid = json.loads((EVALUATION_RESULTS_DIR / "answer_eval_hybrid_rag.json").read_text(encoding="utf-8"))
    answer_eval_llm = json.loads((EVALUATION_RESULTS_DIR / "answer_eval_llm_only.json").read_text(encoding="utf-8"))
    pairwise = json.loads((EVALUATION_RESULTS_DIR / "pairwise_hybrid_rag_vs_llm_only.json").read_text(encoding="utf-8"))

    per_query = pd.DataFrame(retrieval_metrics["per_query"])

    answer_eval_rows = []
    for mode_name, payload in [("hybrid_rag", answer_eval_hybrid), ("llm_only", answer_eval_llm)]:
        for row in payload:
            answer_eval_rows.append(
                {
                    "mode": mode_name,
                    "query_id": row.get("query_id"),
                    "faithfulness": extract_eval_metric(row.get("faithfulness")),
                    "correctness": extract_eval_metric(row.get("correctness")),
                    "relevancy": extract_eval_metric(row.get("relevancy")),
                }
            )
    answer_eval_df = pd.DataFrame(answer_eval_rows)

    return {
        "retrieval_summary": retrieval_metrics["summary"],
        "retrieval_zero_recall_queries": int((per_query["recall@10"] == 0).sum()),
        "retrieval_zero_mrr_queries": int((per_query["mrr@10"] == 0).sum()),
        "worst_queries_top10": per_query.nsmallest(10, ["recall@10", "ndcg@10"])[
            ["query_id", "query_text", "recall@10", "mrr@10", "ndcg@10", "map"]
        ].to_dict(orient="records"),
        "best_queries_top10": per_query.nlargest(10, ["recall@10", "ndcg@10"])[
            ["query_id", "query_text", "recall@10", "mrr@10", "ndcg@10", "map"]
        ].to_dict(orient="records"),
        "answer_eval_by_mode": answer_eval_df.groupby("mode").mean(numeric_only=True).round(4).reset_index().to_dict(orient="records"),
        "pairwise_rows": len(pairwise),
        "pairwise_preferred_left_rate": sum(1 for row in pairwise if row.get("preferred_left")) / len(pairwise) if pairwise else 0.0,
        "system_summary": system_summary,
        "per_query_df": per_query,
    }


def build_findings(raw_summary: dict, benchmark_summary: dict, indexing_summary: dict, retrieval_summary: dict, evaluation_summary: dict) -> list[str]:
    findings: list[str] = []
    raw_rows = raw_summary["total_rows"]
    nfcorpus_rows = next(row["rows"] for row in raw_summary["inventory"] if row["dataset"] == "nfcorpus_nutrition")
    findings.append(
        f"NFCorpus chiếm {nfcorpus_rows}/{raw_rows} tài liệu nguồn ({nfcorpus_rows / raw_rows:.1%}), nên corpus hiện tại lệch mạnh về BEIR thay vì các nguồn authoritative mới crawl."
    )

    judged_sources = benchmark_summary["judged_source_distribution"]
    judged_total = sum(judged_sources.values())
    if judged_total:
        beir_share = judged_sources.get("beir_nfcorpus", 0) / judged_total
        findings.append(
            f"Qrels benchmark tập trung vào `beir_nfcorpus` ({judged_sources.get('beir_nfcorpus', 0)}/{judged_total}, {beir_share:.1%}), nên offline retrieval metrics hầu như không thưởng cho tài liệu MedlinePlus/PubMed bổ sung."
        )

    node_stats = indexing_summary["node_per_doc_stats"]
    if node_stats["max"] <= 1:
        findings.append(
            "Index hiện ở mức 1 node cho mỗi document; chưa có chunking/passage split, nên độ hạt retrieval vẫn là tài liệu đầy đủ."
        )

    answer_modes = retrieval_summary["answer_summary_by_mode"]
    if answer_modes:
        llm_only = next((row for row in answer_modes if row["mode"] == "llm_only"), None)
        hybrid = next((row for row in answer_modes if row["mode"] == "hybrid_rag"), None)
        if llm_only and hybrid:
            findings.append(
                f"Trong retrieval artifacts hiện có, `hybrid_rag` dùng trung bình {hybrid['avg_citations']:.2f} citation/answer, còn `llm_only` là {llm_only['avg_citations']:.2f}, phù hợp với thiết kế hai chế độ trả lời."
            )

    answer_eval_summary = evaluation_summary["system_summary"]["summary"]
    if answer_eval_summary["answer_hybrid_rag"]["rows"] <= 1 or answer_eval_summary["answer_llm_only"]["rows"] <= 1:
        findings.append(
            "Answer evaluation hiện quá mỏng: mỗi mode mới có 1 mẫu được chấm trong `04-evaluation/results`, nên chưa đủ để kết luận ổn định về chất lượng trả lời."
        )

    return findings


def render_report(
    raw_summary: dict,
    benchmark_summary: dict,
    indexing_summary: dict,
    retrieval_summary: dict,
    evaluation_summary: dict,
    findings: list[str],
) -> str:
    raw_inventory_table = markdown_table(
        [
            {
                "dataset": row["dataset"],
                "rows": fmt_num(row["rows"]),
                "unique_doc_ids": fmt_num(row["unique_doc_ids"]),
                "avg_words": fmt_num(row["avg_content_words"]),
                "median_words": fmt_num(row["median_content_words"]),
                "p95_words": fmt_num(row["p95_content_words"]),
            }
            for row in raw_summary["inventory"]
        ],
        ["dataset", "rows", "unique_doc_ids", "avg_words", "median_words", "p95_words"],
    )

    longest_docs_table = markdown_table(
        [
            {
                "doc_id": row["doc_id"],
                "corpus": row["corpus_name"],
                "source": row["source_id"],
                "section_type": row["section_type"],
                "content_words": fmt_num(row["content_words"]),
                "title": row["title"][:90] + ("..." if len(row["title"]) > 90 else ""),
            }
            for row in raw_summary["longest_documents"][:8]
        ],
        ["doc_id", "corpus", "source", "section_type", "content_words", "title"],
    )

    qrels_top_queries_table = markdown_table(
        [
            {
                "query_id": row["query-id"],
                "judged_docs": fmt_num(row["judged_docs"]),
                "total_relevance": fmt_num(row["total_relevance"]),
                "max_relevance": fmt_num(row["max_relevance"]),
            }
            for row in benchmark_summary["top_queries_by_judged_docs"][:8]
        ],
        ["query_id", "judged_docs", "total_relevance", "max_relevance"],
    )

    retrieval_batches_table = markdown_table(
        [
            {
                "batch_id": row["batch_id"],
                "rows": fmt_num(row["rows"]),
                "unique_queries": fmt_num(row["unique_queries"]),
            }
            for row in retrieval_summary["largest_batches_top10"][:8]
        ],
        ["batch_id", "rows", "unique_queries"],
    )

    answer_mode_table = markdown_table(
        [
            {
                "mode": row["mode"],
                "rows": fmt_num(row["rows"]),
                "unique_queries": fmt_num(row["unique_queries"]),
                "avg_answer_words": fmt_num(row["avg_answer_words"]),
                "avg_citations": fmt_num(row["avg_citations"]),
                "avg_evidence_count": fmt_num(row["avg_evidence_count"]),
            }
            for row in retrieval_summary["answer_summary_by_mode"]
        ],
        ["mode", "rows", "unique_queries", "avg_answer_words", "avg_citations", "avg_evidence_count"],
    )

    answer_eval_table = markdown_table(
        [
            {
                "mode": row["mode"],
                "faithfulness": fmt_num(row["faithfulness"], 4),
                "correctness": fmt_num(row["correctness"], 4),
                "relevancy": fmt_num(row["relevancy"], 4),
            }
            for row in evaluation_summary["answer_eval_by_mode"]
        ],
        ["mode", "faithfulness", "correctness", "relevancy"],
    )

    retrieval_eval_summary = evaluation_summary["system_summary"]["summary"]["retrieval_hybrid"]

    report_lines = [
        "# Project EDA Report",
        "",
        f"Generated at: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Executive Summary",
        f"- Total raw corpus rows: **{fmt_num(raw_summary['total_rows'])}** across 3 source files.",
        f"- Indexed documents/nodes: **{fmt_num(indexing_summary['index_documents_rows'])} / {fmt_num(indexing_summary['index_nodes_rows'])}**.",
        f"- Benchmark coverage: **{fmt_num(benchmark_summary['queries_rows'])} queries** and **{fmt_num(benchmark_summary['qrels_rows'])} qrels**.",
        f"- Hybrid retrieval evaluation batch `{retrieval_eval_summary['batch_id']}` scored **Recall@10={retrieval_eval_summary['recall@10']:.4f}**, **MRR@10={retrieval_eval_summary['mrr@10']:.4f}**, **nDCG@10={retrieval_eval_summary['ndcg@10']:.4f}**, **MAP={retrieval_eval_summary['map']:.4f}**.",
        f"- Retrieval artifact inventory: **{fmt_num(retrieval_summary['retrieval_rows'])} retrieval rows**, **{fmt_num(retrieval_summary['retrieval_unique_batches'])} batches**, **{fmt_num(retrieval_summary['answers_rows'])} answer rows**.",
        "",
        "## Key Findings",
        *[f"- {finding}" for finding in findings],
        "",
        "## 1. Raw Corpus Inventory",
        raw_inventory_table,
        "",
        f"- Unique doc_ids: **{fmt_num(raw_summary['unique_doc_ids'])}**",
        f"- Duplicate doc_ids across corpora: **{fmt_num(raw_summary['duplicate_doc_ids'])}**",
        f"- Duplicate normalized titles across corpora: **{fmt_num(raw_summary['duplicate_titles'])}**",
        f"- Overall avg content length: **{fmt_num(raw_summary['content_word_stats']['mean'])} words**",
        "",
        "![Corpus size](./corpus_documents.png)",
        "",
        "![Document length by corpus](./document_word_count_boxplot.png)",
        "",
        "### Top Sources",
        markdown_table(
            [{"source_id": key, "rows": fmt_num(value)} for key, value in raw_summary["source_distribution"].items()],
            ["source_id", "rows"],
        ),
        "",
        "### Top Section Types",
        markdown_table(
            [{"section_type": key, "rows": fmt_num(value)} for key, value in raw_summary["section_distribution_top12"].items()],
            ["section_type", "rows"],
        ),
        "",
        "![Top section types](./section_type_top12.png)",
        "",
        "### Top Matched Keywords",
        markdown_table(
            [{"keyword": key, "rows": fmt_num(value)} for key, value in raw_summary["keyword_distribution_top15"].items()],
            ["keyword", "rows"],
        ),
        "",
        "### Longest Documents",
        longest_docs_table,
        "",
        "## 2. Benchmark Queries and Qrels",
        f"- Query count: **{fmt_num(benchmark_summary['queries_rows'])}**",
        f"- Average query length: **{fmt_num(benchmark_summary['query_word_stats']['mean'])} words**",
        f"- Qrel rows: **{fmt_num(benchmark_summary['qrels_rows'])}**",
        f"- Unique judged documents: **{fmt_num(benchmark_summary['qrels_unique_docs'])}**",
        f"- Average judged docs/query: **{fmt_num(benchmark_summary['qrels_per_query_stats']['mean'])}**",
        f"- Queries missing qrels: **{fmt_num(benchmark_summary['missing_queries_in_qrels'])}**",
        "",
        "![Judged docs per query](./qrels_per_query_hist.png)",
        "",
        "### Judged Source Distribution",
        markdown_table(
            [{"source_id": key, "rows": fmt_num(value)} for key, value in benchmark_summary["judged_source_distribution"].items()],
            ["source_id", "rows"],
        ),
        "",
        "### Queries With Most Judged Documents",
        qrels_top_queries_table,
        "",
        "## 3. Indexing Layer",
        f"- `index_documents.jsonl` rows: **{fmt_num(indexing_summary['index_documents_rows'])}**",
        f"- `index_nodes.jsonl` rows: **{fmt_num(indexing_summary['index_nodes_rows'])}**",
        f"- Average nodes/document: **{fmt_num(indexing_summary['node_per_doc_stats']['mean'])}**",
        f"- Average node body length: **{fmt_num(indexing_summary['body_word_stats']['mean'])} words**",
        f"- Missing raw docs in index: **{fmt_num(len(indexing_summary['missing_from_index']))}**",
        f"- Unexpected indexed docs not found in raw corpus: **{fmt_num(len(indexing_summary['unexpected_index_docs']))}**",
        "",
        "### Indexed Source Distribution",
        markdown_table(
            [{"source_id": key, "rows": fmt_num(value)} for key, value in indexing_summary["source_distribution"].items()],
            ["source_id", "rows"],
        ),
        "",
        "## 4. Retrieval Artifact Inventory",
        f"- Retrieval rows parsed from `03-retrieval/results`: **{fmt_num(retrieval_summary['retrieval_rows'])}**",
        f"- Unique retrieval queries across artifacts: **{fmt_num(retrieval_summary['retrieval_unique_queries'])}**",
        f"- Unique retrieval batches: **{fmt_num(retrieval_summary['retrieval_unique_batches'])}**",
        f"- Average retrieved hits per row: **{fmt_num(retrieval_summary['result_count_stats']['mean'])}**",
        "",
        "### Largest Retrieval Batches",
        retrieval_batches_table,
        "",
        "### Top-1 Source Distribution",
        markdown_table(
            [{"source_id": key, "rows": fmt_num(value)} for key, value in retrieval_summary["top1_source_distribution"].items()],
            ["source_id", "rows"],
        ),
        "",
        "### Answer Artifact Summary",
        answer_mode_table,
        "",
        "## 5. Evaluation Results",
        f"- Zero-recall queries in evaluated hybrid batch: **{fmt_num(evaluation_summary['retrieval_zero_recall_queries'])}** / {fmt_num(len(evaluation_summary['per_query_df']))}",
        f"- Zero-MRR queries in evaluated hybrid batch: **{fmt_num(evaluation_summary['retrieval_zero_mrr_queries'])}** / {fmt_num(len(evaluation_summary['per_query_df']))}",
        f"- Pairwise samples: **{fmt_num(evaluation_summary['pairwise_rows'])}**, preferred-left rate: **{fmt_num(evaluation_summary['pairwise_preferred_left_rate'], 4)}**",
        "",
        "![Retrieval metric distributions](./retrieval_metric_histograms.png)",
        "",
        "### Answer Evaluation Means",
        answer_eval_table,
        "",
        "### Worst Retrieval Queries",
        markdown_table(
            [
                {
                    "query_id": row["query_id"],
                    "recall@10": fmt_num(row["recall@10"], 4),
                    "mrr@10": fmt_num(row["mrr@10"], 4),
                    "ndcg@10": fmt_num(row["ndcg@10"], 4),
                    "map": fmt_num(row["map"], 4),
                    "query_text": row["query_text"][:70] + ("..." if len(row["query_text"]) > 70 else ""),
                }
                for row in evaluation_summary["worst_queries_top10"][:8]
            ],
            ["query_id", "recall@10", "mrr@10", "ndcg@10", "map", "query_text"],
        ),
        "",
        "## 6. Recommendations",
        "- Tách riêng hai lớp đánh giá: benchmark-based retrieval metrics cho NFCorpus và answer quality / source-grounding metrics cho corpus mở rộng MedlinePlus + PubMed.",
        "- Bổ sung chunking ở `index_nodes.jsonl` để retrieval hoạt động ở mức passage thay vì full document.",
        "- Mở rộng answer evaluation lên toàn bộ batch thay vì 1 mẫu/mode để kết luận so sánh có ý nghĩa thống kê hơn.",
        "- Dọn `03-retrieval/results` theo run manifest hoặc đánh dấu batch canonical để tránh trộn partial runs với full benchmark runs.",
        "",
    ]
    return "\n".join(report_lines)


def generate_report() -> ReportArtifacts:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    raw_docs, raw_summary = profile_raw_corpora()
    benchmark_summary = profile_benchmark(raw_docs)
    indexing_summary = profile_indexing(raw_docs)
    retrieval_summary = profile_retrieval_artifacts()
    evaluation_summary = profile_evaluation_results()
    findings = build_findings(raw_summary, benchmark_summary, indexing_summary, retrieval_summary, evaluation_summary)

    image_paths = [
        OUTPUT_DIR / "corpus_documents.png",
        OUTPUT_DIR / "document_word_count_boxplot.png",
        OUTPUT_DIR / "section_type_top12.png",
        OUTPUT_DIR / "qrels_per_query_hist.png",
        OUTPUT_DIR / "retrieval_metric_histograms.png",
    ]

    save_bar_chart(
        {row["dataset"]: row["rows"] for row in raw_summary["inventory"]},
        image_paths[0],
        "Documents per Raw Corpus",
        "Documents",
    )
    save_boxplot(
        {
            corpus_name: raw_docs.loc[raw_docs["corpus_name"] == corpus_name, "content_words"].tolist()
            for corpus_name in raw_docs["corpus_name"].unique()
        },
        image_paths[1],
        "Document Word Count by Corpus",
        "Words",
    )
    save_bar_chart(
        raw_summary["section_distribution_top12"],
        image_paths[2],
        "Top 12 Section Types",
        "Documents",
        rotate=35,
    )
    save_histogram(
        benchmark_summary["qrel_per_query_df"]["judged_docs"].tolist(),
        image_paths[3],
        "Judged Documents per Query",
        "Judged docs",
    )
    save_retrieval_metric_histograms(evaluation_summary["per_query_df"], image_paths[4])

    summary = {
        "raw_corpus": {k: v for k, v in raw_summary.items() if k != "dataframe"},
        "benchmark": {k: v for k, v in benchmark_summary.items() if not k.endswith("_df")},
        "indexing": indexing_summary,
        "retrieval_artifacts": {k: v for k, v in retrieval_summary.items() if not k.endswith("_df")},
        "evaluation": {k: v for k, v in evaluation_summary.items() if not k.endswith("_df")},
        "findings": findings,
    }

    markdown_path = OUTPUT_DIR / "project_eda_report.md"
    summary_path = OUTPUT_DIR / "project_eda_summary.json"
    markdown_path.write_text(
        render_report(raw_summary, benchmark_summary, indexing_summary, retrieval_summary, evaluation_summary, findings),
        encoding="utf-8",
    )
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return ReportArtifacts(markdown_path=markdown_path, summary_path=summary_path, image_paths=image_paths)


def main() -> None:
    artifacts = generate_report()
    print(f"Markdown report: {artifacts.markdown_path}")
    print(f"Summary JSON: {artifacts.summary_path}")
    for image_path in artifacts.image_paths:
        print(f"Chart: {image_path}")


if __name__ == "__main__":
    main()
