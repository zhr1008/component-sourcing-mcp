$ErrorActionPreference = "Stop"
$Python = "py"
& $Python -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -e .
Write-Host "Installed Component Sourcing MCP. MCP command:" -ForegroundColor Green
Write-Host ((Resolve-Path .\.venv\Scripts\component-sourcing-mcp.exe).Path)
