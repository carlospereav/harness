#!/usr/bin/env python3
"""kaggle_comp.py - Universal Kaggle competition harness.

Runs a 5-node pipeline (DataIngestion -> DataProcessing -> Experimentation ->
Evaluation -> DeploymentSync) with an optimization loop, supporting both
Traditional ML/DS competitions and Generative AI (LLM) competitions, and
switching automatically between file and notebook/code submission.

It reuses the existing `kaggle-notebook` skill for ALL Kaggle connectivity:
credentials, workspace location, notebook injection, is_private enforcement,
and path-traversal-safe slugs. Nothing Kaggle-related is re-implemented.

Usage:
    kaggle_comp.py --help
    kaggle_comp.py setup
    kaggle_comp.py list [pattern] [--dry-run]
    kaggle_comp.py files <comp> [--dry-run]
    kaggle_comp.py init <comp> [--title T] [--gpu] [--internet] \\
                   [--mode ml|genai] [--submission auto|file|notebook] \\
                   [--max-iters N] [--force]
    kaggle_comp.py data <comp> [--to DIR] [--file F] [--dry-run]
    kaggle_comp.py detect <comp> [--mode ml|genai] [--from F] [--dry-run]
    kaggle_comp.py render <comp> <node> [--mode ml|genai] [--to F]
    kaggle_comp.py state <comp> [--show] [--update-metric NAME=VAL]
    kaggle_comp.py run <comp> [--mode ml|genai] [--submission auto|file|notebook] \\
                   [--from F] [--max-iters N] [--plateau-patience P] \\
                   [--simulate improve|constant|degrade] [--dry-run]
    kaggle_comp.py submit-file <comp> --from <file> -m "msg" [--dry-run]
    kaggle_comp.py push-notebook <comp> [--dry-run]
    kaggle_comp.py submit <comp> [--mode auto|file|notebook] [--from F] \\
                   [-m msg] [--dry-run]
    kaggle_comp.py status <comp> [--dry-run]
    kaggle_comp.py leaderboard <comp> [--dry-run]

Environment (same as kaggle-notebook):
    KAGGLE_WORKSPACE  workspace root (default: ~/kaggle-workspace)
    KAGGLE_CONFIG_DIR kaggle credentials dir (default: ~/.kaggle)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# Reuse kaggle-notebook for connectivity (no Kaggle code is duplicated).
# Resolves the sibling kaggle-notebook/scripts dir RELATIVE to this file so it
# works both in the repo and after sync.ps1 copies it to ~/.config/opencode/.
# --------------------------------------------------------------------------- #
_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE.parent / "templates"
_KAGGLE_NB_DIR = _HERE.parent.parent / "kaggle-notebook" / "scripts"
if str(_KAGGLE_NB_DIR) not in sys.path:
    sys.path.insert(0, str(_KAGGLE_NB_DIR))

import kaggle_nb  # type: ignore  # noqa: E402  (connectivity reuse)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
COMP_SUBDIR = "competitions"
STATE_FILE = "competition_state.json"

# Node slugs -> user-facing node names (kept stable for stdout/output checks).
NODE_DISPLAY = {
    "ingestion": "DataIngestion_Node",
    "processing": "DataProcessing_Node",
    "experimentation": "Experimentation_Node",
    "evaluation": "Evaluation_Node",
    "deployment": "DeploymentSync_Node",
}
NODES = list(NODE_DISPLAY.keys())

# Default (primary_metric, minimize) per ai_mode. minimize=True => lower wins.
DEFAULT_PRIMARY_METRIC = {
    "ml": ("f1", False),
    "genai": ("bertscore", False),
}

# #METRIC:<name>=<float>  (float allows scientific notation)
_METRIC_RE = re.compile(
    r"#METRIC:([A-Za-z0-9_]+)=(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
)


# --------------------------------------------------------------------------- #
# Workspace / competition dirs (reuse kaggle_nb safety primitives)
# --------------------------------------------------------------------------- #
def _default_workspace() -> Path:
    # Mirror kaggle_nb.DEFAULT_WORKSPACE so we don't rely on a private attribute.
    return Path(os.environ.get("KAGGLE_WORKSPACE", Path.home() / "kaggle-workspace"))


def comp_root(comp: str, workspace: str | None = None) -> Path:
    """Return (creating) the competition workspace dir. Validates the slug."""
    kaggle_nb.validate_slug(comp)  # path-traversal safe
    ws = kaggle_nb.workspace_root(workspace)
    d = ws / COMP_SUBDIR / comp
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path(comp: str, workspace: str | None = None) -> Path:
    return comp_root(comp, workspace) / STATE_FILE


# --------------------------------------------------------------------------- #
# Global state
# --------------------------------------------------------------------------- #
def default_state(comp: str, ai_mode: str = "ml",
                  submission_mode: str = "notebook") -> dict:
    metric, minimize = DEFAULT_PRIMARY_METRIC.get(ai_mode, ("f1", False))
    return {
        "competition": comp,
        "submission_mode": submission_mode,
        "ai_mode": ai_mode,
        "current_node": "ingestion",
        "primary_metric": metric,
        "minimize": minimize,
        "best_local_score": None,
        "best_iteration": None,
        "iterations": 0,
        "max_iterations": 1,
        "plateau_patience": 2,
        "history": [],
        "metric_gate": "improve",
        "metric_threshold": None,
        "last_stdout": "",
    }


def load_state(comp: str, workspace: str | None = None) -> dict | None:
    p = state_path(comp, workspace)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(comp: str, state: dict, workspace: str | None = None) -> None:
    p = state_path(comp, workspace)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    print(f"  wrote {p}")


# --------------------------------------------------------------------------- #
# Competition metadata (extends kaggle_nb.default_metadata with competition_sources)
# --------------------------------------------------------------------------- #
def comp_metadata(comp: str, *, title: str | None = None, gpu: bool = False,
                  internet: bool = False, datasets=()) -> dict:
    meta = kaggle_nb.default_metadata(
        comp, title=title, gpu=gpu, internet=internet, datasets=datasets,
        private=False,  # competition notebooks must be public for medals
    )
    meta["competition_sources"] = [comp]  # attach to competition
    return meta


def _validate_meta_for_deploy(d: Path, comp: str) -> None:
    """Pre-flight check for DeploymentSync. Fixes competition_sources + kernel_type.
    Does NOT re-force privacy — competition notebooks default to public (medals)."""
    try:
        meta = kaggle_nb.read_metadata(d)
    except FileNotFoundError:
        print("  [validate] metadata missing; creating competition metadata")
        kaggle_nb.write_metadata(d, comp_metadata(comp))
        kaggle_nb.write_empty_notebook(d)
        return
    issues = []
    # No longer re-forces is_private — honors whatever the metadata says.
    if comp not in (meta.get("competition_sources") or []):
        issues.append(f"competition_sources missing {comp}")
        meta["competition_sources"] = [comp]
    if meta.get("kernel_type") not in ("notebook", None):
        issues.append("kernel_type != notebook")
        meta["kernel_type"] = "notebook"
    if issues:
        print("  [validate] fixing: " + "; ".join(issues))
        kaggle_nb.write_metadata(d, meta)
    else:
        priv = meta.get("is_private", "false")
        print(f"  [validate] metadata OK (is_private={priv}, competition_sources set)")


# --------------------------------------------------------------------------- #
# Metric parsing + gate logic
# --------------------------------------------------------------------------- #
def parse_metrics(stdout: str) -> list[tuple[str, float]]:
    """Return list of (name, float) for every #METRIC:name=value marker."""
    return [(m.group(1), float(m.group(2))) for m in _METRIC_RE.finditer(stdout or "")]


def _pick_primary_metric(metrics: list[tuple[str, float]], primary: str) -> float | None:
    for name, val in reversed(metrics):
        if name == primary:
            return val
    if metrics:
        return metrics[-1][1]
    return None


def is_better(new: float, old: float | None, minimize: bool) -> bool:
    if old is None:
        return True  # first score sets the baseline
    if minimize:
        return new < old
    return new > old


def gate_decision(state: dict, score: float, max_iters: int,
                  patience: int) -> tuple[str, bool, dict]:
    """Apply the optimization gate.

    Returns (decision, improved, new_state):
      decision in {"iterate", "done", "stop"}
      improved   True iff `score` strictly improved over the prior best
                 (False on the first/baseline iteration).
    """
    st = dict(state)
    st["history"] = list(state.get("history", []))
    best = st.get("best_local_score")
    minimize = st.get("minimize", False)

    # "improved" requires a STRICT improvement over an existing best.
    improved = best is not None and is_better(score, best, minimize)

    # Update best if this score beats the current best (or sets baseline).
    if best is None or is_better(score, best, minimize):
        st["best_local_score"] = score
        st["best_iteration"] = st.get("iterations", 0) + 1

    st["iterations"] = st.get("iterations", 0) + 1
    st["history"].append({
        "iteration": st["iterations"],
        "metric": st.get("primary_metric", ""),
        "score": score,
        "improved": improved,
    })
    st["current_node"] = "evaluation"

    # Single-pass mode: done after the first iteration.
    if max_iters == 1 and st["iterations"] == 1:
        return ("done", improved, st)

    # Max iterations reached.
    if st["iterations"] >= max_iters:
        return ("done", improved, st)

    # Plateau: count trailing non-improving iterations.
    trailing = 0
    for h in reversed(st["history"]):
        if not h.get("improved"):
            trailing += 1
        else:
            break
    if trailing >= patience:
        return ("stop", improved, st)

    return ("iterate", improved, st)


# --------------------------------------------------------------------------- #
# Dry-run metric simulation
# --------------------------------------------------------------------------- #
def _fake_metric(simulate: str, iteration: int, minimize: bool) -> float:
    """Fake a metric trajectory for --dry-run loop exercise."""
    if simulate == "improve":
        v = (0.5 - 0.05 * (iteration - 1)) if minimize else (0.5 + 0.05 * (iteration - 1))
    elif simulate == "degrade":
        v = (0.5 + 0.05 * (iteration - 1)) if minimize else (0.8 - 0.05 * (iteration - 1))
    else:  # constant
        v = 0.5
    return round(v, 4)


# --------------------------------------------------------------------------- #
# Template rendering
# --------------------------------------------------------------------------- #
def render_template(node: str, mode: str, *, competition: str = "",
                    iteration: int = 1) -> str:
    if node not in NODE_DISPLAY:
        raise ValueError(f"unknown node: {node!r} (expected one of {NODES})")
    # Validate the competition name BEFORE interpolating it into Python source
    # (it lands inside a string literal in the ingestion templates). The
    # path-traversal-safe validate_slug rejects quotes/backslashes/separators,
    # so a crafted name cannot break out of the literal. Defense-in-depth at the
    # single interpolation point (covers cmd_render and _run_node).
    if competition:
        kaggle_nb.validate_slug(competition)
    tmpl_path = _TEMPLATES_DIR / mode / f"{node}.py.tmpl"
    if not tmpl_path.exists():
        raise FileNotFoundError(f"template not found: {tmpl_path}")
    text = tmpl_path.read_text(encoding="utf-8")
    return (text
            .replace("{{competition}}", competition)
            .replace("{{iteration}}", str(iteration)))


def _append_code(d: Path, code: str) -> None:
    code_py = d / kaggle_nb.CODE_PY
    if code_py.exists():
        code_py.write_text(code_py.read_text(encoding="utf-8").rstrip() + "\n\n" + code + "\n",
                           encoding="utf-8")
    else:
        code_py.write_text(code + "\n", encoding="utf-8")


def _run_node(comp: str, node: str, mode: str, d: Path, state: dict,
              workspace: str | None, *, iteration: int = 1) -> None:
    """Render a node template, append it to code.py, advance the state."""
    print(f"== {NODE_DISPLAY[node]} ==")
    code = render_template(node, mode, competition=comp, iteration=iteration)
    _append_code(d, code)
    print(f"  [render] {node} -> {kaggle_nb.CODE_PY} (mode={mode}, iter={iteration})")
    state["current_node"] = node
    save_state(comp, state, workspace)


# --------------------------------------------------------------------------- #
# Push / submit primitives (reuse kaggle_nb._run for dry-run printing)
# --------------------------------------------------------------------------- #
def _kaggle_push(comp: str, d: Path, dry_run: bool) -> int:
    # Ensure notebook code is current (kaggle_nb.push already does this, but we
    # also enforce competition_sources here for competition notebooks).
    code_py = d / kaggle_nb.CODE_PY
    nb_path = kaggle_nb.empty_notebook_path(d)
    if code_py.exists():
        code_newer = not nb_path.exists() or code_py.stat().st_mtime > nb_path.stat().st_mtime
        if code_newer:
            print(f"  injecting {code_py.name} into notebook (code.py is newer)")
            kaggle_nb.set_notebook_code(d, code_py.read_text(encoding="utf-8"))
    _validate_meta_for_deploy(d, comp)
    return kaggle_nb._run(["kaggle", "kernels", "push", "-p", str(d)],
                          dry_run=dry_run, cwd=d)


def _submit_file(comp: str, file_path: str, message: str,
                 dry_run: bool) -> int:
    p = Path(file_path)
    if not p.exists() and not dry_run:
        print(f"  [error] submission file not found: {p}")
        return 1
    return kaggle_nb._run(
        ["kaggle", "competitions", "submit", "-f", str(p), "-m", message, comp],
        dry_run=dry_run,
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_setup(args: argparse.Namespace) -> int:
    print("== kaggle_comp setup ==")
    ws = kaggle_nb.workspace_root(args.workspace)
    print(f"  workspace: {ws}")
    (ws / COMP_SUBDIR).mkdir(parents=True, exist_ok=True)
    print(f"  kaggle CLI : {'OK' if kaggle_nb._kaggle_available() else 'MISSING'}")
    print(f"  kaggle API : {'OK' if kaggle_nb._kaggle_api_available() else 'MISSING'}")

    # Check both legacy and OAuth credentials.
    legacy = kaggle_nb.KAGGLE_CONFIG_DIR / "kaggle.json"
    oauth = kaggle_nb.KAGGLE_CONFIG_DIR / "credentials.json"
    has_legacy = legacy.exists()
    has_oauth = oauth.exists()
    print(f"  kaggle.json       : {legacy} ({'OK' if has_legacy else 'MISSING'})")
    print(f"  credentials.json  : {oauth} ({'OK' if has_oauth else 'MISSING'})")
    if has_legacy and has_oauth:
        print("  [WARN] Both kaggle.json (legacy) and credentials.json (OAuth) exist.")
        print("         Kaggle SDK v2+ prefers credentials.json. If API calls fail,")
        print(f"         move kaggle.json aside:  mv {legacy} {legacy}.bak")

    print(f"  templates  : {_TEMPLATES_DIR} ({'OK' if _TEMPLATES_DIR.exists() else 'MISSING'})")
    missing = [n for n in NODES
               for mode in ("ml", "genai")
               if not (_TEMPLATES_DIR / mode / f"{n}.py.tmpl").exists()]
    if missing:
        print(f"  [warn] missing template files: {missing}")
    else:
        print("  templates  : all 10 node templates present")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    return kaggle_nb._run(["kaggle", "competitions", "list"],
                          dry_run=args.dry_run)


def cmd_files(args: argparse.Namespace) -> int:
    kaggle_nb.validate_slug(args.comp)
    return kaggle_nb._run(["kaggle", "competitions", "files", args.comp],
                          dry_run=args.dry_run)


def cmd_init(args: argparse.Namespace) -> int:
    d = comp_root(args.comp, args.workspace)
    print(f"== init competition: {args.comp} -> {d} ==")
    if (state_path(args.comp, args.workspace)).exists() and not args.force:
        print("  already exists (use --force to overwrite)")
        return 1
    sub_mode = args.submission if args.submission != "auto" else "notebook"
    state = default_state(args.comp, ai_mode=args.mode, submission_mode=sub_mode)
    if args.max_iters is not None:
        state["max_iterations"] = args.max_iters
    save_state(args.comp, state, args.workspace)

    meta = comp_metadata(args.comp, title=args.title or args.comp,
                         gpu=args.gpu, internet=args.internet)
    kaggle_nb.write_metadata(d, meta)
    kaggle_nb.write_empty_notebook(d)
    (d / kaggle_nb.CODE_PY).write_text(
        "# code.py - competition " + args.comp +
        f" (mode={args.mode}, submission={sub_mode})\n"
        "# edit this, then run: kaggle_comp.py run " + args.comp + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {d / kaggle_nb.CODE_PY}")
    print("  notebook PUBLIC (is_private=false) — medals/lb eligible; competition_sources set")
    return 0


def cmd_data(args: argparse.Namespace) -> int:
    kaggle_nb.validate_slug(args.comp)
    if args.to:
        dest = Path(args.to)
    else:
        dest = comp_root(args.comp, args.workspace) / "data"
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["kaggle", "competitions", "download"]
    if args.file:
        cmd += ["-f", args.file]
    cmd += ["-p", str(dest), args.comp]

    if args.dry_run:
        return kaggle_nb._run(cmd, dry_run=True)

    # Real run: capture stderr to detect 403 (rules not accepted).
    import subprocess as _sp
    printable = " ".join(cmd)
    print(f"$ {printable}")
    proc = _sp.run(cmd, capture_output=True, text=True, encoding="utf-8")
    combined = (proc.stdout or "") + (proc.stderr or "")
    print(proc.stdout or "")
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        if "403" in combined or "Forbidden" in combined or "forbidden" in combined.lower():
            print()
            print("  [ERROR] Access denied (HTTP 403). You must accept the competition rules first:")
            print(f"           https://www.kaggle.com/competitions/{args.comp}/rules")
            print("  [ERROR] Open that URL in your browser, click 'I Understand and Accept',")
            print("          then re-run this command.")
        return proc.returncode
    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    comp_root(args.comp, args.workspace)
    ai = args.mode or "ml"
    sub = "file" if args.from_file else "notebook"
    print(f"MODE: submission={sub} ai_mode={ai}")
    state = load_state(args.comp, args.workspace)
    if state:
        state["submission_mode"] = sub
        state["ai_mode"] = ai
        save_state(args.comp, state, args.workspace)
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    # Mirror the other cmd_* verbs: validate the competition slug first
    # (path-traversal + template-injection safe). render_template re-validates
    # too, but the CLI entry validates explicitly for consistency.
    kaggle_nb.validate_slug(args.comp)
    if args.node not in NODE_DISPLAY:
        raise ValueError(f"unknown node: {args.node!r} (expected one of {NODES})")
    code = render_template(args.node, args.mode, competition=args.comp, iteration=args.iteration)
    if args.to:
        Path(args.to).write_text(code, encoding="utf-8")
        print(f"  wrote {args.to}")
    else:
        print(code)
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    state = load_state(args.comp, args.workspace)
    if state is None:
        print(f"  no state for {args.comp}; run `init {args.comp}` first")
        return 1
    if args.update_metric:
        if "=" not in args.update_metric:
            print("  [error] --update-metric expects NAME=VAL")
            return 1
        name, val = args.update_metric.split("=", 1)
        try:
            score = float(val)
        except ValueError:
            print(f"  [error] invalid metric value: {val!r}")
            return 1
        decision, improved, state = gate_decision(
            state, score, state.get("max_iterations", 1),
            state.get("plateau_patience", 2),
        )
        if name:
            state["primary_metric"] = name
        save_state(args.comp, state, args.workspace)
        print(f"  updated: {name}={score} improved={improved} decision={decision} "
              f"best={state['best_local_score']} iterations={state['iterations']}")
        if not args.show:
            return 0
    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    comp = args.comp
    d = comp_root(comp, args.workspace)
    state = load_state(comp, args.workspace)
    if state is None:
        sub_mode = args.submission if args.submission != "auto" else "notebook"
        state = default_state(comp, ai_mode=args.mode, submission_mode=sub_mode)
    # apply CLI overrides
    if args.max_iters is not None:
        state["max_iterations"] = args.max_iters
    if args.plateau_patience is not None:
        state["plateau_patience"] = args.plateau_patience
    if args.mode:
        state["ai_mode"] = args.mode
        # refresh default metric if user switched mode without setting a custom one
        m, mn = DEFAULT_PRIMARY_METRIC.get(args.mode, ("f1", False))
        state["primary_metric"] = m
        state["minimize"] = mn
    if args.submission != "auto":
        state["submission_mode"] = args.submission
    mode = state["ai_mode"]
    sub_mode = state["submission_mode"]
    save_state(comp, state, args.workspace)

    # reset code.py for a fresh assembled notebook
    code_py = d / kaggle_nb.CODE_PY
    code_py.write_text(
        f"# code.py - competition {comp} (mode={mode}, submission={sub_mode})\n",
        encoding="utf-8",
    )

    # Ingestion + Processing run ONCE (data setup).
    _run_node(comp, "ingestion", mode, d, state, args.workspace)
    _run_node(comp, "processing", mode, d, state, args.workspace)

    max_iters = max(1, int(state["max_iterations"]))
    patience = max(1, int(state["plateau_patience"]))
    improved_any = False
    push_failed = False
    i = 0
    while True:
        i += 1
        _run_node(comp, "experimentation", mode, d, state, args.workspace, iteration=i)
        _run_node(comp, "evaluation", mode, d, state, args.workspace, iteration=i)

        if args.dry_run:
            score = _fake_metric(args.simulate, i, state["minimize"])
            stdout = f"#METRIC:{state['primary_metric']}={score}\n"
        else:
            # Real run: push notebook, let Kaggle execute it, fetch stdout.
            rc = _kaggle_push(comp, d, dry_run=False)
            if rc != 0:
                print(f"  [run] notebook push failed (rc={rc})")
                print(f"  [run] The assembled notebook is left on disk at:")
                print(f"         {d / 'notebook.ipynb'}")
                print(f"  [run] After fixing auth or accepting the competition rules,")
                print(f"         run:  kaggle_comp.py push-notebook {comp}")
                push_failed = True
                break
            stdout = _fetch_notebook_stdout(comp, d)
            metrics = parse_metrics(stdout)
            score = _pick_primary_metric(metrics, state["primary_metric"])
            if score is None:
                print("  [run] no #METRIC: marker found in notebook stdout")
                print(f"  [run] The assembled notebook is left on disk at:")
                print(f"         {d / 'notebook.ipynb'}")
                print(f"  [run] Check the kernel output on Kaggle for errors, then")
                print(f"         update the metric manually with:")
                print(f"         kaggle_comp.py state {comp} --update-metric <name>=<val>")
                return 2

        state = load_state(comp, args.workspace)
        decision, improved, state = gate_decision(state, score, max_iters, patience)
        state["last_stdout"] = stdout[-2000:] if isinstance(stdout, str) else ""
        save_state(comp, state, args.workspace)
        if improved:
            improved_any = True
        print(f"  [iter {i}/{max_iters}] {state['primary_metric']}={score} "
              f"decision={decision} improved={improved} best={state['best_local_score']}")
        if decision in ("done", "stop"):
            break
        if i > max_iters + 5:  # safety valve against runaway loops
            print("  [safety] exceeded expected iterations; stopping")
            break

    # DeploymentSync node (renders the finalization cell, then pushes/submits).
    _run_node(comp, "deployment", mode, d, state, args.workspace, iteration=i)

    # Assemble the notebook from code.py.
    kaggle_nb.set_notebook_code(d, code_py.read_text(encoding="utf-8"))
    print(f"  [run] notebook assembled at {d / 'notebook.ipynb'}")

    # Push failure: notebook is already assembled; skip final submission.
    if push_failed:
        return 1  # non-zero but recoverable — notebook is on disk

    # Submission gate: deploy iff single-pass, or at least one strict improvement.
    if max_iters == 1 or improved_any:
        if sub_mode == "file":
            if not args.from_file:
                print("  [DeploymentSync] file submission mode requires --from <file>")
                return 1
            return _submit_file(comp, args.from_file, args.message, args.dry_run)
        return _kaggle_push(comp, d, args.dry_run)
    print("  [DeploymentSync] SKIPPED: no improvement over baseline "
          "(submission gate not passed); no push/submit performed.")
    return 0


def _fetch_notebook_stdout(comp: str, d: Path) -> str:
    """Fetch the notebook's stdout after a successful push.

    Resolves the kernel id, polls ``kaggle kernels status <id>`` until COMPLETE
    (bounded loop, 10-second interval, max 60 iterations = 10 min), then
    ``kaggle kernels output <id> -p <tmp>`` and parses the JSONL log's
    ``data`` key, joining lines into the reconstructed stdout.
    """
    # Resolve the full kernel id (user/slug) from metadata if available.
    try:
        meta = kaggle_nb.read_metadata(d)
        kid = meta.get("id", comp)
    except Exception:
        kid = comp
    if "/" not in kid:
        user = kaggle_nb._resolve_kaggle_username()
        if user:
            kid = f"{user}/{kid}"

    print(f"  [fetch] polling kaggle kernels status {kid} ...")

    # Poll status until COMPLETE (or error/timeout).
    max_polls = 60
    for _ in range(max_polls):
        try:
            import subprocess as _subprocess
            cap = _subprocess.run(
                ["kaggle", "kernels", "status", kid],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            combined = (cap.stdout or "") + (cap.stderr or "")
        except Exception:
            time.sleep(10)
            continue
        if "complete" in combined.lower() or "status\" has status \"complete\"" in combined.lower():
            print(f"  [fetch] kernel {kid} status: COMPLETE")
            break
        if "error" in combined.lower() and "complete" not in combined.lower():
            print(f"  [fetch] kernel {kid} has error status:\n{combined}")
            return ""
        print(f"  [fetch] waiting (10s) ...")
        time.sleep(10)
    else:
        print(f"  [fetch] timed out waiting for kernel {kid} to complete")
        return ""

    # Download the output.
    tmp_out = Path(tempfile.mkdtemp(prefix="kgo_"))
    try:
        rc = kaggle_nb._run(
            ["kaggle", "kernels", "output", kid, "-p", str(tmp_out)], dry_run=False,
        )
        if rc != 0:
            print(f"  [fetch] kaggle kernels output failed (rc={rc})")
            return ""

        # Find and parse the JSONL log file.
        stdout_lines = _parse_jsonl_log_files(tmp_out)
        if stdout_lines:
            result = "\n".join(stdout_lines)
            print(f"  [fetch] reconstructed stdout ({len(result)} chars)")
            # Show last few lines for visibility
            for line in result.splitlines()[-5:]:
                print(f"         {line}")
            return result
        else:
            print("  [fetch] no stdout entries found in kernel output log")
            return ""
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_out, ignore_errors=True)
        except Exception:
            pass


def _parse_jsonl_log_files(output_dir: Path) -> list[str]:
    """Parse kernel JSONL log files in *output_dir*, extracting ``data`` entries.

    Each line in ``*.log`` files may be a JSON object with a ``data`` key;
    its value (string or dict) is collected as a line of reconstructed stdout.
    Non-JSON lines are passed through as-is.

    Returns a list of stdout lines (may be empty).
    """
    log_files = sorted(output_dir.glob("*.log")) + sorted(output_dir.glob("**/*.log"))
    stdout_lines: list[str] = []
    for lf in log_files:
        try:
            text = lf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # Not JSONL — could be plain text log
                stdout_lines.append(line)
                continue
            # Extract the "data" stream (stdout).
            data = entry.get("data")
            if isinstance(data, dict):
                # Some kernel logs wrap stdout in data.text or data.output
                text_val = data.get("text") or data.get("output") or ""
                for sub_line in str(text_val).splitlines():
                    stdout_lines.append(sub_line)
            elif isinstance(data, str):
                stdout_lines.append(data)
    return stdout_lines


def cmd_submit_file(args: argparse.Namespace) -> int:
    comp_root(args.comp, args.workspace)
    return _submit_file(args.comp, args.from_file, args.message, args.dry_run)


def cmd_push_notebook(args: argparse.Namespace) -> int:
    d = comp_root(args.comp, args.workspace)
    return _kaggle_push(args.comp, d, args.dry_run)


def cmd_submit(args: argparse.Namespace) -> int:
    d = comp_root(args.comp, args.workspace)
    mode = args.mode
    if mode == "auto":
        mode = "file" if args.from_file else "notebook"
    if mode == "file":
        if not args.from_file:
            print("  [error] --mode file requires --from <file>")
            return 1
        return _submit_file(args.comp, args.from_file, args.message, args.dry_run)
    # notebook
    code_py = d / kaggle_nb.CODE_PY
    if code_py.exists():
        kaggle_nb.set_notebook_code(d, code_py.read_text(encoding="utf-8"))
    return _kaggle_push(args.comp, d, args.dry_run)


def cmd_status(args: argparse.Namespace) -> int:
    kaggle_nb.validate_slug(args.comp)
    return kaggle_nb._run(["kaggle", "competitions", "status", args.comp],
                          dry_run=args.dry_run)


def cmd_leaderboard(args: argparse.Namespace) -> int:
    kaggle_nb.validate_slug(args.comp)
    return kaggle_nb._run(["kaggle", "competitions", "leaderboard", args.comp],
                          dry_run=args.dry_run)


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kaggle_comp.py",
        description="Universal Kaggle competition harness (5-node pipeline, "
                    "ML or GenAI, file or notebook submission). Reuses the "
                    "kaggle-notebook skill for Kaggle connectivity.",
    )
    p.add_argument("--workspace", help=f"workspace root (default: {_default_workspace()})")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_dry_run(sp):
        sp.add_argument("--dry-run", action="store_true",
                        help="print the kaggle command without executing it")

    sp = sub.add_parser("setup", help="check kaggle, nbformat, credentials, templates")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("list", help="list competitions")
    sp.add_argument("pattern", nargs="?", default="")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("files", help="list competition files")
    sp.add_argument("comp")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_files)

    sp = sub.add_parser("init", help="scaffold a new competition workspace + private notebook")
    sp.add_argument("comp")
    sp.add_argument("--title")
    sp.add_argument("--gpu", action="store_true")
    sp.add_argument("--internet", action="store_true")
    sp.add_argument("--mode", choices=["ml", "genai"], default="ml")
    sp.add_argument("--submission", choices=["auto", "file", "notebook"], default="auto")
    sp.add_argument("--max-iters", type=int, default=None)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("data", help="download competition data")
    sp.add_argument("comp")
    sp.add_argument("--to")
    sp.add_argument("--file")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_data)

    sp = sub.add_parser("detect", help="detect submission mode + ai_mode, write to state")
    sp.add_argument("comp")
    sp.add_argument("--mode", choices=["ml", "genai"], default=None)
    sp.add_argument("--from", dest="from_file", default=None,
                    help="if supplied, sets submission mode to 'file'")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_detect)

    sp = sub.add_parser("render", help="render a node template to python")
    sp.add_argument("comp")
    sp.add_argument("node", choices=NODES)
    sp.add_argument("--mode", choices=["ml", "genai"], default="ml")
    sp.add_argument("--iteration", type=int, default=1)
    sp.add_argument("--to", help="write rendered code to this path (default: stdout)")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("state", help="show / update the competition state")
    sp.add_argument("comp")
    sp.add_argument("--show", action="store_true", help="print the state as JSON")
    sp.add_argument("--update-metric", default=None, help="NAME=VAL to apply through the gate")
    sp.set_defaults(func=cmd_state)

    sp = sub.add_parser("run", help="drive the 5-node pipeline (+ optional optimization loop)")
    sp.add_argument("comp")
    sp.add_argument("--mode", choices=["ml", "genai"], default="ml")
    sp.add_argument("--submission", choices=["auto", "file", "notebook"], default="auto")
    sp.add_argument("--from", dest="from_file", default=None,
                    help="submission file (required for --submission file)")
    sp.add_argument("--max-iters", type=int, default=None,
                    help="1 = single-pass deploy (default); >1 = optimization loop")
    sp.add_argument("--plateau-patience", type=int, default=None,
                    help="consecutive non-improving iters before stop (default 2)")
    sp.add_argument("--simulate", choices=["improve", "constant", "degrade"],
                    default="improve", help="dry-run only: fake metric trajectory")
    sp.add_argument("-m", "--message", default="submission")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("submit-file", help="kaggle competitions submit -f <file> -m <msg> <comp>")
    sp.add_argument("comp")
    sp.add_argument("--from", dest="from_file", required=True)
    sp.add_argument("-m", "--message", required=True)
    add_dry_run(sp)
    sp.set_defaults(func=cmd_submit_file)

    sp = sub.add_parser("push-notebook", help="inject code.py and push the private notebook")
    sp.add_argument("comp")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_push_notebook)

    sp = sub.add_parser("submit", help="universal submit (auto routes file vs notebook)")
    sp.add_argument("comp")
    sp.add_argument("--mode", choices=["auto", "file", "notebook"], default="auto")
    sp.add_argument("--from", dest="from_file", default=None)
    sp.add_argument("-m", "--message", default="submission")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_submit)

    sp = sub.add_parser("status", help="kaggle competitions status <comp>")
    sp.add_argument("comp")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("leaderboard", help="kaggle competitions leaderboard <comp>")
    sp.add_argument("comp")
    add_dry_run(sp)
    sp.set_defaults(func=cmd_leaderboard)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - top-level CLI guard
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())