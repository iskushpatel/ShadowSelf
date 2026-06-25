param(
    [switch]$SkipCompile
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:VIRTUAL_ENV = Join-Path $ProjectRoot ".venv"
$env:UV_NO_CACHE = "1"
$python = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"

Write-Host "Project: $ProjectRoot"
Write-Host "VIRTUAL_ENV: $env:VIRTUAL_ENV"
Write-Host "UV_NO_CACHE: $env:UV_NO_CACHE"

function Invoke-Checked {
    param([string[]]$Command)
    $executable = $Command[0]
    $arguments = $Command[1..($Command.Length - 1)]
    & $executable @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($Command -join ' ')"
    }
}

if (-not (Test-Path $env:VIRTUAL_ENV)) {
    Write-Error "Missing .venv. Run: uv sync"
}

if (-not $SkipCompile) {
    Write-Host "Checking Python syntax..."
    Invoke-Checked @(
        $python,
        "-c",
        "from pathlib import Path; files=[Path('main.py'), *Path('backend').rglob('*.py'), *Path('db').rglob('*.py')]; [compile(path.read_text(encoding='utf-8'), str(path), 'exec') for path in files]; print(f'Compiled {len(files)} files')"
    )
}

Write-Host "Importing FastAPI app..."
Invoke-Checked @($python, "-c", "from main import app; print(app.title, app.version)")

Write-Host "Checks passed."
