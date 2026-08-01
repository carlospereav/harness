# Harness

**A safety-first OpenCode harness for disciplined software delivery and private Kaggle workflows.**

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PowerShell](https://img.shields.io/badge/installer-PowerShell-5391FE.svg)](sync.ps1)
[![Python](https://img.shields.io/badge/helpers-Python-3776AB.svg)](opencode-kaggle/)

Harness turns an OpenCode session into a repeatable workflow:

```text
investigate -> plan -> approve -> implement -> evaluate -> security review -> deliver
```

It also brings private Kaggle notebook editing and a universal competition pipeline into the same command-driven setup.

## What lives here?

This repository contains two deliberately decoupled source trees. Both are installed side-by-side into the global OpenCode configuration at `~/.config/opencode/`.

| Source tree | Command surface | Purpose |
| --- | --- | --- |
| `opencode/` | `/harness` | Generic plan, implementation, evaluation, security-review, commit, and push workflow for any project. |
| `opencode-kaggle/` | `/kaggle`, `/competition` | Private Kaggle notebook automation and an ML/GenAI competition pipeline. |

The trees share the `harness` primary agent after installation, but each owns its own commands and skills in this repository.

## Quick start

```powershell
.\sync.ps1 -DryRun       # preview the install
.\sync.ps1              # install both source trees
```

Restart OpenCode after syncing, then use any command in any project:

```text
/harness "add pagination to /users"
/kaggle "create a private notebook that explores a dataset"
/competition "build and evaluate a private notebook for <competition>"
```

The installer never edits `opencode.jsonc`. Permissions are defined by the installed harness agent.

## The generic harness

### Workflow

```text
/harness <task>
     |
     v
 harness-plan       investigate the project and write acceptance criteria
     |
     v
 user approval      the plan must be explicitly approved
     |
     v
 harness-implement  make the approved changes
     |
     v
 harness-evaluate   @evaluator verifies every criterion
     |       ^
     |       +-- failures return to implementation, up to 3 iterations
     v
 harness-security-review  @security-auditor audits git diff HEAD
     |
     +-- HIGH finding: fix, evaluate again, and re-audit
     v
 commit + push       each publishing action requires a fresh user approval
```

### Safety model

The primary agent is intentionally restrictive:

- **Deny by default:** commands not explicitly allow-listed ask for approval.
- **Task allowlist:** only the read-only `evaluator` and `security-auditor` subagents can be spawned.
- **Publish ask gate:** `git commit`, `git push`, and GitHub publishing operations require explicit approval.
- **Destructive denies:** force-push, hard reset, branch/tag deletion, stash clearing, and similar operations are denied.
- **Chaining protection:** shell separators such as `;`, `&&`, `||`, and `|` are denied by the permission rules.

The installed agents are:

| Agent | Role |
| --- | --- |
| `harness` | Primary workflow coordinator with baked-in permissions. |
| `evaluator` | Hidden, read-only rubric runner. It verifies criteria but cannot edit, commit, or push. |
| `security-auditor` | Hidden, read-only diff auditor that classifies findings as HIGH, MEDIUM, or LOW. |

## Kaggle extensions

The Kaggle tree is self-contained and reuses `kaggle-notebook` for credentials, workspace handling, notebook injection, privacy, and slug validation.

### `/kaggle`: private notebook editing

The `kaggle-notebook` skill scaffolds, edits, pulls, pushes, and inspects Kaggle notebooks through `kaggle_nb.py`.

```powershell
python opencode-kaggle\skills\kaggle-notebook\scripts\setup.ps1
python opencode-kaggle\skills\kaggle-notebook\scripts\kaggle_nb.py new <slug> --title "<title>" --gpu --internet
python opencode-kaggle\skills\kaggle-notebook\scripts\kaggle_nb.py write-code <slug> --from code.py
python opencode-kaggle\skills\kaggle-notebook\scripts\kaggle_nb.py push <slug> --dry-run
```

Available helper operations include `setup`, `new`, `pull`, `write-code`, `append-code`, `push`, `status`, `output`, and `list`.

Privacy is a design constraint, not a convention:

- Kaggle metadata is forced to `is_private=true`.
- Notebook code lives outside this repository, by default under `~/kaggle-workspace`.
- Credentials and notebook workspaces are protected by `.gitignore`.
- `--dry-run` validates pushes without contacting Kaggle.
- `# %%` and `# %% [markdown]` delimiters can split `code.py` into readable notebook cells; files without markers remain backward-compatible single-cell notebooks.

### `/competition`: universal ML and GenAI pipeline

The `kaggle-competition` skill supports both traditional data-science competitions and Generative AI/code competitions, with file or notebook submission routes.

```text
DataIngestion -> DataProcessing -> Experimentation -> Evaluation -> DeploymentSync
```

| Node | Responsibility |
| --- | --- |
| `DataIngestion_Node` | Discover and read competition inputs; detect ML/GenAI flavor and submission mode. |
| `DataProcessing_Node` | Impute, normalize, engineer features, tokenize, chunk, embed, or structure prompts. |
| `Experimentation_Node` | Train, tune, probe, or fine-tune; feeds the optimization loop. |
| `Evaluation_Node` | Emit the local metric that updates `best_local_score`. |
| `DeploymentSync_Node` | Validate metadata and submit a file or push a private notebook only when the gate passes. |

#### Plan before implementation

Competition work has its own persistent approval workflow:

```powershell
python opencode-kaggle\skills\kaggle-competition\scripts\kaggle_comp.py init <comp> --mode ml --submission notebook
python opencode-kaggle\skills\kaggle-competition\scripts\kaggle_comp.py context <comp> --top 5
python opencode-kaggle\skills\kaggle-competition\scripts\kaggle_comp.py plan <comp>
# inspect and present plan.md; wait for explicit user approval
python opencode-kaggle\skills\kaggle-competition\scripts\kaggle_comp.py plan <comp> --approve
python opencode-kaggle\skills\kaggle-competition\scripts\kaggle_comp.py run <comp> --require-plan
```

`plan.md` records schema discoveries, node approaches, validation, metrics, budgets, risks, and acceptance criteria. Approval is bound to the exact plan contents and relevant run configuration. Use `--force` to replace a plan; use `--allow-unplanned` only for an explicit legacy-recovery bypass.

The `context` command ranks public notebooks by votes and writes readable digests under the competition workspace's `context/` directory before implementation begins.

#### Metrics, state, and optimization

Generated evaluation code must print a parseable marker:

```python
print(f"#METRIC:f1={score:.4f}")
```

The resumable `competition_state.json` tracks the current node, flavor, submission mode, primary metric, direction, iterations, best score, plan approval, and history. With `--max-iters N` greater than one, the harness loops through experimentation and evaluation until it reaches the iteration limit or a plateau. Deployment occurs only after a strict improvement; `--simulate improve|constant|degrade` exercises this gate offline.

#### Submission routing

```text
submit --mode file     -> kaggle competitions submit -f <file> ...
submit --mode notebook -> kaggle kernels push -p <competition-workspace>
submit --mode auto     -> file when --from is supplied, otherwise notebook
```

The assembled `code.py` is the editable source of truth. Percent-format markers (`# %%`) turn pipeline sections into separate code cells, and `# %% [markdown]` creates Markdown cells. Node labels stay readable in the notebook while the flat source remains easy to edit.

The competition helper supports:

```text
setup, list, files, init, plan, data, context, detect, render, state,
run, submit-file, push-notebook, submit, status, leaderboard
```

Use `--dry-run` for push, submit, data, and remote status operations when working without credentials or network access.

## Installation and synchronization

From the repository root:

```powershell
.\sync.ps1 -DryRun
.\sync.ps1
.\sync.ps1 -OnlyHarness
.\sync.ps1 -OnlyKaggle
```

`sync.ps1` performs an additive merge into `~/.config/opencode/`, overwriting source files while leaving unrelated installed files alone. It excludes `__pycache__` artifacts and runs an **ask-gate self-check** before syncing the harness tree. The check verifies that required approval and destructive-deny patterns exist and are ordered correctly.

## Repository map

```text
harness/
├── opencode/                                  # generic harness source
│   ├── AGENTS.md                              # always-on workflow rules
│   ├── agents/
│   │   ├── harness.md                         # primary agent + permissions
│   │   ├── evaluator.md                       # read-only evaluation agent
│   │   └── security-auditor.md                # read-only security agent
│   ├── commands/harness.md                    # /harness
│   └── skills/
│       ├── harness-plan/SKILL.md
│       ├── harness-implement/SKILL.md
│       ├── harness-evaluate/SKILL.md
│       └── harness-security-review/SKILL.md
├── opencode-kaggle/                           # Kaggle source, decoupled
│   ├── commands/
│   │   ├── kaggle.md                          # /kaggle
│   │   └── competition.md                     # /competition
│   └── skills/
│       ├── kaggle-notebook/
│       │   ├── SKILL.md
│       │   └── scripts/                        # CLI, setup, smoke test
│       └── kaggle-competition/
│           ├── SKILL.md
│           ├── scripts/                        # CLI and smoke test
│           └── templates/                      # ML and GenAI node code
├── sync.ps1                                   # installer and safety check
├── .gitignore                                 # workspace and credential guard
├── LICENSE                                    # MIT
└── README.md
```

## Development and verification

When changing a Kaggle helper or template:

```powershell
python -m py_compile opencode-kaggle\skills\kaggle-notebook\scripts\kaggle_nb.py
python -m py_compile opencode-kaggle\skills\kaggle-competition\scripts\kaggle_comp.py
python .\opencode-kaggle\skills\kaggle-notebook\scripts\smoke_test.py -v
python .\opencode-kaggle\skills\kaggle-competition\scripts\smoke_test.py -v
.\sync.ps1 -DryRun
```

The smoke tests use throwaway workspaces, dry-run behavior, and no Kaggle credentials or network. They cover notebook privacy, path-traversal rejection, cell injection, competition templates, plan approval, metric parsing, optimization gates, submission routing, and remote-command dry runs.

## Security and privacy checklist

- Never commit `kaggle.json`, credentials, notebook source, competition state, or competition data.
- Keep Kaggle workspaces outside git; the repository ignores both `kaggle-workspace/` and `competitions/` as defense in depth.
- Keep notebook metadata private and preserve `competition_sources` for competition notebooks.
- Use `--dry-run` before any Kaggle push or submission.
- Let the harness evaluator and security auditor complete before publishing code.

## License

Released under the [MIT License](LICENSE).
