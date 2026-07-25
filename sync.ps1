param(
    [switch]$DryRun,
    [switch]$OnlyHarness,
    [switch]$OnlyKaggle
)

$ErrorActionPreference = "Stop"

# Two source trees merged into the same target (~/.config/opencode/):
#   - opencode/         : the original harness (AGENTS.md, agents/, commands/, skills/)
#   - opencode-kaggle/  : the Kaggle opencode extensions (commands/, skills/) —
#                 fully decoupled from the harness source tree, but installed
#                 side-by-side so the `kaggle-notebook` and `kaggle-competition`
#                 skills keep working as siblings under ~/.config/opencode/skills/.
$sourceHarness = Join-Path $PSScriptRoot "opencode"
$sourceKaggle   = Join-Path $PSScriptRoot "opencode-kaggle"
$target = "$env:USERPROFILE\.config\opencode"

if ($OnlyHarness -and $OnlyKaggle) {
    Write-Error "Specify only one of -OnlyHarness / -OnlyKaggle, not both."
    exit 1
}

$syncHarness = -not $OnlyKaggle
$syncKaggle   = -not $OnlyHarness

if (-not (Test-Path $sourceHarness) -and $syncHarness) {
    Write-Error "Harness source directory not found: $sourceHarness"
    exit 1
}
if (-not (Test-Path $sourceKaggle) -and $syncKaggle) {
    Write-Error "Kaggle source directory not found: $sourceKaggle"
    exit 1
}

if (-not (Test-Path $target)) {
    New-Item -ItemType Directory -Path $target -Force | Out-Null
}

# Ensure target subdirectories exist
@("skills", "agents", "commands") | ForEach-Object {
    $p = Join-Path $target $_
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
    }
}

# Items to sync, relative to each source root.
# The harness tree owns AGENTS.md and agents/; the kaggle tree only needs
# commands/ and skills/. Both trees share commands/ and skills/ in the target
# (additive merge — they never overlap by filename).
$harnessItems = @("AGENTS.md", "skills", "agents", "commands")
$kaggleItems  = @("skills", "commands")

Write-Host "Syncing -> ~/.config/opencode/"
Write-Host "Target : $target"
if ($syncHarness) { Write-Host "Harness source : $sourceHarness" }
if ($syncKaggle)  { Write-Host "Kaggle  source : $sourceKaggle" }
if ($DryRun) { Write-Host "** DRY RUN (no files will be changed) **" }
Write-Host ""

# ------------------------------------------------------------------ #
# Self-check: enforce the "mandatory ask gate" permission patterns.  #
# Fails the sync if the harness agent weakens the commit/push guard,#
# preventing regression of the CVE-like ask-gate evasion (git -C ... #
# commit|push bypassing the "git commit*" / "git push*" patterns).   #
# ------------------------------------------------------------------ #
function Test-AskGatePatterns {
    $agent = Join-Path $sourceHarness "agents\harness.md"
    if (-not (Test-Path -LiteralPath $agent)) { return $true }  # nothing to check
    $lines = Get-Content -LiteralPath $agent -Encoding UTF8

    # Required rules. The DEFAULT must be `ask` (deny-by-default) so that ANY
    # command not explicitly allow-listed asks for approval — closes ALL
    # bypasses (interpreter wrappers, chaining, absolute paths, aliases,
    # mixed-case binaries, future binaries) deterministically. Plus the
    # destructive deny rules must be present and ordered after ask.
    $askNeedles = @(
        '"*": ask',                            # the kill-switch: deny-by-default
        '"git commit*": ask',
        '"git push*": ask',
        '"gh pr create*": ask',
        '"gh pr merge*": ask'
    )
    $denyNeedles = @(
        '"*; *": deny',                         # universal chaining deny
        '"*&& *": deny',
        '"*|| *": deny',
        '"*| *": deny',
        '"git push --force*": deny',
        '"git push -f*": deny',
        '"git push* --force*": deny',
        '"git push* -f*": deny',
        '"git reset --hard*": deny',
        '"git branch -D*": deny',
        '"git branch * -D*": deny',
        '"git tag -d*": deny',
        '"git stash drop*": deny',
        '"git stash clear*": deny',
        '"git clean -f*": deny',
        '"gh repo delete*": deny'
    )

    # indexed list of (lineNumber, trimmedLine) for non-comment lines
    $codeLines = @()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $trim = ($lines[$i] -replace '^\s*', '')
        if ($trim.Length -eq 0) { continue }
        if ($trim.StartsWith('#')) { continue }
        $codeLines += [pscustomobject]@{ LineNo = $i; Text = $lines[$i] }
    }

    $missing = @()
    $askLineNos = @()
    $denyLineNos = @()
    foreach ($n in $askNeedles) {
        $found = -1
        foreach ($cl in $codeLines) {
            if ($cl.Text -match [regex]::Escape($n) -and $cl.Text -match ': ask') {
                $found = $cl.LineNo; break
            }
        }
        if ($found -ge 0) { $askLineNos += $found } else { $missing += "$n  (with `": ask`")" }
    }
    foreach ($n in $denyNeedles) {
        $found = -1
        foreach ($cl in $codeLines) {
            if ($cl.Text -match [regex]::Escape($n)) { $found = $cl.LineNo; break }
        }
        if ($found -ge 0) { $denyLineNos += $found } else { $missing += "$n" }
    }

    if ($missing.Count -gt 0) {
        Write-Host "[ASK-GATE CHECK] MISSING required permission patterns:" -ForegroundColor Red
        foreach ($m in $missing) { Write-Host "   - $m" -ForegroundColor Red }
        Write-Host "These patterns are mandatory to prevent ask-gate evasion via" -ForegroundColor Red
        Write-Host "git -C <path> commit|push, gh publishing verbs, or absolute-path git.exe." -ForegroundColor Red
        Write-Host "Fix opencode/agents/harness.md before syncing." -ForegroundColor Red
        return $false
    }

    # ORDERING check: deny rules must appear AFTER ask rules (last match wins
    # in opencode, so if a deny precedes an ask, the ask overrides the deny).
    if ($denyLineNos.Count -gt 0 -and $askLineNos.Count -gt 0) {
        $minDeny = ($denyLineNos | Measure-Object -Minimum).Minimum
        $maxAsk  = ($askLineNos  | Measure-Object -Maximum).Maximum
        if ($minDeny -le $maxAsk) {
            Write-Host "[ASK-GATE CHECK] ORDERING ERROR: deny rules must come AFTER ask rules" -ForegroundColor Red
            Write-Host "  (last match wins; min deny line $minDeny <= max ask line $maxAsk)" -ForegroundColor Red
            Write-Host "Fix opencode/agents/harness.md before syncing." -ForegroundColor Red
            return $false
        }
    }

    Write-Host "[ASK-GATE CHECK] OK - all mandatory ask/deny patterns present and ordered." -ForegroundColor Green
    return $true
}

if ($syncHarness) {
    if (-not (Test-AskGatePatterns)) { exit 1 }
} else {
    Write-Host "[ASK-GATE CHECK] Skipped (harness tree not selected)." -ForegroundColor DarkGray
}

# Generic per-tree sync. $root is the source base (opencode/ or opencode-kaggle/),
# $label is the human-friendly tree name, $itemList is the list of relative
# entries to copy. Mirrors behaviour of the original single-tree loop:
# directories are merged recursively, files are overwritten.
function Sync-Tree {
    param(
        [string]$Root,
        [string]$Label,
        [string[]]$ItemList
    )

    Write-Host "[$Label]" -ForegroundColor Cyan

    foreach ($item in $ItemList) {
        $srcPath = Join-Path $Root $item
        $dstPath = Join-Path $target $item

        if (-not (Test-Path $srcPath)) {
            Write-Warning "Source not found, skipping: $item"
            continue
        }

        if ($DryRun) {
            Write-Host "  [DRY-RUN] Would sync: $item"
            if (Test-Path $srcPath -PathType Container) {
                Get-ChildItem $srcPath -Recurse -File |
                    Where-Object { $_.FullName -notmatch '\\__pycache__\\' } |
                    ForEach-Object {
                        $rel = $_.FullName.Substring($Root.Length + 1).Replace("\", "/")
                        $dstFile = Join-Path $target $rel
                        $exists = if (Test-Path $dstFile) { "(overwrite)" } else { "(new)" }
                        Write-Host "      $rel $exists"
                    }
            } else {
                $exists = if (Test-Path $dstPath) { "(overwrite)" } else { "(new)" }
                Write-Host "      $item $exists"
            }
        } else {
            if (Test-Path $srcPath -PathType Container) {
                # Exclude __pycache__ (Pip-style artifacts not meant to be installed).
                Get-ChildItem $srcPath -Recurse -File |
                    Where-Object { $_.FullName -notmatch '\\__pycache__\\' } |
                    ForEach-Object {
                        $rel = $_.FullName.Substring($Root.Length + 1)
                        $dstFile = Join-Path $target $rel
                        $dstDir = Split-Path -Parent $dstFile
                        if (-not (Test-Path $dstDir)) {
                            New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
                        }
                        Copy-Item -Path $_.FullName -Destination $dstFile -Force
                    }
            } else {
                Copy-Item -Path $srcPath -Destination $dstPath -Force
            }
            Write-Host "  Synced: $item"
        }
    }
}

if ($syncHarness) { Sync-Tree -Root $sourceHarness -Label "harness" -ItemList $harnessItems }
if ($syncKaggle)  { Sync-Tree -Root $sourceKaggle   -Label "kaggle"  -ItemList $kaggleItems  }

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
