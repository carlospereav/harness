<#
.SYNOPSIS
    Installs the prerequisites for the kaggle-notebook skill:
    - Python packages: kaggle, nbformat
    - Workspace directory (default: ~/kaggle-workspace)
    - Checks for Kaggle credentials at ~/.kaggle/kaggle.json

.PARAMETER Workspace
    Override workspace root (default: $env:USERPROFILE\kaggle-workspace).

.PARAMETER DryRun
    Print actions without executing them.

.EXAMPLE
    .\setup.ps1
    .\setup.ps1 -DryRun
    .\setup.ps1 -Workspace D:\my-kaggle
#>
param(
    [string]$Workspace = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-OK([string]$msg)    { Write-Host "  [OK]   $msg" -ForegroundColor Green }
function Write-No([string]$msg)   { Write-Host "  [MISS] $msg" -ForegroundColor Yellow }
function Write-Info([string]$msg) { Write-Host "         $msg" -ForegroundColor DarkGray }

# --- Python --------------------------------------------------------------- #
Write-Step "Checking Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
    $ver = & python --version 2>$null
    Write-OK "Python: $ver ($($py.Source))"
} else {
    Write-No "Python not found on PATH"
    Write-Info "Install Python 3.10+ from https://www.python.org/downloads/ and rerun this script."
    exit 1
}

# --- pip packages --------------------------------------------------------- #
Write-Step "Installing Python packages (kaggle, nbformat)"

$pkgs = @("kaggle", "nbformat")
foreach ($pkg in $pkgs) {
    $installed = & python -m pip show $pkg 2>$null | Select-String "^Version:"
    if ($installed) {
        Write-OK "$pkg already installed ($($installed.ToString()))"
    } else {
        if ($DryRun) {
            Write-Host "  [DRY] would install $pkg" -ForegroundColor Magenta
        } else {
            & python -m pip install $pkg
            if ($LASTEXITCODE -ne 0) { Write-Error "Failed to install $pkg"; exit 1 }
            Write-OK "$pkg installed"
        }
    }
}

# --- Kaggle credentials --------------------------------------------------- #
Write-Step "Checking Kaggle credentials"
$credsDir = Join-Path $env:USERPROFILE ".kaggle"
$creds = Join-Path $credsDir "kaggle.json"
if (Test-Path -LiteralPath $creds) {
    Write-OK "credentials found: $creds"
} else {
    Write-No "credentials NOT found: $creds"
    Write-Info "1. Go to https://www.kaggle.com/settings"
    Write-Info "2. Scroll to 'API' -> 'Create New Token'  (downloads kaggle.json)"
    Write-Info "3. Save it at: $creds"
    Write-Info "   Format: {""username"":""youruser"",""key"":""abc123...""}"
    if (-not $DryRun) {
        if (-not (Test-Path -LiteralPath $credsDir)) {
            New-Item -ItemType Directory -Path $credsDir -Force | Out-Null
        }
        Write-Info "(created empty dir $credsDir - place kaggle.json there)"
    }
}

# --- Workspace ------------------------------------------------------------ #
Write-Step "Setting up workspace"
$ws = if ($Workspace) { $Workspace } else { Join-Path $env:USERPROFILE "kaggle-workspace" }
if (Test-Path -LiteralPath $ws) {
    Write-OK "workspace exists: $ws"
} else {
    if ($DryRun) {
        Write-Host "  [DRY] would create workspace: $ws" -ForegroundColor Magenta
    } else {
        New-Item -ItemType Directory -Path $ws -Force | Out-Null
        Write-OK "workspace created: $ws"
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
Write-Host "IMPORTANT: The workspace lives OUTSIDE your git repos, so notebook"
Write-Host "           code never leaks to GitHub. Notebooks are pushed to Kaggle"
Write-Host "           as PRIVATE kernels (is_private=true)."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  python opencode/skills/kaggle-notebook/scripts/kaggle_nb.py setup"
Write-Host "  python opencode/skills/kaggle-notebook/scripts/kaggle_nb.py new my-notebook"
Write-Host "  python opencode/skills/kaggle-notebook/scripts/kaggle_nb.py push my-notebook"