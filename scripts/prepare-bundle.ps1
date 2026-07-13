<#
.SYNOPSIS
  Stage a relocatable Python interpreter for a fully-offline QAmate installer.

.DESCRIPTION
  Downloads a python-build-standalone (astral-sh) "install_only" build and
  extracts it to build\pybundle\python. electron-builder bundles that folder as
  an extraResource (resources\pybundle\python\python.exe), and bootstrap.js uses
  it to create the app's venv on first launch WITHOUT requiring the user to have
  Python installed.

  This is OPTIONAL. If you skip it, the installer still builds; on a machine
  without the bundle, QAmate's first run falls back to a system Python 3.11+.

.PARAMETER PythonVersion
  CPython version to fetch (default 3.12.7). Must match a python-build-standalone
  release asset.

.PARAMETER Tag
  python-build-standalone release tag (date, e.g. 20241016). Defaults to the
  latest release resolved from the GitHub API.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\prepare-bundle.ps1
#>
[CmdletBinding()]
param(
  [string]$PythonVersion = "3.12.7",
  [string]$Tag = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root = Split-Path -Parent $PSScriptRoot
$bundleDir = Join-Path $root "build\pybundle"
$pythonDir = Join-Path $bundleDir "python"
$tmp = Join-Path $env:TEMP "qamate-pybundle"

Write-Host "QAmate :: staging offline Python runtime" -ForegroundColor Cyan

if (Test-Path (Join-Path $pythonDir "python.exe")) {
  Write-Host "Already staged at $pythonDir — delete build\pybundle\python to re-stage." -ForegroundColor Yellow
  exit 0
}

# Resolve release tag (latest if not pinned).
if ([string]::IsNullOrWhiteSpace($Tag)) {
  Write-Host "Resolving latest python-build-standalone release..."
  try {
    $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest" `
      -Headers @{ "User-Agent" = "qamate-build" }
    $Tag = $rel.tag_name
  } catch {
    $Tag = "20241016"
    Write-Host "GitHub API unavailable; falling back to pinned tag $Tag" -ForegroundColor Yellow
  }
}

$asset = "cpython-$PythonVersion+$Tag-x86_64-pc-windows-msvc-install_only.tar.gz"
$url = "https://github.com/astral-sh/python-build-standalone/releases/download/$Tag/$asset"

New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$archive = Join-Path $tmp $asset

Write-Host "Downloading $asset" -ForegroundColor Cyan
Write-Host "  $url"
Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing

Write-Host "Extracting..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
# The install_only archive expands to a top-level "python\" directory.
tar -xzf $archive -C $bundleDir

if (-not (Test-Path (Join-Path $pythonDir "python.exe"))) {
  throw "Extraction did not produce $pythonDir\python.exe — check the version/tag."
}

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

$ver = & (Join-Path $pythonDir "python.exe") --version
Write-Host "✓ Staged $ver at build\pybundle\python" -ForegroundColor Green
Write-Host "  electron-builder will bundle this as resources\pybundle\python." -ForegroundColor Green
