---
name: kaggle-competition
description: >
  Universal Kaggle competition harness: a 5-node pipeline
  (DataIngestion -> DataProcessing -> Experimentation -> Evaluation ->
  DeploymentSync) that works for both Traditional ML/DS competitions and
  Generative AI (LLM) competitions, switching automatically between file
  and notebook/code submission. Reuses the kaggle-notebook skill for all
  Kaggle connectivity (credentials, workspace, notebook injection, privacy).
  Triggered by the `/competition` command.
---

# Kaggle Competition harness

A universal, node-based harness for Kaggle competitions. It runs the full
competition lifecycle as a 5-node pipeline and an optimization loop, and it
supports two competition flavors with two submission paths each:

| Flavor | `ai_mode` | Typical metric | Submission path |
|---|---|---|---|
| Traditional ML / DS | `ml` | f1, roc_auc, rmse | `file` (submission.csv) or `notebook` |
| Generative AI / LLM | `genai` | bertscore, ragas, LLM-as-a-Judge | `notebook` (code competition) or `file` |

It reuses the existing `kaggle-notebook` skill for **all** Kaggle connectivity:
credentials, the `~/kaggle-workspace` location, notebook injection,
`is_private=true` enforcement, and path-traversal-safe slugs. No Kaggle code
is duplicated.

---

## The 5 nodes

1. **DataIngestion_Node** — access and read raw data from `/kaggle/input/<comp>`.
   - ML: load CSVs into pandas/Polars DataFrames.
   - GenAI: load instruction JSONL corpora, large text, or vector stores.
   - This node also **detects** the submission mode (`file` vs `notebook`) and
     the flavor (`ml` vs `genai`) and writes them to the global state.

2. **DataProcessing_Node** — transform raw data into the exact shape the
   training engine needs.
   - ML: null imputation, normalization, feature engineering.
   - GenAI: tokenization, chunking, embeddings, ChatML prompt structuring.

3. **Experimentation_Node** *(the optimization loop)* — train, tune, and read
   Jupyter stdout to evaluate results. The agent regenerates this node's code
   each iteration based on `Evaluation_Node` feedback.
   - ML: LightGBM / Random Forest training, hyperparameter search, CV.
   - GenAI: LoRA/QLoRA fine-tuning, generation probes, temperature/Top-P tuning.

4. **Evaluation_Node** — compute the local success metric that drives
   `best_local_score` in the global state.
   - ML: F1, ROC-AUC, RMSE.
   - GenAI: RAGAS (RAG), BERTScore, LLM-as-a-Judge.

5. **DeploymentSync_Node** — validate `kernel-metadata.json` and safely run
   `kaggle kernels push -p` (notebook) or `kaggle competitions submit`
   (file). Only fires when the submission gate passes.

---

## Metric emission convention (REQUIRED)

For the harness to read Jupyter stdout reliably, generated code MUST print a
parseable metric marker:

```python
print(f"#METRIC:<name>=<float>")
```

Examples:
```python
print(f"#METRIC:f1={score:.4f}")
print(f"#METRIC:rmse={rmse:.4f}")        # minimize=True in state
print(f"#METRIC:bertscore={score:.4f}")
print(f"#METRIC:ragas_faithfulness={val:.4f}")
```

The harness parses every `#METRIC:name=value` line in the notebook's stdout and
selects the one matching `state.primary_metric` (last match wins if multiple).
Direction-aware via `state.minimize` (RMSE → `minimize=true`, lower wins).

---

## Global state (`competition_state.json`)

Lives in the competition workspace dir (`$KAGGLE_WORKSPACE/competitions/<comp>/`).
Schema:

```json
{
  "competition": "<comp>",
  "submission_mode": "file | notebook",
  "ai_mode": "ml | genai",
  "current_node": "ingestion | processing | experimentation | evaluation | deployment",
  "primary_metric": "f1",
  "minimize": false,
  "best_local_score": null,
  "best_iteration": null,
  "iterations": 0,
  "max_iterations": 1,
  "plateau_patience": 2,
  "history": [{"iteration": 1, "metric": "f1", "score": 0.83, "improved": true}],
  "metric_gate": "improve",
  "metric_threshold": null,
  "last_stdout": ""
}
```

The state is **resumable**: re-running `run` reloads it and continues.

---

## Optimization loop & termination

`run --max-iters N`:
- `N == 1` (default): **single-pass** mode — walks all 5 nodes once and
  deploys (generates one submission). No optimization gate applies.
- `N > 1`: **optimization** mode — runs
  `Experimentation -> Evaluation -> (gate)` up to `N` iterations.
  The gate decides each round:
  - `done` — `iterations >= max_iterations`
  - `stop` — no improvement for `plateau_patience` consecutive iters
  - `iterate` — keep optimizing

Deployment fires at the end **only if at least one iteration strictly improved
over the prior best** (the submission gate). Otherwise `DeploymentSync_Node` is
skipped with a warning — preventing wasted pushes.

`--simulate {improve,constant,degrade}` is a **dry-run only** knob that fakes
the metric trajectory so the loop and gate can be exercised offline:

| `--simulate` | trajectory | effect |
|---|---|---|
| `improve` (default) | metric gets better each iter | gate passes -> deploy |
| `constant` | metric flat | plateau -> stop, skip deploy |
| `degrade` | metric worsens | no improvement -> skip deploy |

---

## Submission routing

`submit --mode {auto,file,notebook}`:
- `file`: `kaggle competitions submit -f <file> -m <msg> <comp>` (needs `--from <file>`).
- `notebook`: `kaggle kernels push -p <comp-dir>` after injecting `code.py`.
- `auto` (default): `file` if `--from <file>` is supplied, else `notebook`.

This single rule covers classic CSV submissions AND modern GenAI code
competitions (where the notebook is the submission and Kaggle scores it).

---

## Cell segregation

The competition harness uses the **percent-format** cell delimiter convention
(`# %%`) inside ``code.py`` so each pipeline step can render as a readable
sequence of notebook cells on Kaggle:

- ``_append_code`` prepends a ``# %%`` marker before every node's rendered
  code. The assembled ``code.py`` stays a single editable flat file (the source
  of truth) while the markers tell the injection layer where to split.
- On push, ``kaggle_nb.set_notebook_code`` splits the flat ``code.py`` on
  ``# %%`` lines into separate code cells.  The marker lines themselves are
  **stripped** from the published notebook. The ML templates use narrative
  Markdown headings and decision logs instead of placing ``*_Node`` labels in
  generated cell contents; node names remain available in CLI output/state.
- A ``# %% [markdown]`` marker (optional) produces a **markdown cell** — useful
  for narrative or instructions between pipeline steps.  Leading ``# `` comment
  prefixes are stripped so the notebook renders clean Markdown.
- The file header in ``code.py`` (``# code.py - competition ...``) is a
  comment-only preamble and is **not** rendered as a cell, keeping the
  notebook clean.  Any non-comment code before the first ``# %%`` is preserved
  as a cell, so the agent can safely add imports/setup at the top.
- When no ``# %%`` marker exists anywhere in ``code.py``, the whole file is a
  single code cell (**backward-compatible** with scratch notebooks and the
  ``write-code`` / ``push`` commands in ``kaggle-notebook``).

This means every run of ``/competition`` produces a **well-segregated, readable
notebook** — multiple documented cells per pipeline step, with each iteration's
experimentation/evaluation pair in its own sequence.

---

## Helper CLI

Location: `opencode-kaggle/skills/kaggle-competition/scripts/kaggle_comp.py`

```text
kaggle_comp.py --help
kaggle_comp.py setup
kaggle_comp.py list [pattern] [--dry-run]
kaggle_comp.py files <comp> [--dry-run]
kaggle_comp.py init <comp> [--title T] [--gpu] [--internet] [--mode ml|genai] [--submission auto|file|notebook] [--max-iters N] [--force]
kaggle_comp.py data <comp> [--to DIR] [--file F] [--dry-run]
kaggle_comp.py context <comp> [--top N] [--list-only] [--dry-run]
kaggle_comp.py detect <comp> [--mode ml|genai] [--from F] [--dry-run]
kaggle_comp.py render <comp> <node> [--mode ml|genai] [--to F]
kaggle_comp.py state <comp> [--show] [--update-metric NAME=VAL]
kaggle_comp.py run <comp> [--mode ml|genai] [--submission auto|file|notebook] [--from F] [--max-iters N] [--plateau-patience P] [--simulate improve|constant|degrade] [--dry-run]
kaggle_comp.py submit-file <comp> --from <file> -m "msg" [--dry-run]
kaggle_comp.py push-notebook <comp> [--dry-run]
kaggle_comp.py submit <comp> [--mode auto|file|notebook] [--from F] [-m msg] [--dry-run]
kaggle_comp.py status <comp> [--dry-run]
kaggle_comp.py leaderboard <comp> [--dry-run]
```

`<node>` for `render` is one of:
`ingestion | processing | experimentation | evaluation | deployment`.

Environment overrides (same as `kaggle-notebook`):
- `KAGGLE_WORKSPACE` — workspace root (default `~/kaggle-workspace`)
- `KAGGLE_CONFIG_DIR` — kaggle credentials dir (default `~/.kaggle`)

Competition workspace layout (all OUTSIDE any git repo):
```
~/kaggle-workspace/competitions/<comp>/
├── competition_state.json      # global state (resumable)
├── notebook.ipynb               # private notebook (is_private=true)
├── kernel-metadata.json         # competition_sources=[<comp>]
├── code.py                      # editable mirror (single source of truth)
├── context/                     # top-voted public notebook digests
└── data/                        # downloaded competition data
```

---

## Recommended workflow (for the opencode agent)

When the user asks to participate in a Kaggle competition:

1. **Setup** (one-time, only if not done):
   ```powershell
   python opencode-kaggle\skills\kaggle-notebook\scripts\kaggle_nb.py setup
   ```
2. **Init** the competition (scaffolds state + private notebook + metadata):
   ```powershell
   python opencode-kaggle\skills\kaggle-competition\scripts\kaggle_comp.py init <comp> --mode ml --submission notebook --gpu --internet
   ```
3. **Read top-voted public notebooks for context before writing `code.py`:**
   ```powershell
   python ...\kaggle_comp.py context <comp> --top 5
   ```
   This ranks public kernels by votes, pulls them into the workspace, and writes
   readable `.py` digests under `competitions/<comp>/context/`. Review these for
   schema discoveries, validation strategy, and modeling ideas before coding.
4. **(Optional) detect / fetch data:**
   ```powershell
   python ...\kaggle_comp.py detect <comp>
   python ...\kaggle_comp.py data <comp>
   ```
5. **Run the pipeline** (single-pass to produce a first submission):
   ```powershell
   python ...\kaggle_comp.py run <comp> --mode ml --submission notebook
   ```
   Or an optimization loop (offline-safe with `--dry-run`):
   ```powershell
   python ...\kaggle_comp.py run <comp> --mode genai --max-iters 5 --simulate improve
   ```
6. **Push / submit explicitly** if needed:
   ```powershell
   python ...\kaggle_comp.py submit <comp> --mode notebook
   python ...\kaggle_comp.py submit <comp> --mode file --from submission.csv -m "v1"
   ```
7. **Status / leaderboard:**
   ```powershell
   python ...\kaggle_comp.py status <comp>
   python ...\kaggle_comp.py leaderboard <comp>
   ```
8. Use `--dry-run` to validate any push/submit without hitting the Kaggle API.

The agent is expected to **edit `code.py` in the workspace between iterations**
to improve the model; the harness re-injects it into the notebook on push.

---

## Rules — must always hold (inherited from `kaggle-notebook`)

- DO NOT commit anything under `~/kaggle-workspace` (or the competition
  workspace) to git. The `.gitignore` already excludes `kaggle-workspace/` and
  `kaggle.json`.
- DO NOT add notebook code, credentials, or `kaggle.json` to the repository.
- Always keep `is_private=true` in `kernel-metadata.json`. The helper forces it.
- Always keep `competition_sources=[<comp>]` for notebook submissions.
- Competition names are validated with the same path-traversal-safe `validate_slug`
  used by `kaggle-notebook`.
- The ML templates bound input discovery, EDA samples/output, correlation plots,
  model validation/training budgets, diagnostic prediction batches, and CSV
  formula-like values to keep generated notebooks safer on untrusted or large
  competition inputs.
- If `kaggle` is not installed, instruct the user to run the `kaggle-notebook`
  setup (`setup.ps1` / `kaggle_nb.py setup`).
- When generating Python code, favour Kaggle-preinstalled libraries: `numpy`,
  `pandas`, `scikit-learn`, `xgboost`, `lightgbm`, `torch`, `transformers`,
  `peft`, `trl`, `datasets`, `bert_score`, `ragas`, `jury`, etc.
- Generated evaluation code MUST print a `#METRIC:<name>=<float>` marker so the
  harness can parse `best_local_score`.

---

## Flags / features

| Flag / feature            | Purpose                                                      |
|---------------------------|--------------------------------------------------------------|
| `--gpu` (init)            | Enable GPU on the kernel (`enable_gpu: true`)                |
| `--internet` (init)       | Enable internet (`enable_internet: true`)                    |
| `--mode ml\|genai`        | Pick node templates + default primary metric                 |
| `--submission auto\|file\|notebook` | Choose submission path (auto routes on `--from`)    |
| `--max-iters N` (run)     | 1 = single-pass deploy; >1 = optimization loop               |
| `--plateau-patience P`    | Consecutive non-improving iters before stop (default 2)      |
| `--simulate ...` (run)    | Dry-run only: fake metric trajectory (improve/constant/degrade) |
| `--dry-run` (push/submit) | Print the kaggle command without executing it                 |
| `--force` (init)          | Overwrite an existing scaffold                               |

Use `--dry-run` whenever you want to validate a command without hitting the
Kaggle API (e.g. during evaluation without credentials).
