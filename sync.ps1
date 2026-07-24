param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$source = Join-Path $PSScriptRoot "opencode"
$target = "$env:USERPROFILE\.config\opencode"

if (-not (Test-Path $source)) {
    Write-Error "Source directory not found: $source"
    exit 1
}

if (-not (Test-Path $target)) {
    New-Item -ItemType Directory -Path $target -Force | Out-Null
}

# Ensure subdirectories exist
@("skills", "agents", "commands") | ForEach-Object {
    $p = Join-Path $target $_
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
    }
}

# Items to sync (paths relative to opencode/)
$items = @("AGENTS.md", "skills", "agents", "commands")

Write-Host "Syncing harness -> ~/.config/opencode/"
Write-Host "Source : $source"
Write-Host "Target : $target"
if ($DryRun) { Write-Host "** DRY RUN (no files will be changed) **" }
Write-Host ""

foreach ($item in $items) {
    $srcPath = Join-Path $source $item
    $dstPath = Join-Path $target $item

    if (-not (Test-Path $srcPath)) {
        Write-Warning "Source not found, skipping: $item"
        continue
    }

    if ($DryRun) {
        Write-Host "[DRY-RUN] Would sync: $item"
        if (Test-Path $srcPath -PathType Container) {
            Get-ChildItem $srcPath -Recurse -File | ForEach-Object {
                $rel = $_.FullName.Substring($source.Length + 1).Replace("\", "/")
                $dstFile = Join-Path $target $rel
                $exists = if (Test-Path $dstFile) { "(overwrite)" } else { "(new)" }
                Write-Host "    $rel $exists"
            }
        } else {
            $exists = if (Test-Path $dstPath) { "(overwrite)" } else { "(new)" }
            Write-Host "    $item $exists"
        }
    } else {
        if (Test-Path $srcPath -PathType Container) {
            Copy-Item -Path "$srcPath\*" -Destination $dstPath -Recurse -Force
        } else {
            Copy-Item -Path $srcPath -Destination $dstPath -Force
        }
        Write-Host "Synced: $item"
    }
}

Write-Host ""
Write-Host "Done."
Write-Host ""
Write-Host "NOTE: opencode.jsonc was NOT touched."
Write-Host "Your personal permissions and config remain intact."
Write-Host ""
Write-Host "Permissions are baked into the harness agent (task allowlist, git gates)."
Write-Host "No manual opencode.jsonc configuration is required."

if (-not $DryRun) {
    Write-Host ""
    Write-Host "Restart opencode or start a new session for changes to take effect."
}
