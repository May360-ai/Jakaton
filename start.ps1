$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
if (-not (Test-Path '.venv/Scripts/python.exe')) { python -m venv .venv }
& ./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias Python' }
Push-Location frontend
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron instalar las dependencias frontend' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar el frontend' }
} finally { Pop-Location }
Write-Host 'Dashboard: http://127.0.0.1:5000'
& ./.venv/Scripts/python.exe -m backend.app
