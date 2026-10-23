param(
    [string]$PythonCommand = "python",
    [string]$Version = "2026.9.7",
    [string]$InstallRoot = (Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "szlcsc-mcp\altium-cruncher")
)
$ErrorActionPreference = "Stop"
$targetRoot = [System.IO.Path]::GetFullPath($InstallRoot)
if (-not (Get-Command $PythonCommand -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ is required. Pass -PythonCommand with its executable path."
}

# A dedicated venv has a real console entry point and survives removal of uv
# tool environments. Native command failures must stop this installer.
Write-Host "[SZLCSC MCP] Installing altium-cruncher $Version into $targetRoot"
& $PythonCommand -X utf8 -m venv $targetRoot
if ($LASTEXITCODE -ne 0) { throw "Could not create the converter environment." }
$converterPython = Join-Path $targetRoot "Scripts\python.exe"
$converterExe = Join-Path $targetRoot "Scripts\altium-cruncher.exe"
& $converterPython -X utf8 -m pip install --index-url https://pypi.org/simple --upgrade "altium-cruncher==$Version"
if ($LASTEXITCODE -ne 0) { throw "Converter installation failed." }
& $converterExe version
if ($LASTEXITCODE -ne 0) { throw "Converter version check failed." }
& $converterExe easyeda-import --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Converter has no usable easyeda-import command." }
& $converterExe merge --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Converter has no usable merge command." }
Write-Host "Verified: $converterExe"
Write-Host "The default location is discovered automatically. For a custom root, set SZLCSC_ALTIUM_CRUNCHER to this executable."
