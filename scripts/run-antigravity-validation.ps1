param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Commit,
    [Parameter(Mandatory = $true)][string]$TestPlan,
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [string]$PrintTimeout = "90m"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-SemVer([string]$Text) {
    $match = [regex]::Match($Text, '(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)')
    if (-not $match.Success) {
        throw "Unable to parse Antigravity CLI version from: $Text"
    }
    return [version]$match.Value
}

function Set-JsonProperty([object]$Object, [string]$Name, [object]$Value) {
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Normalize-PermissionPath([string]$PathValue) {
    if (-not $PathValue) { return "" }
    return ($PathValue -replace '\\', '/')
}

$agy = Get-Command agy -ErrorAction SilentlyContinue
if (-not $agy) {
    throw "Antigravity CLI (agy) is not installed or not on PATH. Install official Antigravity CLI first."
}

$agyVersionText = (& agy --version 2>&1 | Out-String).Trim()
$agyVersion = Get-SemVer $agyVersionText
if ($agyVersion -lt [version]"1.1.8") {
    throw "Antigravity CLI >= 1.1.8 is required for reliable headless structured output. Found: $agyVersionText"
}

if (-not (Test-Path -LiteralPath $TestPlan -PathType Leaf)) {
    throw "Test plan not found: $TestPlan"
}

$reportDir = Split-Path -Parent $ReportPath
if ($reportDir) {
    New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
}

$workspace = (Get-Location).Path
$testRoot = if ($env:SZLCSC_ANTIGRAVITY_TEST_ROOT) {
    $env:SZLCSC_ANTIGRAVITY_TEST_ROOT
}
else {
    "D:\SZLCSC_MCP_TEST\auto\$($Version.TrimStart('v'))"
}

$legacyRoot = if ($env:SZLCSC_ANTIGRAVITY_LEGACY_LIBRARY) {
    $env:SZLCSC_ANTIGRAVITY_LEGACY_LIBRARY
}
else {
    ""
}

$validationPython = if ($env:VALIDATION_PYTHON) { $env:VALIDATION_PYTHON } else { "python" }
$artifactDir = Join-Path (Split-Path -Parent $ReportPath) ".antigravity-artifacts"
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
$agyOutput = Join-Path $artifactDir "$($Version.TrimStart('v'))-agy-output.json"
$preflightOutput = Join-Path $artifactDir "$($Version.TrimStart('v'))-agy-preflight.json"

Write-Host "Antigravity CLI: $agyVersionText"
Write-Host "Workspace: $workspace"
Write-Host "Candidate: $Commit"
Write-Host "Plan: $TestPlan"
Write-Host "Report: $ReportPath"
Write-Host "Test root: $testRoot"
Write-Host "Legacy library: $legacyRoot"

# Prove that authentication/proxy/headless print mode works before changing any
# permission settings. This prompt intentionally requires no tools.
$preflightPrompt = "Reply exactly AGY_HEADLESS_OK. Do not call any tool, read any file, or execute any command."
& agy -p $preflightPrompt --cwd $workspace --output-format json 2>&1 |
    Tee-Object -FilePath $preflightOutput
$preflightExit = $LASTEXITCODE
$preflightText = if (Test-Path -LiteralPath $preflightOutput) { Get-Content -LiteralPath $preflightOutput -Raw } else { "" }
if ($preflightExit -ne 0 -or $preflightText -notmatch 'AGY_HEADLESS_OK') {
    throw "Antigravity headless preflight failed. Exit=$preflightExit Output=$preflightOutput"
}
Write-Host "Antigravity headless preflight PASS."

$prompt = @"
You are the release-validation executor for SZLCSC MCP.

Test exactly this source commit: $Commit
Release version: $Version
Normative test plan: $TestPlan
Required report path: $ReportPath
Validation Python: $validationPython
Test root: $testRoot
Legacy Altium library root (may be empty): $legacyRoot

Rules:
1. Read and follow the normative test plan exactly. Do not invent PASS results.
2. Run all required local Windows, live SZLCSC, Datasheet, EasyEDA/STEP and Altium checks that the plan requires.
3. Use the existing repository and local environment only for validation. Do not implement features or repair failing source code.
4. Do not commit, push, create branches, create releases, or modify GitHub state.
5. Source-code edits are forbidden. Temporary test fixtures belong under the test root. The only intended repository write is the final report file.
6. Treat the legacy Altium library root as read-only. Copy source assets into the test root before any destructive or writeback test.
7. Prefer the Validation Python path shown above for Python/pytest commands so the exact candidate environment is tested.
8. Do not browse the web directly. Exercise live network behavior through this project's own SZLCSC/Datasheet/EasyEDA code and tests.
9. If a component under test is genuinely wrong and the MCP correctly detects it, record that under Component findings; that is not automatically an MCP FAIL.
10. The report must include these machine-readable lines exactly, in addition to any Markdown formatting you want:
   Executor: Antigravity
   Version: $Version
   Commit: $Commit
   Overall: PASS|FAIL
   FAIL items: [...]
   SKIP / NOT_RUN items: [...]
11. Preserve concrete stdout/stderr, file paths, C-codes, URLs, tool outputs and manual Altium findings for every failure/blocker.
12. Write the complete report to $ReportPath before finishing.
"@

$settingsPath = Join-Path $HOME ".gemini\antigravity-cli\settings.json"
$settingsDir = Split-Path -Parent $settingsPath
$settingsExisted = Test-Path -LiteralPath $settingsPath -PathType Leaf
$originalSettingsBytes = $null

if ($settingsExisted) {
    $originalSettingsBytes = [System.IO.File]::ReadAllBytes($settingsPath)
    $rawSettings = Get-Content -LiteralPath $settingsPath -Raw
    if ($rawSettings.Trim()) {
        $settings = $rawSettings | ConvertFrom-Json
    }
    else {
        $settings = [pscustomobject]@{}
    }
}
else {
    $settings = [pscustomobject]@{}
}

$allow = @(
    'command(python)',
    'command(py)',
    'command(pytest)',
    'command(altium-cruncher)',
    'command(git (status|diff|rev-parse|show|log))',
    "read_file($(Normalize-PermissionPath $testRoot))",
    "write_file($(Normalize-PermissionPath $testRoot))"
)
if ($legacyRoot) {
    $allow += "read_file($(Normalize-PermissionPath $legacyRoot))"
}

$deny = @(
    'command(git (push|commit|reset|clean|checkout|switch|merge|rebase))',
    'command(gh)',
    'command(curl .*)',
    'command(curl.exe .*)',
    'command(wget .*)',
    'command(Invoke-WebRequest .*)',
    'command(Invoke-RestMethod .*)',
    'write_file(.git/)',
    'execute_url(*)',
    'read_url(*)'
)
if ($legacyRoot) {
    $deny += "write_file($(Normalize-PermissionPath $legacyRoot))"
}

$temporaryPermissions = [pscustomobject]@{
    allow = $allow
    deny = $deny
    ask = @()
}

Set-JsonProperty $settings 'permissions' $temporaryPermissions
Set-JsonProperty $settings 'toolPermission' 'always-proceed'
Set-JsonProperty $settings 'artifactReviewPolicy' 'always-proceed'
Set-JsonProperty $settings 'allowNonWorkspaceAccess' $true

New-Item -ItemType Directory -Force -Path $settingsDir | Out-Null
$settingsJson = $settings | ConvertTo-Json -Depth 12
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($settingsPath, $settingsJson, $utf8NoBom)

Write-Host "Temporary CI autonomy installed. Explicit deny rules still block Git/GitHub mutation, direct web tools, .git writes, and legacy-library writes."

try {
    & agy --mode=accept-edits -p $prompt --cwd $workspace --output-format json --print-timeout $PrintTimeout 2>&1 |
        Tee-Object -FilePath $agyOutput
    $exitCode = $LASTEXITCODE

    if ($exitCode -ne 0) {
        throw "Antigravity CLI exited with code $exitCode. Structured output: $agyOutput"
    }
    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) {
        throw "Antigravity completed without creating the required report: $ReportPath"
    }

    $report = Get-Content -LiteralPath $ReportPath -Raw
    foreach ($required in @("Executor: Antigravity", "Version: $Version", "Commit: $Commit", "Overall:")) {
        if ($report -notmatch [regex]::Escape($required)) {
            throw "Report is missing required marker: $required"
        }
    }

    Write-Host "Antigravity report generated successfully."
}
finally {
    if ($settingsExisted) {
        [System.IO.File]::WriteAllBytes($settingsPath, $originalSettingsBytes)
    }
    elseif (Test-Path -LiteralPath $settingsPath) {
        Remove-Item -LiteralPath $settingsPath -Force
    }
    Write-Host "Original Antigravity settings restored."
}
