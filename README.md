# Hybrid BM25 + Vector Search RAG vs LLM-Only

Local-first benchmark and demo app for comparing retrieval-grounded QA with LLM-only answers on nutrition-focused health questions.

This repository bundles the full workflow in one place:
- corpus preparation
- PostgreSQL indexing with BM25 and pgvector
- retrieval and answer generation
- evaluation
- a small web demo for side-by-side comparison

The project was built for coursework and experimentation. It is not a medical product, and it should not be used as a source of medical advice.

## What is in the repo

```text
.
├─ 01-data-preparation/
├─ 02-indexing/
├─ 03-retrieval/
├─ 04-evaluation/
├─ 05-demo-app/
├─ docker-compose.yml
├─ requirements.txt
├─ run_pipeline.ps1
├─ run_demo.ps1
└─ .env.example
```

- `01-data-preparation/` builds the nutrition-focused working corpus.
- `02-indexing/` prepares records, exports benchmark queries, and writes data into PostgreSQL.
- `03-retrieval/` runs hybrid retrieval, grounded answer generation, and LLM-only answering.
- `04-evaluation/` computes retrieval metrics, answer-level scores, and pairwise comparisons.
- `05-demo-app/` contains the FastAPI backend and Next.js frontend used for the local demo.

## Data and scope

The current framing is intentionally narrow:
- question domain: nutrition-related health information
- benchmark backbone: NFCorpus
- authoritative seed source: MedlinePlus

The repo compares two answer modes:
- `hybrid_rag`: answer with retrieved evidence
- `llm_only`: answer directly without retrieval context

The retrieval layer uses:
- BM25 in PostgreSQL through `pg_textsearch`
- vector retrieval in PostgreSQL through `pgvector`
- reciprocal rank fusion for hybrid ranking
- optional Cohere reranking on BM25/vector branches before final fusion

## Requirements

- Windows PowerShell
- Python 3.13+
- Node.js 22+
- Docker Desktop
- an OpenAI API key

Create a local environment file before running anything:

```powershell
Copy-Item .env.example .env
```

Then fill in `OPENAI_API_KEY` in `.env`.

Optional reranker settings:

```powershell
RERANKER_ENABLED=false
RERANKER_PROVIDER=cohere
COHERE_API_KEY=
COHERE_RERANK_MODEL=rerank-v4.0-fast
RERANKER_CANDIDATE_K=20
```

When reranking is enabled, the system reranks BM25 and vector candidates independently and then fuses the reranked branch orders with RRF. If the Cohere API is unavailable, the retrieval flow falls back to the original BM25 + vector + RRF pipeline.

## Quick start

Run the full pipeline:

```powershell
.\run_pipeline.ps1
```

Run a smaller evaluation-only pass:

```powershell
.\run_pipeline.ps1 -Mode eval-only -SkipDocker -Limit 3 -TopK 3
```

This produces:
- indexed documents and nodes in PostgreSQL
- retrieval logs in `03-retrieval/results/`
- evaluation outputs in `04-evaluation/results/`

## Demo app

The demo app is local-only. It reads the indexed data and the latest evaluation artifacts already produced by the pipeline.

### What you can do in the demo

- inspect the latest evaluation snapshot from the benchmark run
- compare `hybrid_rag` and `llm_only` answers for the same question
- inspect the top retrieved passages used by the grounded answer
- check inline citations and timing breakdowns for a live query
- use preset prompts for a quick walkthrough during a demo session

### Screenshots

Dashboard:

![Dashboard](docs/screenshots/dashboard.png)

Workbench before running a query:

![Demo workbench](docs/screenshots/demo-workbench.png)

Workbench after a live query:

![Demo results](docs/screenshots/demo-results.png)

Start it with two terminals.

Terminal 1:

```powershell
cd "05-demo-app\api"
..\..\03-retrieval\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8008
```

Terminal 2:

```powershell
cd "05-demo-app\web"
$env:MEDIR_DEMO_API_URL="http://127.0.0.1:8008"
npm install
npm run dev -- --port 3000
```

Then open:
- `http://127.0.0.1:3000`
- `http://127.0.0.1:3000/demo`

If you prefer a single command, `.\run_demo.ps1` is included, but the manual two-terminal flow is easier to debug.

## Main commands

Phase 1:

```powershell
python 01-data-preparation\data_preparation.py all
python 01-data-preparation\data_preparation.py status
```

Phase 2:

```powershell
python 02-indexing\indexing.py prepare-records
python 02-indexing\indexing.py export-benchmark
python 02-indexing\indexing.py index-postgres
python 02-indexing\indexing.py index-vector
```

Phase 3:

```powershell
python 03-retrieval\retrieval.py db-init
python 03-retrieval\retrieval.py ingest-postgres
python 03-retrieval\retrieval.py query --mode hybrid-rag --text "Can dietary fiber help lower cholesterol?"
python 03-retrieval\retrieval.py batch --limit 10 --top-k 5
```

Phase 4:

```powershell
python 04-evaluation\evaluation.py retrieval-metrics --mode hybrid
python 04-evaluation\evaluation.py answer-eval --mode hybrid_rag
python 04-evaluation\evaluation.py answer-eval --mode llm_only
python 04-evaluation\evaluation.py compare --left-mode hybrid_rag --right-mode llm_only
python 04-evaluation\evaluation.py report
```

## What is versioned

The repo keeps source code, scripts, configuration, and lightweight benchmark files.

It does not keep local runtime output such as:
- generated JSONL corpora
- retrieval and evaluation result folders
- `.env`
- local caches
- `node_modules`
- `.next`

## Limitations

- The corpus is small and intentionally scoped.
- Evaluation is useful for comparison, not for medical validation.
- The demo app assumes local Docker/PostgreSQL and existing evaluation artifacts.
- The default setup is tuned for local reproducibility, not deployment.

## Roadmap

- improve startup reliability for the demo app
- add cleaner dataset refresh and artifact management
- support more benchmark queries and analysis views
- separate demo-facing APIs from evaluation scripts more cleanly

## License

MIT
