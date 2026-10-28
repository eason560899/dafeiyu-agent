param(
  [int]$Port = 8420,
  [switch]$Reload
)

$ErrorActionPreference = "Stop"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "Python 3.11+ is required. Install it from python.org first." }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  python -m venv .venv
}
& ".venv\Scripts\python.exe" -m pip install -e backend
$args = @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "$Port")
if ($Reload) { $args += "--reload" }
& ".venv\Scripts\python.exe" @args
