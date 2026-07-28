# AGENTS.md — harness repo (upstream source)

## Repo purpose
This repo is the **upstream source of truth** for two opencode extensions, both
synced into the global config (`~/.config/opencode/`) by `sync.ps1`:

| Tree | Installs to | Purpose |
|---|---|---|
| `opencode/` | `~/.config/opencode/{AGENTS.md,agents/,commands/,skills/}` | Generic dev harness: plan → implement → evaluate → security-review → commit+push |
| `opencode-kaggle/` | `~/.config/opencode/{commands/,skills/}` | Kaggle skills: `kaggle-notebook` (+`/kaggle`) and `kaggle-competition` (+`/competition`) |

When the user asks to fix/improve a skill, **edit the files in this repo**
(`opencode-kaggle/skills/<name>/...`), then run `sync.ps1` to deploy, then run
the skill's smoke test. Do NOT edit live files under `~/.config/opencode/` —
this repo is the source, the live tree is the deploy target.

## Layout
```
harness/
├── opencode/                       # generic harness (plan/implement/evaluate/security)
│   ├── AGENTS.md                   # global protocol (installed globally)
│   ├── agents/{harness,evaluator,security-auditor}.md
│   ├── commands/harness.md
│   └── skills/harness-{plan,implement,evaluate,security-review}/SKILL.md
├── opencode-kaggle/                # Kaggle extensions (decoupled)
│   ├── commands/{kaggle,competition}.md
│   └── skills/
│       ├── kaggle-notebook/        # connectivity: credentials, push, privacy
│       │   ├── SKILL.md
│       │   └── scripts/  (kaggle_nb.py, setup.ps1, smoke_test.py)
│       └── kaggle-competition/     # 5-node pipeline, reuses kaggle-notebook
│           ├── SKILL.md
│           ├── scripts/  (kaggle_comp.py, smoke_test.py)
│           └── templates/ (genai/, ml/  — one *.py.tmpl per node)
├── sync.ps1                        # deploy both trees to ~/.config/opencode/
└── README.md
```

## Standard change workflow (follow for every fix here)
1. Edit the source file(s) under `opencode-kaggle/skills/...` (or `opencode/...`).
2. Run `python -m py_compile <changed>.py` to syntax-check helper scripts.
3. Run the relevant smoke test from the repo root (no network, no credentials):
   ```powershell
   python .\opencode-kaggle\skills\kaggle-notebook\scripts\smoke_test.py -v
   python .\opencode-kaggle\skills\kaggle-competition\scripts\smoke_test.py -v
   ```
   Exit code 0 = pass. Add a smoke case for any new behavior.
4. Deploy: `.\sync.ps1` (both trees) or `.\sync.ps1 -OnlyKaggle`.
5. Commit. Prefix the message with the skill name, e.g.
   `kaggle-competition: fix kernel id to include username`.

## Known bugs in the kaggle skills — high-priority upstream fixes (ALL FIXED 2026-07-28)

All 9 defects below were patched in the commit that contains this note. The
fixes span `kaggle_nb.py`, `kaggle_comp.py`, 4 templates, and both smoke tests.

### 1. Forced `is_private=true` blocks medal eligibility — ✅ FIXED
`kaggle_nb.default_metadata` now accepts a `private` param (default `False`).
`kaggle_comp.comp_metadata` passes `private=False` so competition notebooks
default to public. `cmd_push` in `kaggle_nb.py` no longer re-forces private
when `is_private` is explicitly `"false"`; `_validate_meta_for_deploy` stopped
re-forcing it too. Scratch notebooks via `/kaggle new` still stay private
(`private=True` in `cmd_new`).

### 2. Kernel `id` is the bare slug → `Invalid slug` on push — ✅ FIXED
Added `_resolve_kaggle_username()` (tries `kaggle config view` → `KAGGLE_USERNAME`
env → `kaggle.json` → `credentials.json`). `default_metadata` now prefixes a
bare slug with `"<user>/"` when a username is resolvable. Explicit `owner/slug`
passes through unchanged.

### 3. `_fetch_notebook_stdout` was a `return ""` placeholder — ✅ FIXED
Implemented production-grade polling: `kaggle kernels status <id>` with 10s
intervals (max 60). On COMPLETE, downloads output to a temp dir via
`kaggle kernels output`, then parses JSONL `.log` files extracting the `"data"`
stream. Extracted parsing into testable `_parse_jsonl_log_files()`.

### 4. Ingestion hardcoded `/kaggle/input/<comp>` — ✅ FIXED
Both `ml/ingestion.py.tmpl` and `genai/ingestion.py.tmpl` now use
`os.walk("/kaggle/input")` recursively, print every file found, and read CSVs /
JSONL / TXT by name wherever they live under the mount.

### 5. No rules-acceptance step — ✅ FIXED
`cmd_data` now captures stderr from `kaggle competitions download`. On
non-zero exit with `403`/`Forbidden` in output, it prints a clear message:
"accept the rules first: https://www.kaggle.com/competitions/<comp>/rules"
with instructions.

### 6. New Kaggle SDK (v2.x) doesn't accept legacy `kaggle.json` — ✅ FIXED
Both `cmd_setup` functions (`kaggle_nb.py` and `kaggle_comp.py`) now report
`credentials.json` (OAuth) separately from `kaggle.json`. When both files
coexist, a clear warning is printed with instructions to move `kaggle.json`
aside.

### 7. `run` aborted on any push failure with no handoff — ✅ FIXED
`cmd_run` no longer `return rc` on push failure. Instead it sets a flag,
breaks the optimization loop, continues to render the DeploymentSync node,
assembles the notebook on disk, prints a recovery message pointing to
`push-notebook`, and returns exit code 1 (recoverable). The assembled notebook
is left on disk for manual push.

## Notebook/model quality (upstream template improvements) — ALL FIXED

### 8. Generic ML templates assumed the wrong schema — ✅ FIXED
`ml/experimentation.py.tmpl` now uses `HistGradientBoostingClassifier` which
handles categoricals and NaN natively. Target column detection is robust
(checks for `target*` prefix, falls back to last column). LabelEncoder handles
string targets. Feature columns exclude `id`-like columns.

### 9. Primary metric hardcoded to binary `f1` — ✅ FIXED
`ml/evaluation.py.tmpl` now auto-detects number of classes and uses
`average="macro"` for multiclass (>2) or `"binary"` for binary. The
`#METRIC:` marker uses the actual metric name (defaults to `"accuracy"` if F1
fails). Target column detection matches the experimentation template.

## Local user context (machine-specific; do not hardcode upstream)
- Kaggle username: `carlospereavega`. Use it only when prompting/pushing on
  this machine, never as a literal in committed upstream code.
- Auth (working): OAuth via `kaggle auth login` → `~/.kaggle/credentials.json`.
- Legacy `~/.kaggle/kaggle.json` has been moved aside to `kaggle.json.bak`; do
  not restore it unless the skill is patched to support legacy auth.

## Sync & test commands (cheat sheet)
```powershell
.\sync.ps1 -DryRun               # preview both trees
.\sync.ps1                       # install both to ~/.config/opencode/
.\sync.ps1 -OnlyKaggle           # install kaggle extensions only
python .\opencode-kaggle\skills\kaggle-notebook\scripts\smoke_test.py -v
python .\opencode-kaggle\skills\kaggle-competition\scripts\smoke_test.py -v
python -m py_compile .\opencode-kaggle\skills\kaggle-competition\scripts\kaggle_comp.py
```
After editing skill scripts, always sync before declaring done, and run the
matching smoke test. See `~/.config/opencode/AGENTS.md` for the global harness
protocol that still governs plan/evaluate/security-review on changes here.