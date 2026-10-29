$ErrorActionPreference = "Stop"
Push-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
try {
    & py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & .\.venv\Scripts\python.exe -m pip install -e .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "Installed Component Sourcing MCP. MCP command:" -ForegroundColor Green
    Write-Host ((Resolve-Path .\.venv\Scripts\component-sourcing-mcp.exe).Path)
}
finally {
    Pop-Location
}
