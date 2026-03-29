param(
    [int]$ApiPort = 8008,
    [int]$WebPort = 3000,
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WebDir = Join-Path $ProjectRoot "05-demo-app\web"
$ApiAppDir = Join-Path $ProjectRoot "05-demo-app\api"
$ApiPython = Join-Path $ProjectRoot "03-retrieval\.venv\Scripts\python.exe"
$SystemSummary = Join-Path $ProjectRoot "04-evaluation\results\system_summary.json"

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    & $Action
}

function Ensure-Env {
    if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
        throw "Missing .env. Copy .env.example to .env first."
    }
}

function Ensure-Docker {
    docker ps | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Docker daemon is not available."
    }
}

function Wait-ForUrl {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 12
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 1000
        }
    }

    throw "Timed out waiting for $Url"
}

Ensure-Env

if (-not $SkipDocker) {
    Ensure-Docker
    Invoke-Step "Docker Up" { docker compose up -d }
}

Invoke-Step "Python Runtime Bootstrap" { python 03-retrieval\retrieval.py bootstrap }

if (-not (Test-Path $ApiPython)) {
    throw "API interpreter not found at $ApiPython"
}

Invoke-Step "Web Dependencies" {
    Push-Location $WebDir
    try {
        npm install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed."
        }
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path $SystemSummary)) {
    Write-Warning "Missing 04-evaluation/results/system_summary.json. Dashboard will still open, but some metrics cards may be empty."
}

$apiProcess = $null

try {
    Invoke-Step "Start FastAPI" {
        $script:apiProcess = Start-Process `
            -FilePath $ApiPython `
            -ArgumentList @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", $ApiPort.ToString()) `
            -WorkingDirectory $ApiAppDir `
            -PassThru
    }

    Wait-ForUrl -Url "http://127.0.0.1:$ApiPort/health"

    Write-Host ""
    Write-Host "FastAPI is ready at http://127.0.0.1:$ApiPort" -ForegroundColor Green
    Write-Host "Starting Next.js demo on http://127.0.0.1:$WebPort" -ForegroundColor Green

    Push-Location $WebDir
    try {
        $env:MEDIR_DEMO_API_URL = "http://127.0.0.1:$ApiPort"
        npm run dev -- --port $WebPort
    } finally {
        Pop-Location
    }
} finally {
    if ($apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force
    }
}
