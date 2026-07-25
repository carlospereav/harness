---
name: kaggle-notebook
description: >
  Create, edit and push PRIVATE Kaggle notebooks (kernels) from opencode.
  Keeps notebook code in a workspace OUTSIDE any public git repo so it never
  leaks to GitHub. Triggered by the `/kaggle` command.
---

# Kaggle Notebook editing system

This skill lets opencode generate Python code and upload it to Kaggle as a
**private** notebook (kernel), in the most automatic way possible, without the
notebook source ever landing in a public GitHub repo.

---

## Key principles

1. **Privacy on Kaggle:** every notebook is pushed with `"is_private": "true"`
   in `kernel-metadata.json`.
2. **Privacy on GitHub:** notebook source code lives in a workspace **outside**
   any git repository — default `~/kaggle-workspace` (override with
   `KAGGLE_WORKSPACE`). Nothing from the workspace is EVER committed to git.
   The `.gitignore` of the harness repo already excludes it as a safeguard.
3. **Automation:** opencode writes Python code to `code.py`, the helper CLI
   injects it into `notebook.ipynb` and runs `kaggle kernels push` in one step.

---

## Prerequisites

Run once:
```powershell
.\opencode-kaggle\skills\kaggle-notebook\scripts\setup.ps1
```
This installs `kaggle` + `nbformat`, creates the workspace, and verifies
Kaggle credentials at `~/.kaggle/kaggle.json`.

If credentials are missing, get a token from
https://www.kaggle.com/settings -> API -> Create New Token, and save it as
`~/.kaggle/kaggle.json` (format: `{"username":"...","key":"..."}`).

---

## Helper CLI

Location: `opencode-kaggle/skills/kaggle-notebook/scripts/kaggle_nb.py`

```text
kaggle_nb.py --help
kaggle_nb.py setup
kaggle_nb.py new <slug> [--title T] [--gpu] [--internet] [--dataset OWNER/DATASET] [--force]
kaggle_nb.py pull <owner>/<slug>
kaggle_nb.py write-code <slug> --from <file.py>
kaggle_nb.py append-code <slug> --from <file.py>
kaggle_nb.py push <slug> [--dry-run]
kaggle_nb.py status <owner>/<slug>
kaggle_nb.py output <owner>/<slug> [--to DIR]
kaggle_nb.py list [user]
```

Environment overrides:
- `KAGGLE_WORKSPACE` — workspace root (default `~/kaggle-workspace`)
- `KAGGLE_CONFIG_DIR` — kaggle credentials dir (default `~/.kaggle`)

---

## Recommended workflow (for the opencode agent)

When the user asks to create or edit a Kaggle notebook:

1. **Scaffold** (only if the notebook does not already exist):
   ```powershell
   python opencode-kaggle\skills\kaggle-notebook\scripts\kaggle_nb.py new <slug> --title "<title>" [--gpu] [--internet]
   ```
   This creates `~/kaggle-workspace/<slug>/` with:
   - `notebook.ipynb` (empty)
   - `kernel-metadata.json` (PRIVATE, notebook kernel)
   - `code.py` (editable mirror)

2. **Generate code.** Write the requested Python code into
   `~/kaggle-workspace/<slug>/code.py` using the `write` tool.
   Never write the notebook contents into the git repo — always under the
   workspace.

3. **Push.** Inject `code.py` into the notebook and upload it privately:
   ```powershell
   python opencode-kaggle\skills\kaggle-notebook\scripts\kaggle_nb.py push <slug>
   ```
   The helper re-injects `code.py` whenever it is newer than
   `notebook.ipynb`, re-checks `is_private=true`, and runs
   `kaggle kernels push -p <workspace>/<slug>`.

4. **Check status / fetch output (optional):**
   ```powershell
   python ...\kaggle_nb.py status <owner>/<slug>
   python ...\kaggle_nb.py output <owner>/<slug>
   ```

5. **Editing an existing notebook:** use `pull` to fetch it into the
   workspace, edit `code.py`, then `push`.

---

## Rules — must always hold

- DO NOT commit anything under `~/kaggle-workspace` to git.
- DO NOT add notebook code, credentials, or `kaggle.json` to the repository.
- Always keep `is_private=true`. The helper refuses to push otherwise.
- If `kaggle` is not installed, instruct the user to run `setup.ps1`.
- If credentials are missing, instruct the user to create a Kaggle API token.
- When generating Python code, favour standard data-science libraries already
  available in Kaggle notebooks: `numpy`, `pandas`, `scikit-learn`,
  `matplotlib`, `seaborn`, `torch`, `tensorflow`, `xgboost`, etc. Kaggle
  notebooks come with them preinstalled.

---

## Flags / features

| Flag / feature        | Purpose                                              |
|-----------------------|------------------------------------------------------|
| `--gpu` (new)         | Enable GPU on the kernel (`enable_gpu: true`)       |
| `--internet` (new)    | Enable internet (`enable_internet: true`)            |
| `--dataset` (new)     | Attach dataset source(s) to metadata                 |
| `--dry-run` (push...) | Print the kaggle command without executing it        |
| `--workspace`         | Override workspace root for one invocation           |
| `--force` (new)       | Overwrite an existing scaffold                       |

Use `--dry-run` whenever you want to validate a command without hitting the
Kaggle API (e.g. during evaluation without credentials).