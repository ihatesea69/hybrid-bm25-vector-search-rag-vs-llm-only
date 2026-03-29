from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import site
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

NUTRITION_KEYWORDS = [
    "nutrition",
    "nutrient",
    "nutrients",
    "diet",
    "dietary",
    "food",
    "foods",
    "nutrition facts",
    "food label",
    "calorie",
    "calories",
    "protein",
    "sodium",
    "sugar",
    "fat",
    "fiber",
    "vitamin",
    "vitamins",
    "mineral",
    "minerals",
    "carbohydrate",
    "carbohydrates",
]

FOCUSED_NUTRITION_KEYWORDS = [
    "nutrition facts",
    "food label",
    "calorie",
    "calories",
    "protein",
    "sodium",
    "sugar",
    "fat",
    "fiber",
    "vitamin",
    "vitamins",
    "mineral",
    "minerals",
    "carbohydrate",
    "carbohydrates",
]

EXCLUDED_PAGE_HINTS = [
    "tips",
    "snack",
    "snacks",
    "recipe",
    "recipes",
    "fast food tips",
    "healthy snacks",
]

CRAWL_SOURCES = [
    {
        "source_id": "medlineplus_nutrition",
        "title": "MedlinePlus Nutrition",
        "url": "https://medlineplus.gov/nutrition.html",
        "kind": "html",
    },
    {
        "source_id": "fda_nutrition_facts_label",
        "title": "FDA Nutrition Facts Label Guide",
        "url": "https://www.fda.gov/food/nutrition-education-resources-materials/how-understand-and-use-nutrition-facts-label",
        "kind": "html",
    },
    {
        "source_id": "fda_daily_value",
        "title": "FDA Daily Value on the Nutrition Facts Label",
        "url": "https://www.fda.gov/food/new-nutrition-facts-label/daily-value-new-nutrition-and-supplement-facts-labels",
        "kind": "html",
    },
    {
        "source_id": "fda_label_pdf",
        "title": "FDA Nutrition Facts Label PDF",
        "url": "https://www.fda.gov/media/135302/download",
        "kind": "pdf",
    },
]

USER_AGENT = "nutrition-data-preparation/0.1"
MAX_DISCOVERED_LINKS_PER_SOURCE = 12
MAX_DISCOVERED_LINKS_PER_PAGE = 8
MAX_CRAWL_DEPTH = 2
MAX_TOTAL_CRAWL_RECORDS = 100
WHITESPACE_RE = re.compile(r"\s+")

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

NFCORPUS_JSONL = ROOT / "nfcorpus_nutrition.jsonl"
NUTRITION_WEB_JSONL = ROOT / "nutrition_crawl.jsonl"
PUBMED_JSONL = ROOT / "pubmed_kb.jsonl"

LEGACY_OUTPUTS = [
    ROOT / "nutrition_kb.jsonl",
    ROOT / "nutrition_build_summary.json",
    ROOT / "nutrition_structure.jsonl",
    ROOT / "nutrition_metadata.jsonl",
    ROOT / "nutrition_lifecycle.jsonl",
]

PACKAGE_TO_MODULE = {
    "beautifulsoup4": "bs4",
    "datasets": "datasets",
    "pypdf": "pypdf",
}


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\u00a0", " ")
    normalized = WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def keyword_matches(text: str) -> list[str]:
    lowered = f" {text.lower()} "
    matched: list[str] = []
    for keyword in NUTRITION_KEYWORDS:
        needle = f" {keyword.lower()} "
        if needle in lowered:
            matched.append(keyword)
    return matched


def focused_keyword_matches(text: str) -> list[str]:
    lowered = f" {text.lower()} "
    matched: list[str] = []
    for keyword in FOCUSED_NUTRITION_KEYWORDS:
        needle = f" {keyword.lower()} "
        if needle in lowered:
            matched.append(keyword)
    return matched


def fetch_url_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        return response.read()


def extract_html_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    blocks: list[str] = []
    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = normalize_text(element.get_text(" ", strip=True))
        if text:
            blocks.append(text)

    return "\n\n".join(blocks)


def dedupe_links(links: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for link in links:
        url = link["url"]
        if url in seen:
            continue
        seen.add(url)
        output.append(link)
    return output


def extract_html_text_and_links(html: str, base_url: str) -> tuple[str, list[dict[str, str]]]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    blocks: list[str] = []
    links: list[dict[str, str]] = []
    base_netloc = urlparse(base_url).netloc

    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = normalize_text(element.get_text(" ", strip=True))
        if text:
            blocks.append(text)

    for anchor in soup.find_all("a", href=True):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc != base_netloc:
            continue

        text = normalize_text(anchor.get_text(" ", strip=True))
        signal = f"{absolute} {text}"
        if not keyword_matches(signal):
            continue

        clean_url = absolute.split("#", 1)[0]
        links.append({"url": clean_url, "text": text})

    return "\n\n".join(blocks), dedupe_links(links)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages:
        text = normalize_text(page.extract_text() or "")
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def infer_kind_from_url(url: str) -> str:
    lowered = url.lower()
    if lowered.endswith(".pdf") or "/download" in lowered or "media/" in lowered:
        return "pdf"
    return "html"


def make_child_doc_id(source_id: str, url: str) -> str:
    parsed = urlparse(url)
    slug = parsed.path.strip("/").replace("/", "_")
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", slug).strip("_").lower()
    if not slug:
        slug = "root"
    return f"{source_id}_{slug[:80]}"


def should_keep_crawl_record(*, title: str, url: str, content: str, depth: int) -> bool:
    signal = f"{title} {url} {content[:4000]}"
    focused = focused_keyword_matches(signal)
    lowered_signal = signal.lower()

    if depth == 0:
        return True

    if not focused:
        return False

    for hint in EXCLUDED_PAGE_HINTS:
        if hint in lowered_signal:
            return False

    return True


def make_record(
    *,
    doc_id: str,
    source_id: str,
    source_kind: str,
    title: str,
    source_url: str,
    content: str,
    mime_type: str,
    matched_keywords: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "source_id": source_id,
        "source_kind": source_kind,
        "title": title,
        "source_url": source_url,
        "content": content,
        "mime_type": mime_type,
        "matched_keywords": matched_keywords or [],
        "language": "en",
    }

SOURCE_METADATA = {
    "beir_nfcorpus": {
        "source_name": "BEIR NFCorpus",
        "issuing_unit": "BeIR / University of Heidelberg",
        "source_type": "benchmark",
        "applicable_audience": "research",
        "update_frequency": "one_shot",
    },
    "medlineplus_nutrition": {
        "source_name": "MedlinePlus Nutrition",
        "issuing_unit": "U.S. National Library of Medicine",
        "source_type": "health_topic",
        "applicable_audience": "general_public",
        "update_frequency": "periodic",
    },
    "fda_nutrition_facts_label": {
        "source_name": "FDA Nutrition Facts Label Guide",
        "issuing_unit": "U.S. Food and Drug Administration",
        "source_type": "guideline",
        "applicable_audience": "general_public",
        "update_frequency": "periodic",
    },
    "fda_daily_value": {
        "source_name": "FDA Daily Value on the Nutrition Facts Label",
        "issuing_unit": "U.S. Food and Drug Administration",
        "source_type": "guideline",
        "applicable_audience": "general_public",
        "update_frequency": "periodic",
    },
    "fda_label_pdf": {
        "source_name": "FDA Nutrition Facts Label PDF",
        "issuing_unit": "U.S. Food and Drug Administration",
        "source_type": "pdf_guide",
        "applicable_audience": "general_public",
        "update_frequency": "periodic",
    },
    "pubmed_nutrition": {
        "source_name": "PubMed Nutrition Articles",
        "issuing_unit": "U.S. National Library of Medicine",
        "source_type": "journal",
        "applicable_audience": "research",
        "update_frequency": "periodic",
    },
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def activate_local_site_packages(install_missing: bool) -> Path:
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = str(UV_CACHE_DIR)

    if not VENV.exists():
        subprocess.run(
            ["uv", "venv", str(VENV), "--python", sys.executable],
            check=True,
            env=env,
        )

    site_packages = Path(
        subprocess.check_output(
            [str(VENV_PYTHON), "-c", "import site; print(site.getsitepackages()[0])"],
            text=True,
        ).strip()
    )
    site.addsitedir(str(site_packages))

    missing = [
        package
        for package, module_name in PACKAGE_TO_MODULE.items()
        if importlib.util.find_spec(module_name) is None
    ]
    if missing and install_missing:
        subprocess.run(
            ["uv", "pip", "install", "--python", str(VENV_PYTHON), "-r", str(REQUIREMENTS)],
            check=True,
            env=env,
        )
        site.addsitedir(str(site_packages))

    return site_packages


def bootstrap() -> None:
    site_packages = activate_local_site_packages(install_missing=True)
    print(f"Venv ready at: {VENV}")
    print(f"Using site-packages from: {site_packages}")
    print(f"uv cache dir: {UV_CACHE_DIR}")


def cleanup_legacy_outputs() -> None:
    for path in LEGACY_OUTPUTS:
        if path.exists():
            path.unlink()


def build_nfcorpus_nutrition(force: bool) -> list[dict]:
    activate_local_site_packages(install_missing=True)
    from datasets import load_dataset

    if NFCORPUS_JSONL.exists() and not force:
        return load_jsonl(NFCORPUS_JSONL)

    corpus = load_dataset("BeIR/nfcorpus", "corpus", split="corpus")
    rows: list[dict] = []

    for item in corpus:
        title = normalize_text(item.get("title", "")) or str(item["_id"])
        body = normalize_text(item.get("text", ""))
        content = "\n\n".join(part for part in [title, body] if part).strip()
        matched = keyword_matches(content)
        if not matched:
            continue
        rows.append(
            make_record(
                doc_id=str(item["_id"]),
                source_id="beir_nfcorpus",
                source_kind="benchmark",
                title=title,
                source_url="https://huggingface.co/datasets/BeIR/nfcorpus",
                content=content,
                mime_type="text/plain",
                matched_keywords=matched,
            )
        )

    write_jsonl(NFCORPUS_JSONL, rows)
    return rows


def normalize_pubmed_kb(force: bool) -> list[dict]:
    if not PUBMED_JSONL.exists():
        return []

    rows = load_jsonl(PUBMED_JSONL)
    if not rows:
        return []

    already_normalized = all("structure" in row and "metadata" in row and "lifecycle" in row for row in rows)
    if already_normalized and not force:
        return rows

    normalized_rows: list[dict] = []
    collected_at = utc_now_iso()

    for order_idx, row in enumerate(rows, start=1):
        doc_id = row.get("doc_id") or row.get("_id")
        title = normalize_text(row.get("title", "")) or str(doc_id)
        content = normalize_text(row.get("content") or row.get("text") or "")
        source_url = (
            row.get("source_url")
            or (row.get("metadata") or {}).get("url")
            or f"https://pubmed.ncbi.nlm.nih.gov/{str(doc_id).replace('PMID-', '')}/"
        )
        matched = focused_keyword_matches(f"{title} {content}") or keyword_matches(f"{title} {content}")

        normalized_rows.append(
            {
                "doc_id": doc_id,
                "source_id": "pubmed_nutrition",
                "source_kind": "pubmed",
                "title": title,
                "source_url": source_url,
                "content": content,
                "mime_type": "text/plain",
                "matched_keywords": matched,
                "language": "en",
                "structure": {
                    "parent_source_id": "source::pubmed_nutrition",
                    "relation_type": "source_document",
                    "order_idx": order_idx,
                    "topic_key": slug((matched[0] if matched else "nutrition")),
                    "topic_relation": f"topic::{slug((matched[0] if matched else 'nutrition'))}",
                },
                "metadata": {
                    "source_name": "PubMed Nutrition Articles",
                    "source_type": "journal",
                    "issuing_unit": "U.S. National Library of Medicine",
                    "applicable_audience": "research",
                    "mime_type": "text/plain",
                    "language": "en",
                    "topic_domain": "nutrition-related health information",
                    "issued_at": None,
                    "effective_from": None,
                    "effective_to": None,
                },
                "lifecycle": {
                    "version": "v1",
                    "status": "current",
                    "collected_at": collected_at,
                    "last_checked_at": collected_at,
                    "update_frequency": "periodic",
                    "supersedes": None,
                    "superseded_by": None,
                    "is_current": True,
                },
            }
        )

    write_jsonl(PUBMED_JSONL, normalized_rows)
    return normalized_rows


def crawl_nutrition_sources(force: bool) -> list[dict]:
    activate_local_site_packages(install_missing=True)

    if NUTRITION_WEB_JSONL.exists() and not force:
        return load_jsonl(NUTRITION_WEB_JSONL)

    rows: list[dict] = []
    seen_urls: set[str] = set()

    for source in CRAWL_SOURCES:
        queue: list[dict[str, object]] = [
            {
                "url": source["url"],
                "title": source["title"],
                "kind": source["kind"],
                "depth": 0,
            }
        ]

        while queue and len(rows) < MAX_TOTAL_CRAWL_RECORDS:
            item = queue.pop(0)
            url = str(item["url"])
            title = str(item["title"])
            kind = str(item["kind"])
            depth = int(item["depth"])

            if url in seen_urls:
                continue
            seen_urls.add(url)

            try:
                raw = fetch_url_bytes(url)
            except Exception:
                continue

            discovered_links: list[dict[str, str]] = []
            if kind == "pdf":
                content = extract_pdf_text(raw)
                mime_type = "application/pdf"
            else:
                content, discovered_links = extract_html_text_and_links(
                    raw.decode("utf-8", errors="ignore"),
                    url,
                )
                mime_type = "text/html"

            content = normalize_text(content)
            matched = keyword_matches(f"{title} {content}")
            focused = focused_keyword_matches(f"{title} {url} {content}")
            if not matched:
                continue
            if not should_keep_crawl_record(
                title=title,
                url=url,
                content=content,
                depth=depth,
            ):
                continue

            doc_id = (
                source["source_id"]
                if depth == 0
                else make_child_doc_id(source["source_id"], url)
            )
            rows.append(
                make_record(
                    doc_id=doc_id,
                    source_id=source["source_id"],
                    source_kind="crawl",
                    title=title,
                    source_url=url,
                    content=content,
                    mime_type=mime_type,
                    matched_keywords=focused or matched,
                )
            )

            if depth >= MAX_CRAWL_DEPTH:
                continue

            link_limit = (
                MAX_DISCOVERED_LINKS_PER_SOURCE
                if depth == 0
                else MAX_DISCOVERED_LINKS_PER_PAGE
            )
            for link in discovered_links[:link_limit]:
                child_url = link["url"]
                if child_url in seen_urls:
                    continue
                queue.append(
                    {
                        "url": child_url,
                        "title": link["text"] or child_url,
                        "kind": infer_kind_from_url(child_url),
                        "depth": depth + 1,
                    }
                )

    write_jsonl(NUTRITION_WEB_JSONL, rows)
    return rows


def source_meta(source_id: str) -> dict:
    return SOURCE_METADATA.get(
        source_id,
        {
            "source_name": source_id,
            "issuing_unit": None,
            "source_type": None,
            "applicable_audience": None,
            "update_frequency": None,
        },
    )


def slug(value: str) -> str:
    return (
        value.lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .strip("_")
    )


def primary_topic(row: dict) -> str | None:
    keywords = row.get("matched_keywords") or []
    return keywords[0] if keywords else None


def enrich_rows(rows: list[dict]) -> list[dict]:
    collected_at = utc_now_iso()
    per_source_order: dict[str, int] = {}
    enriched: list[dict] = []

    for row in rows:
        source_id = row["source_id"]
        doc_id = row["doc_id"]
        per_source_order[source_id] = per_source_order.get(source_id, 0) + 1
        topic = primary_topic(row)
        meta = source_meta(source_id)

        enriched_row = dict(row)
        enriched_row["structure"] = {
            "parent_source_id": f"source::{source_id}",
            "relation_type": "source_document",
            "order_idx": per_source_order[source_id],
            "topic_key": slug(topic) if topic else None,
            "topic_relation": f"topic::{slug(topic)}" if topic else None,
        }
        enriched_row["metadata"] = {
            "source_name": meta["source_name"],
            "source_type": meta["source_type"],
            "issuing_unit": meta["issuing_unit"],
            "applicable_audience": meta["applicable_audience"],
            "mime_type": row["mime_type"],
            "language": row.get("language", "en"),
            "topic_domain": "nutrition-related health information",
            "issued_at": None,
            "effective_from": None,
            "effective_to": None,
        }
        enriched_row["lifecycle"] = {
            "version": "v1",
            "status": "current",
            "collected_at": collected_at,
            "last_checked_at": collected_at,
            "update_frequency": meta["update_frequency"],
            "supersedes": None,
            "superseded_by": None,
            "is_current": True,
        }
        enriched.append(enriched_row)

    return enriched


def run_all(force: bool) -> None:
    cleanup_legacy_outputs()
    bootstrap()
    nfcorpus_rows = build_nfcorpus_nutrition(force=force)
    web_rows = crawl_nutrition_sources(force=force)
    pubmed_rows = normalize_pubmed_kb(force=force)
    write_jsonl(NFCORPUS_JSONL, enrich_rows(nfcorpus_rows))
    write_jsonl(NUTRITION_WEB_JSONL, enrich_rows(web_rows))
    print(
        json.dumps(
            {
                "nfcorpus_nutrition_count": len(nfcorpus_rows),
                "nutrition_crawl_count": len(web_rows),
                "pubmed_kb_count": len(pubmed_rows),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def status() -> None:
    cleanup_legacy_outputs()
    payload = {
        "nfcorpus_nutrition_exists": NFCORPUS_JSONL.exists(),
        "nutrition_crawl_exists": NUTRITION_WEB_JSONL.exists(),
        "pubmed_kb_exists": PUBMED_JSONL.exists(),
        "nfcorpus_nutrition_count": len(load_jsonl(NFCORPUS_JSONL)),
        "nutrition_crawl_count": len(load_jsonl(NUTRITION_WEB_JSONL)),
        "pubmed_kb_count": len(load_jsonl(PUBMED_JSONL)),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Simple nutrition-focused data preparation pipeline."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Create .venv and install minimal dependencies.")

    nf_parser = sub.add_parser(
        "build-nfcorpus",
        help="Export one JSONL file from NFCorpus filtered to nutrition topics.",
    )
    nf_parser.add_argument("--force", action="store_true")

    crawl_parser = sub.add_parser(
        "crawl-nutrition",
        help="Crawl official nutrition HTML/PDF sources into one JSONL file.",
    )
    crawl_parser.add_argument("--force", action="store_true")

    kb_parser = sub.add_parser(
        "build-kb",
        help="Enrich the two main JSONL files and normalize PubMed JSONL to the same schema.",
    )
    kb_parser.add_argument("--force", action="store_true")

    pubmed_parser = sub.add_parser(
        "normalize-pubmed",
        help="Rewrite pubmed_kb.jsonl to the same schema as the other JSONL outputs.",
    )
    pubmed_parser.add_argument("--force", action="store_true")

    all_parser = sub.add_parser("all", help="Run the full simple data pipeline.")
    all_parser.add_argument("--force", action="store_true")

    sub.add_parser("status", help="Print file existence and record counts.")

    args = parser.parse_args()

    if args.command == "bootstrap":
        bootstrap()
    elif args.command == "build-nfcorpus":
        build_nfcorpus_nutrition(force=args.force)
    elif args.command == "crawl-nutrition":
        crawl_nutrition_sources(force=args.force)
    elif args.command == "build-kb":
        nfcorpus_rows = build_nfcorpus_nutrition(force=args.force)
        web_rows = crawl_nutrition_sources(force=args.force)
        write_jsonl(NFCORPUS_JSONL, enrich_rows(nfcorpus_rows))
        write_jsonl(NUTRITION_WEB_JSONL, enrich_rows(web_rows))
        normalize_pubmed_kb(force=args.force)
    elif args.command == "normalize-pubmed":
        normalize_pubmed_kb(force=args.force)
    elif args.command == "all":
        run_all(force=args.force)
    elif args.command == "status":
        status()
    else:
        parser.error(f"Unknown command: {args.command}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
