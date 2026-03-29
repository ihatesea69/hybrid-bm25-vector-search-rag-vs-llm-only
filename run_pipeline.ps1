param(
    [ValidateSet("full", "eval-only")]
    [string]$Mode = "full",
    [switch]$Force,
    [int]$Limit = 10,
    [int]$TopK = 5,
    [switch]$SkipDocker,
    [switch]$NoClear
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param(
        [string]$Name,
        [string]$Command
    )

    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    Write-Host $Command -ForegroundColor DarkGray

    & powershell -NoProfile -ExecutionPolicy Bypass -Command $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed: $Name"
    }
}

function Ensure-Env {
    if (-not (Test-Path ".env")) {
        throw "Missing .env. Create it first, for example by copying .env.example."
    }
}

function Ensure-Docker {
    docker ps | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon is not available."
    }
}

function Print-Artifacts {
    Write-Host ""
    Write-Host "=== Artifacts ===" -ForegroundColor Green
    $artifacts = @(
        "01-data-preparation\nfcorpus_nutrition.jsonl",
        "01-data-preparation\nutrition_crawl.jsonl",
        "01-data-preparation\pubmed_kb.jsonl",
        "02-indexing\data\index_documents.jsonl",
        "02-indexing\data\index_nodes.jsonl",
        "02-indexing\data\benchmark_queries.jsonl",
        "02-indexing\data\benchmark_qrels_test.tsv",
        "04-evaluation\results\retrieval_metrics_hybrid.json",
        "04-evaluation\results\answer_eval_hybrid_rag.json",
        "04-evaluation\results\answer_eval_llm_only.json",
        "04-evaluation\results\pairwise_hybrid_rag_vs_llm_only.json",
        "04-evaluation\results\system_summary.json"
    )
    foreach ($path in $artifacts) {
        if (Test-Path $path) {
            Write-Host $path
        }
    }
}

Ensure-Env

if (-not $SkipDocker) {
    Ensure-Docker
    Invoke-Step "Docker Up" "docker compose up -d"
}

if ($Mode -eq "full") {
    $phase1Force = if ($Force) { "--force" } else { "" }
    $phase2Force = if ($Force) { "--force" } else { "" }

    Invoke-Step "Phase 1" "python 01-data-preparation\data_preparation.py all $phase1Force"
    Invoke-Step "Phase 2 Prepare" "python 02-indexing\indexing.py prepare-records $phase2Force"
    Invoke-Step "Phase 2 Benchmark" "python 02-indexing\indexing.py export-benchmark $phase2Force"
    Invoke-Step "Phase 2 Postgres" "python 02-indexing\indexing.py index-postgres $phase2Force"
    Invoke-Step "Phase 2 Vector" "python 02-indexing\indexing.py index-vector $phase2Force"
}

if (-not $NoClear) {
    Invoke-Step "Phase 3 Clear" "python 03-retrieval\retrieval.py clear-results"
}

Invoke-Step "Phase 3 DB Init" "python 03-retrieval\retrieval.py db-init"

if ($Mode -eq "full") {
    $phase3Force = if ($Force) { "--force" } else { "" }
    Invoke-Step "Phase 3 Ingest" "python 03-retrieval\retrieval.py ingest-postgres $phase3Force"
}

Invoke-Step "Phase 3 Batch" "python 03-retrieval\retrieval.py batch --limit $Limit --top-k $TopK"
Invoke-Step "Phase 4 Retrieval Metrics" "python 04-evaluation\evaluation.py retrieval-metrics --mode hybrid"
Invoke-Step "Phase 4 Answer Eval RAG" "python 04-evaluation\evaluation.py answer-eval --mode hybrid_rag --limit $Limit"
Invoke-Step "Phase 4 Answer Eval LLM" "python 04-evaluation\evaluation.py answer-eval --mode llm_only --limit $Limit"
Invoke-Step "Phase 4 Compare" "python 04-evaluation\evaluation.py compare --left-mode hybrid_rag --right-mode llm_only --limit $Limit"
Invoke-Step "Phase 4 Report" "python 04-evaluation\evaluation.py report"

Invoke-Step "Phase 2 Status" "python 02-indexing\indexing.py status"
Invoke-Step "Phase 3 Status" "python 03-retrieval\retrieval.py status"
Invoke-Step "Phase 4 Status" "python 04-evaluation\evaluation.py status"

Print-Artifacts
