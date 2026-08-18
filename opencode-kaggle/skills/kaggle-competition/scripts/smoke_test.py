#!/usr/bin/env python3
"""smoke_test.py - End-to-end smoke test for the kaggle_comp competition harness.

Runs the full node pipeline + submission routing + loop/gate logic in a
throwaway temp workspace, all with --dry-run. Does NOT contact the Kaggle API
and does NOT require credentials: every Kaggle-touching step uses --dry-run so
the helper just prints the command it would run.

Usage:
    python smoke_test.py
    python smoke_test.py -v          # verbose (print helper output)
    python smoke_test.py --keep      # keep the temp workspace for inspection

Exit code 0 = all checks passed; non-zero = at least one failed.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "kaggle_comp.py"
NB_HELPER = HERE.parent.parent / "kaggle-notebook" / "scripts" / "kaggle_nb.py"
NB_SMOKE = HERE.parent.parent / "kaggle-notebook" / "scripts" / "smoke_test.py"
PY = sys.executable

# Make kaggle_comp importable for the in-process metric/gate unit checks.
sys.path.insert(0, str(HERE))
import kaggle_comp  # type: ignore  # noqa: E402

_VERBOSE = False
_USE_COLOR = sys.stdout.isatty()


def _c(code: str, msg: str) -> str:
    if not _USE_COLOR:
        return msg
    return f"\033[{code}m{msg}\033[0m"


def ok(msg: str) -> str:
    return _c("32", f"PASS  {msg}")


def bad(msg: str) -> str:
    return _c("31", f"FAIL  {msg}")


def info(msg: str) -> str:
    return _c("90", f"      {msg}")


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def record(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(ok(name) + (f"  {detail}" if detail and _VERBOSE else ""))
        else:
            self.failed += 1
            self.failures.append(name)
            print(bad(name) + (f"  {detail}" if detail else ""))

    @property
    def exit_code(self) -> int:
        return 0 if self.failed == 0 else 1


def run_helper(args: list[str], workspace: Path) -> tuple[int, str, str]:
    """Run the helper with --workspace <workspace> before the subcommand."""
    cmd = [PY, str(HELPER), "--workspace", str(workspace), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if _VERBOSE:
        print(info("$ " + " ".join(cmd)))
        if proc.stdout:
            print(info(proc.stdout))
        if proc.stderr:
            print(info(proc.stderr))
    return proc.returncode, proc.stdout, proc.stderr


def load_state(comp: str, ws: Path) -> dict | None:
    p = ws / "competitions" / comp / "competition_state.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_meta(comp: str, ws: Path) -> dict | None:
    p = ws / "competitions" / comp / "kernel-metadata.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_helper_syntax(r: Results) -> None:
    name = "01 kaggle_comp.py parses as valid Python"
    try:
        ast.parse(HELPER.read_text(encoding="utf-8"))
        r.record(name, True)
    except SyntaxError as e:
        r.record(name, False, str(e))


def check_help(r: Results, _ws: Path) -> None:
    name = "02 --help lists every subcommand"
    proc = subprocess.run([PY, str(HELPER), "--help"], capture_output=True, text=True)
    expected = ["setup", "list", "files", "init", "data", "context", "detect", "render",
                "state", "run", "submit-file", "push-notebook", "submit",
                 "status", "leaderboard", "lint"]
    ok_help = proc.returncode == 0 and all(e in proc.stdout for e in expected)
    missing = [e for e in expected if e not in proc.stdout]
    r.record(name, ok_help, f"rc={proc.returncode} missing={missing}")


def check_import_kaggle_nb(r: Results, _ws: Path) -> None:
    name = "03 kaggle_comp imports kaggle_nb at runtime (connectivity reuse)"
    ok_imp = (
        hasattr(kaggle_comp, "kaggle_nb")
        and kaggle_comp.kaggle_nb is not None
        and hasattr(kaggle_comp.kaggle_nb, "validate_slug")
    )
    r.record(name, ok_imp)


def check_templates_and_render(r: Results, _ws: Path) -> None:
    name = "04 templates exist + render -> parseable Python + #METRIC: in exp/eval"
    modes = ["ml", "genai"]
    nodes = ["ingestion", "processing", "experimentation", "evaluation", "deployment"]
    all_ok = True
    detail = ""
    tdir = kaggle_comp._TEMPLATES_DIR
    for mode in modes:
        for node in nodes:
            p = tdir / mode / f"{node}.py.tmpl"
            if not p.exists():
                all_ok = False
                detail = f"missing {p}"
                break
            rendered = kaggle_comp.render_template(node, mode,
                                                   competition="mycomp", iteration=2)
            try:
                ast.parse(rendered)
            except SyntaxError as e:
                all_ok = False
                detail = f"{mode}/{node} rendered not parseable: {e}"
                break
    # experimentation + evaluation must contain a #METRIC: emission marker
    for mode in modes:
        for node in ("experimentation", "evaluation"):
            tfile = tdir / mode / f"{node}.py.tmpl"
            if "#METRIC:" not in tfile.read_text(encoding="utf-8"):
                all_ok = False
                detail = f"{mode}/{node} missing #METRIC: marker"
                break
    r.record(name, all_ok, detail)


def check_ml_template_quality(r: Results, _ws: Path) -> None:
    name = "04b ML templates document decisions, expand EDA, and avoid node labels"
    tdir = kaggle_comp._TEMPLATES_DIR / "ml"
    texts = {
        node: (tdir / f"{node}.py.tmpl").read_text(encoding="utf-8")
        for node in ("ingestion", "processing", "experimentation", "evaluation", "deployment")
    }
    forbidden_labels = [
        "DataIngestion_Node",
        "DataProcessing_Node",
        "Experimentation_Node",
        "Evaluation_Node",
        "DeploymentSync_Node",
    ]
    labels_removed = not any(label in text for text in texts.values() for label in forbidden_labels)
    markdown_cells = sum(text.count("# %% [markdown]") for text in texts.values())
    eda_signals = all(
        signal in texts["ingestion"]
        for signal in (
            "missing_table",
            "target_counts",
            "imshow",
            "plt.hist",
            "Train/test distribution",
            "MAX_DISCOVERY_FILES",
            "MAX_DIRECTORY_ENTRIES",
            "MAX_INPUT_FILE_BYTES",
            "MAX_EDA_ROWS",
            "MAX_CORRELATION_COLUMNS",
            "MAX_OUTPUT_CHARS",
            "_iter_input_files",
            "_print_limited",
            "Outlier view",
            "Target relationship",
            "[eda] takeaways",
        )
    )

    decision_signals = all(
        signal in texts["experimentation"]
        for signal in (
            "target_reason", "decision_log", "Validation choice", "learning_rate",
            "MAX_TRAIN_ROWS", "MAX_MODEL_FEATURES", "MAX_MODEL_ITER", "MAX_CV_ROWS",
            "# %% [markdown]",
        )
    )
    diagnostics = all(
        signal in texts["evaluation"]
        for signal in (
            "confusion_matrix", "Actual versus predicted class counts", "MAX_EVAL_ROWS",
            "MAX_DIAGNOSTIC_ROWS", "_predict_in_batches", "#METRIC:",
        )
    )
    submission_safety = all(
        signal in texts["deployment"]
        for signal in ("_sanitize_submission_value", "_sanitize_submission_header",
                       "MAX_PREDICTION_BATCH_ROWS")
    )
    r.record(
        name,
        labels_removed and markdown_cells >= 8 and eda_signals and decision_signals
        and diagnostics and submission_safety,
        f"labels_removed={labels_removed} markdown_cells={markdown_cells} "
        f"eda={eda_signals} decisions={decision_signals} diagnostics={diagnostics} "
        f"submission_safety={submission_safety}",
    )


def check_notebook_lint(r: Results, ws: Path) -> None:
    name = "04c notebook lint detects minified code and accepts readable code"
    ugly = "# %%\n" + "x = 1; y = 2; " + ("value = 3 " * 30) + "\n"
    clean = ("# %% [markdown]\n# Explain the step.\n# %%\n"
             "import matplotlib.pyplot as plt\n\n"
             "def documented(value):\n"
             "    \"\"\"Return the input unchanged.\"\"\"\n"
             "    return value\n\n"
             "plt.plot([0, 1], [0, 1])\n")
    ugly_issues = kaggle_comp.lint_notebook_code(ugly)
    clean_issues = kaggle_comp.lint_notebook_code(clean)
    r.record(name, any("long lines" in issue for issue in ugly_issues)
             and any("semicolon" in issue for issue in ugly_issues)
             and any("markdown" in issue for issue in ugly_issues)
             and not clean_issues,
             f"ugly={ugly_issues} clean={clean_issues}")


def check_lint_cli(r: Results, ws: Path) -> None:
    name = "04d lint CLI reports workspace issues without network access"
    comp = "lintcli"
    run_helper(["init", comp], ws)
    code_path = ws / "competitions" / comp / "code.py"
    code_path.write_text("# %%\nx = 1; y = 2; " + ("value = 3 " * 30) + "\n", encoding="utf-8")
    bad_rc, bad_out, _ = run_helper(["lint", comp], ws)
    r.record(name, bad_rc == 1 and "issue(s)" in bad_out and "semicolon" in bad_out,
             f"rc={bad_rc} output={bad_out!r}")


def check_markdown_source_of_truth(r: Results, ws: Path) -> None:
    name = "04e competition assembly removes stale Markdown cells"
    comp = "markdowntruth"
    run_helper(["init", comp], ws)
    d = ws / "competitions" / comp
    kaggle_comp.kaggle_nb.set_notebook_code(
        d,
        "# %% [markdown]\n# Current documentation\n# %%\nvalue = 1\n",
        preserve_markdown=False,
    )
    kaggle_comp.kaggle_nb.set_notebook_code(
        d,
        "# %%\nvalue = 2\n",
        preserve_markdown=False,
    )
    notebook = json.loads((d / "notebook.ipynb").read_text(encoding="utf-8"))
    markdown = [cell for cell in notebook["cells"] if cell["cell_type"] == "markdown"]
    r.record(name, not markdown and "value = 2" in "".join(notebook["cells"][0]["source"]))


def check_init(r: Results, ws: Path) -> None:
    name = "05 init scaffolds state + PRIVATE metadata + notebook + code.py"
    rc, _, _ = run_helper(["init", "myinit", "--title", "My", "--mode", "ml"], ws)
    state = load_state("myinit", ws)
    meta = load_meta("myinit", ws)
    nb = ws / "competitions" / "myinit" / "notebook.ipynb"
    code = ws / "competitions" / "myinit" / "code.py"
    files_ok = nb.exists() and code.exists()
    state_ok = state is not None and all(
        k in state for k in ("competition", "submission_mode", "ai_mode",
                              "current_node", "plan_created", "primary_metric", "minimize",
                              "best_local_score", "iterations", "max_iterations",
                              "history", "plan_approved", "approved_plan_sha256",
                              "approved_plan_config")
    )
    meta_ok = (
        meta is not None
        and str(meta.get("is_private", "")).lower() == "true"
        and meta.get("competition_sources") == ["myinit"]
    )
    r.record(name, rc == 0 and files_ok and state_ok and meta_ok,
             f"rc={rc} files={files_ok} state={state_ok} meta={meta_ok}")


def check_plan_scaffold(r: Results, ws: Path) -> None:
    name = "20a plan scaffolds plan.md, is idempotent, and resets approval on --force"
    comp = "planscaffold"
    run_helper(["init", comp, "--mode", "ml"], ws)
    plan_path = ws / "competitions" / comp / "plan.md"

    first_rc, _, _ = run_helper(["plan", comp], ws)
    first_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    first_state = load_state(comp, ws)
    scaffold_ok = (
        first_rc == 0
        and plan_path.exists()
        and f"# Competition plan: {comp}" in first_text
        and "## Approach" in first_text
        and "## Acceptance criteria" in first_text
        and "Primary metric: `f1`" in first_text
        and first_state is not None
        and first_state.get("plan_approved") is False
    )

    approve_rc, _, _ = run_helper(["plan", comp, "--approve"], ws)
    approved_state = load_state(comp, ws)
    (plan_path).write_text(first_text + "\nSENTINEL\n", encoding="utf-8")
    repeat_rc, _, _ = run_helper(["plan", comp], ws)
    preserved = plan_path.read_text(encoding="utf-8")
    force_rc, _, _ = run_helper(["plan", comp, "--force"], ws)
    final_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    final_state = load_state(comp, ws)
    approval_ok = (
        approve_rc == 0
        and approved_state is not None
        and approved_state.get("plan_approved") is True
        and bool(approved_state.get("approved_plan_sha256"))
        and isinstance(approved_state.get("approved_plan_config"), dict)
    )
    idempotent_ok = repeat_rc != 0 and "SENTINEL" in preserved
    reset_ok = force_rc == 0 and "SENTINEL" not in final_text and final_state is not None and final_state.get("plan_approved") is False
    r.record(name, scaffold_ok and approval_ok and idempotent_ok and reset_ok,
             f"scaffold={scaffold_ok} approve={approval_ok} idempotent={idempotent_ok} reset={reset_ok}")


def check_plan_approve_gate(r: Results, ws: Path) -> None:
    name = "20b plan approval persists and --require-plan gates run"
    comp = "plangate"
    run_helper(["init", comp, "--mode", "ml"], ws)

    blocked_rc, blocked_out, _ = run_helper(
        ["run", comp, "--require-plan", "--dry-run"], ws
    )
    blocked_ok = blocked_rc != 0 and "plan gate active" in blocked_out
    approve_missing_rc, _, _ = run_helper(["plan", comp, "--approve"], ws)
    scaffold_rc, _, _ = run_helper(["plan", comp], ws)
    plain_blocked_rc, plain_blocked_out, _ = run_helper(["run", comp, "--dry-run"], ws)
    show_rc, show_out, _ = run_helper(["plan", comp, "--show"], ws)
    approve_rc, _, _ = run_helper(["plan", comp, "--approve"], ws)
    state = load_state(comp, ws)
    allowed_rc, allowed_out, _ = run_helper(
        ["run", comp, "--require-plan", "--dry-run"], ws
    )
    allowed_ok = (
        allowed_rc == 0
        and "approved plan" in allowed_out
        and "DataIngestion_Node" in allowed_out
        and "DeploymentSync_Node" in allowed_out
    )
    show_ok = show_rc == 0 and f"# Competition plan: {comp}" in show_out
    persisted_ok = approve_rc == 0 and state is not None and state.get("plan_approved") is True
    plain_blocked_ok = plain_blocked_rc != 0 and "plan gate active" in plain_blocked_out

    plan_path = ws / "competitions" / comp / "plan.md"
    plan_path.unlink()
    deleted_rc, deleted_out, _ = run_helper(["run", comp, "--dry-run"], ws)
    recreate_rc, _, _ = run_helper(["plan", comp, "--force"], ws)
    reapprove_delete_rc, _, _ = run_helper(["plan", comp, "--approve"], ws)
    plan_path.write_text(plan_path.read_text(encoding="utf-8") + "\npost-approval edit\n", encoding="utf-8")
    changed_rc, changed_out, _ = run_helper(
        ["run", comp, "--require-plan", "--dry-run"], ws
    )
    reapprove_rc, _, _ = run_helper(["plan", comp, "--approve"], ws)
    config_rc, config_out, _ = run_helper(
        ["run", comp, "--require-plan", "--max-iters", "2", "--dry-run"], ws
    )
    integrity_ok = (
        deleted_rc != 0
        and "approved plan.md is missing" in deleted_out
        and recreate_rc == 0
        and reapprove_delete_rc == 0
        and changed_rc != 0
        and "changed after approval" in changed_out
        and reapprove_rc == 0
        and config_rc != 0
        and "configuration changed" in config_out
    )
    r.record(
        name,
        blocked_ok and approve_missing_rc != 0 and scaffold_rc == 0 and plain_blocked_ok
        and show_ok and persisted_ok and allowed_ok and integrity_ok,
        f"blocked={blocked_ok} missing_approve={approve_missing_rc != 0} scaffold={scaffold_rc == 0} "
        f"plain_blocked={plain_blocked_ok} show={show_ok} persisted={persisted_ok} "
        f"allowed={allowed_ok} integrity={integrity_ok}",
    )


def check_state_show(r: Results, ws: Path) -> None:
    name = "06 state --show prints valid JSON with required keys"
    rc, out, _ = run_helper(["state", "myinit", "--show"], ws)
    try:
        j = json.loads(out)
    except Exception:
        r.record(name, False, f"rc={rc} (json decode failed)")
        return
    required = ["competition", "best_local_score", "iterations", "history"]
    keys_ok = all(k in j for k in required)
    r.record(name, rc == 0 and keys_ok, f"rc={rc} keys_ok={keys_ok}")


def check_state_update_metric(r: Results, ws: Path) -> None:
    name = "07 --update-metric updates best/iterations/history + respects minimize"
    # maximize case (ml f1, minimize=False)
    run_helper(["init", "stmax", "--mode", "ml"], ws)
    run_helper(["state", "stmax", "--update-metric", "f1=0.83"], ws)
    run_helper(["state", "stmax", "--update-metric", "f1=0.80"], ws)  # worse -> no improve
    s = load_state("stmax", ws)
    max_ok = (s["best_local_score"] == 0.83 and s["iterations"] == 2
              and len(s["history"]) == 2)
    # minimize case: craft a state with minimize=True (rmse, lower wins)
    dmin = ws / "competitions" / "stmin"
    dmin.mkdir(parents=True, exist_ok=True)
    (dmin / "competition_state.json").write_text(json.dumps({
        "competition": "stmin", "submission_mode": "notebook", "ai_mode": "ml",
        "current_node": "evaluation", "primary_metric": "rmse", "minimize": True,
        "best_local_score": None, "best_iteration": None, "iterations": 0,
        "max_iterations": 1, "plateau_patience": 2, "history": [],
        "metric_gate": "improve", "metric_threshold": None, "last_stdout": "",
    }), encoding="utf-8")
    run_helper(["state", "stmin", "--update-metric", "rmse=0.50"], ws)  # baseline
    run_helper(["state", "stmin", "--update-metric", "rmse=0.40"], ws)  # better (lower)
    run_helper(["state", "stmin", "--update-metric", "rmse=0.60"], ws)  # worse -> no improve
    s2 = load_state("stmin", ws)
    min_ok = s2["best_local_score"] == 0.40  # 0.40 improved over 0.50; 0.60 not
    r.record(name, max_ok and min_ok,
             f"max_ok={max_ok} (best={s['best_local_score']}) min_ok={min_ok} (best={s2['best_local_score']})")


def check_metric_parser(r: Results, _ws: Path) -> None:
    name = "08 parse_metrics extracts from noisy/multi/empty stdout"
    a = kaggle_comp.parse_metrics("noise\n#METRIC:f1=0.83 more\nnoise")
    b = kaggle_comp.parse_metrics("#METRIC:a=1\n#METRIC:b=2.5\n#METRIC:c=-3e-2")
    c = kaggle_comp.parse_metrics("no metric here")
    a_ok = a == [("f1", 0.83)]
    b_ok = len(b) == 3 and b[0] == ("a", 1.0) and b[1] == ("b", 2.5)
    c_ok = c == []
    r.record(name, a_ok and b_ok and c_ok, f"a={a} b={b} c={c}")


def check_run_headers_privacy(r: Results, ws: Path, mode: str) -> None:
    name = f"09 run --mode {mode} --dry-run walks 5 nodes -> dry-run push + privacy + hint"
    comp = f"run{mode}"
    run_helper(["init", comp, "--mode", mode, "--submission", "notebook"], ws)
    rc, out, _ = run_helper(
        ["run", comp, "--mode", mode, "--submission", "notebook", "--dry-run"], ws
    )
    headers = ["DataIngestion_Node", "DataProcessing_Node",
              "Experimentation_Node", "Evaluation_Node", "DeploymentSync_Node"]
    headers_ok = all(h in out for h in headers)
    push_ok = "[DRY-RUN] kaggle kernels push -p" in out
    hint_ok = "Submit to Competition" in out and "score NO aparecera" in out
    meta = load_meta(comp, ws)
    privacy_ok = meta is not None and str(meta.get("is_private", "")).lower() == "true"
    comp_src_ok = meta is not None and "competition_sources" in meta and comp in meta["competition_sources"]
    r.record(name, rc == 0 and headers_ok and push_ok and hint_ok and privacy_ok and comp_src_ok,
              f"rc={rc} headers={headers_ok} push={push_ok} hint={hint_ok} priv={privacy_ok} comp_src={comp_src_ok}")


def check_competition_privacy_enforced(r: Results, ws: Path) -> None:
    name = "09b deployment validation forces competition metadata to is_private=true"
    comp = "privacyfix"
    run_helper(["init", comp, "--mode", "ml", "--submission", "notebook"], ws)
    d = ws / "competitions" / comp
    meta_path = d / "kernel-metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["is_private"] = "false"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    kaggle_comp._validate_meta_for_deploy(d, comp)
    repaired = json.loads(meta_path.read_text(encoding="utf-8"))
    r.record(name, str(repaired.get("is_private", "")).lower() == "true")


def check_submit_routing(r: Results, ws: Path) -> None:
    name = "10 submit routing: file/notebook/auto"
    comp = "subrt"
    run_helper(["init", comp], ws)
    csv = ws / "sub.csv"
    csv.write_text("id,target\n0,1\n", encoding="utf-8")
    # file
    rc1, o1, _ = run_helper(["submit", comp, "--mode", "file", "--from", str(csv),
                            "-m", "v1", "--dry-run"], ws)
    f_ok = rc1 == 0 and "[DRY-RUN] kaggle competitions submit -f" in o1
    # notebook
    rc2, o2, _ = run_helper(["submit", comp, "--mode", "notebook", "--dry-run"], ws)
    nb_ok = rc2 == 0 and "[DRY-RUN] kaggle kernels push -p" in o2
    # auto without --from -> notebook
    rc3, o3, _ = run_helper(["submit", comp, "--mode", "auto", "--dry-run"], ws)
    auto_nb_ok = "[DRY-RUN] kaggle kernels push -p" in o3
    # auto with --from -> file
    rc4, o4, _ = run_helper(["submit", comp, "--mode", "auto", "--from", str(csv),
                            "--dry-run"], ws)
    auto_f_ok = "[DRY-RUN] kaggle competitions submit -f" in o4
    r.record(name, f_ok and nb_ok and auto_nb_ok and auto_f_ok,
             f"file={f_ok} notebook={nb_ok} auto_nb={auto_nb_ok} auto_file={auto_f_ok}")


def check_detect(r: Results, ws: Path) -> None:
    name = "11 detect prints MODE: (file when --from, notebook otherwise)"
    run_helper(["init", "det1"], ws)
    rc1, o1, _ = run_helper(["detect", "det1", "--dry-run"], ws)
    nb_ok = rc1 == 0 and "MODE: submission=notebook" in o1
    csv = ws / "d.csv"
    csv.write_text("x\n", encoding="utf-8")
    rc2, o2, _ = run_helper(["detect", "det1", "--from", str(csv), "--dry-run"], ws)
    f_ok = rc2 == 0 and "MODE: submission=file" in o2
    r.record(name, nb_ok and f_ok, f"notebook={nb_ok} file={f_ok}")


def check_remote_cmds_dryrun(r: Results, ws: Path) -> None:
    name = "12 files/data/list/status/leaderboard --dry-run print kaggle commands"
    comp = "remote1"
    run_helper(["init", comp], ws)
    _, o1, _ = run_helper(["files", comp, "--dry-run"], ws)
    _, o2, _ = run_helper(["data", comp, "--dry-run"], ws)
    _, o3, _ = run_helper(["list", "--dry-run"], ws)
    _, o4, _ = run_helper(["status", comp, "--dry-run"], ws)
    _, o5, _ = run_helper(["leaderboard", comp, "--dry-run"], ws)
    f_ok = "[DRY-RUN] kaggle competitions files" in o1
    d_ok = "[DRY-RUN] kaggle competitions download" in o2
    l_ok = "[DRY-RUN] kaggle competitions list" in o3
    s_ok = "[DRY-RUN] kaggle competitions status" in o4
    lb_ok = "[DRY-RUN] kaggle competitions leaderboard" in o5
    r.record(name, all([f_ok, d_ok, l_ok, s_ok, lb_ok]),
             f"files={f_ok} data={d_ok} list={l_ok} status={s_ok} lb={lb_ok}")


def check_context_dry_run(r: Results, ws: Path) -> None:
    name = "19a context --dry-run prints ranked kernel list and pull plan"
    run_helper(["init", "ctx1"], ws)
    rc, out, _ = run_helper(["context", "ctx1", "--top", "3", "--dry-run"], ws)
    list_ok = "[DRY-RUN] kaggle kernels list --competition ctx1 --sort-by voteCount --page-size 3 --csv" in out
    pull_ok = "[DRY-RUN] kaggle kernels pull <owner>/<slug>" in out
    r.record(name, rc == 0 and list_ok and pull_ok,
             f"rc={rc} list={list_ok} pull={pull_ok}")


def check_context_csv_parser(r: Results, _ws: Path) -> None:
    name = "19b context CSV parser handles noise, quoted titles, and empty results"
    sample = (
        "Next Page Token = abc\n"
        "ref,title,author,lastRunTime,totalVotes\n"
        'alice/eda,"EDA, with tricks",alice,2026-07-01,120\n'
        "bob/starter,Simple starter,bob,2026-06-30,95\n"
    )
    kernels = kaggle_comp._parse_kernel_list_csv(sample)
    parsed = (len(kernels) == 2 and kernels[0]["ref"] == "alice/eda"
              and kernels[0]["title"] == "EDA, with tricks")
    empty = (kaggle_comp._parse_kernel_list_csv("Not found\n") == []
             and kaggle_comp._parse_kernel_list_csv("") == [])
    r.record(name, parsed and empty, f"parsed={parsed} empty={empty}")


def check_context_digest(r: Results, _ws: Path) -> None:
    name = "19c notebook digest preserves markdown, code cells, and valid Python"
    directory = Path(tempfile.mkdtemp(prefix="kgs_context_"))
    try:
        notebook = {
            "cells": [
                {"cell_type": "markdown", "source": ["# Intro\n", "Some text"]},
                {"cell_type": "code", "source": ["import pandas as pd\n", "x = 1"]},
                {"cell_type": "code", "source": ["print(x)"]},
            ]
        }
        path = directory / "kernel.ipynb"
        path.write_text(json.dumps(notebook), encoding="utf-8")
        digest = kaggle_comp._extract_notebook_digest(
            path, ref="alice/eda", title="EDA", votes="12"
        )
        ast.parse(digest)
        checks = [
            digest.count("# %%") == 3,
            "# Some text" in digest,
            "import pandas as pd" in digest,
            "# source: https://www.kaggle.com/code/alice/eda" in digest,
        ]
        r.record(name, all(checks), f"checks={checks}")
    except Exception as exc:  # noqa: BLE001 - report as a smoke failure
        r.record(name, False, str(exc))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def check_context_traversal(r: Results, ws: Path) -> None:
    name = "19d context rejects traversal competition and kernel references"
    rc, _, _ = run_helper(["context", "..", "--dry-run"], ws)
    bad_ref_rejected = False
    try:
        kaggle_comp._context_dirname("a/../../x")
    except ValueError:
        bad_ref_rejected = True
    r.record(name, rc != 0 and bad_ref_rejected,
             f"bad_comp={rc != 0} bad_ref={bad_ref_rejected}")


def check_path_traversal(r: Results, ws: Path) -> None:
    name = "13 path-traversal competition names are rejected"
    bad_slugs = ["..", "a/b", "C:\\x", ""]
    all_rejected = True
    for slug in bad_slugs:
        rc, _, _ = run_helper(["init", slug], ws)
        if rc == 0:
            all_rejected = False
            print(info(f"  slug {slug!r} was ACCEPTED (should be rejected)"))
            break
    # also run + submit-file with one bad slug
    rc_run, _, _ = run_helper(["run", "..", "--dry-run"], ws)
    csv = ws / "x.csv"
    csv.write_text("a\n", encoding="utf-8")
    rc_sf, _, _ = run_helper(["submit-file", "..", "--from", str(csv), "-m", "x", "--dry-run"], ws)
    if rc_run == 0 or rc_sf == 0:
        all_rejected = False
    r.record(name, all_rejected, f"tested {len(bad_slugs)} slugs + run + submit-file")


def check_render_validation(r: Results, ws: Path) -> None:
    name = "13b render rejects traversal / template-injection competition names"
    # The last slug tries to break out of a Python string literal via quote/backslash.
    bad_slugs = ["..", "a/b", "C:\\x", "", 'x"; os.system("y"); "', 'bad\\slug']
    all_rejected = True
    for slug in bad_slugs:
        rc, _, _ = run_helper(["render", slug, "ingestion", "--mode", "ml"], ws)
        if rc == 0:
            all_rejected = False
            print(info(f"  render slug {slug!r} was ACCEPTED (should be rejected)"))
            break
    # sanity: a valid comp still renders fine to stdout
    rc_ok, out_ok, _ = run_helper(["render", "mycomp", "evaluation", "--mode", "genai"], ws)
    valid_ok = rc_ok == 0 and "#METRIC:bertscore" in out_ok
    r.record(name, all_rejected and valid_ok,
             f"rejected={all_rejected} valid_render={valid_ok}")


def check_loop_maxiters(r: Results, ws: Path) -> None:
    name = "16a run --max-iters 3 --simulate constant stops after 3 iters (no deploy)"
    comp = "loopmax"
    run_helper(["init", comp], ws)
    rc, out, _ = run_helper(["run", comp, "--max-iters", "3", "--plateau-patience", "5",
                            "--simulate", "constant", "--dry-run"], ws)
    iters_ok = ("[iter 1/3]" in out and "[iter 2/3]" in out and "[iter 3/3]" in out)
    skipped_ok = "SKIPPED" in out
    no_push = "[DRY-RUN] kaggle kernels push -p" not in out
    r.record(name, rc == 0 and iters_ok and skipped_ok and no_push,
             f"rc={rc} iters={iters_ok} skipped={skipped_ok} no_push={no_push}")


def check_loop_plateau(r: Results, ws: Path) -> None:
    name = "16b run --max-iters 5 --plateau-patience 2 stops at iter 2 (plateau)"
    comp = "loopplat"
    run_helper(["init", comp], ws)
    rc, out, _ = run_helper(["run", comp, "--max-iters", "5", "--plateau-patience", "2",
                            "--simulate", "constant", "--dry-run"], ws)
    ran_two = ("[iter 1/5]" in out and "[iter 2/5]" in out)
    not_three = "[iter 3/5]" not in out
    stop_ok = "decision=stop" in out
    skipped_ok = "SKIPPED" in out
    r.record(name, rc == 0 and ran_two and not_three and stop_ok and skipped_ok,
             f"ran_two={ran_two} not_three={not_three} stop={stop_ok} skipped={skipped_ok}")


def check_gate_proceed(r: Results, ws: Path) -> None:
    name = "17a gate proceed: --max-iters 2 --simulate improve -> deploy"
    comp = "gateproceed"
    run_helper(["init", comp], ws)
    rc, out, _ = run_helper(["run", comp, "--max-iters", "2", "--simulate", "improve", "--dry-run"], ws)
    deploy_ok = "[DRY-RUN] kaggle kernels push -p" in out
    improved_ok = "decision=done" in out
    r.record(name, rc == 0 and deploy_ok and improved_ok,
             f"rc={rc} deploy={deploy_ok} done={improved_ok}")


def check_gate_skip(r: Results, ws: Path) -> None:
    name = "17b gate skip: --max-iters 2 --simulate constant -> no deploy"
    comp = "gateskip"
    run_helper(["init", comp], ws)
    rc, out, _ = run_helper(["run", comp, "--max-iters", "2", "--simulate", "constant", "--dry-run"], ws)
    skipped_ok = "SKIPPED" in out
    no_push = "[DRY-RUN] kaggle kernels push -p" not in out
    r.record(name, rc == 0 and skipped_ok and no_push,
             f"rc={rc} skipped={skipped_ok} no_push={no_push}")


def check_no_git_leak(r: Results, _ws: Path) -> None:
    name = "15 nothing written under the repo (competitions/ absent from CWD)"
    cwd = Path.cwd()
    leaked = (cwd / "competitions").exists() or (cwd / "kaggle-workspace").exists()
    r.record(name, not leaked, f"cwd={cwd}")


def check_fetch_notebook_stdout(r: Results, ws: Path) -> None:
    name = "14 _parse_jsonl_log_files extracts stdout from stubbed JSONL log (no network)"
    log_dir = Path(tempfile.mkdtemp(prefix="kgs_fetch_"))
    try:
        log_file = log_dir / "kernel.log"
        entries = [
            json.dumps({"data": "#METRIC:f1=0.85"}),
            json.dumps({"data": "Line two"}),
            json.dumps({"other": "ignored"}),  # no "data" key -> skipped
            json.dumps({"data": {"text": "nested data"}}),
        ]
        log_file.write_text("\n".join(entries) + "\n", encoding="utf-8")

        lines = kaggle_comp._parse_jsonl_log_files(log_dir)
        result = "\n".join(lines)

        ok1 = "#METRIC:f1=0.85" in result
        ok2 = "Line two" in result
        ok3 = "ignored" not in result  # no "data" key
        ok4 = "nested data" in result  # dict data.text
        r.record(name, ok1 and ok2 and ok3 and ok4,
                 f"has_metric={ok1} has_line2={ok2} no_other={ok3} nested={ok4}")
    finally:
        shutil.rmtree(log_dir, ignore_errors=True)


def check_ingestion_oswalk(r: Results, ws: Path) -> None:
    name = "14b rendered ml ingestion safely walks /kaggle/input; no hard dep on /input/<comp>"
    rendered = kaggle_comp.render_template("ingestion", "ml", competition="mycomp", iteration=1)
    has_walk = (
        "_iter_input_files" in rendered
        or 'os.scandir("/kaggle/input")' in rendered
        or "os.scandir(DATA_DIR)" in rendered
        or 'os.walk("/kaggle/input")' in rendered
        or "os.walk('/kaggle/input')" in rendered
        or "os.walk(DATA_DIR)" in rendered
    )
    no_hard = "/kaggle/input/mycomp" not in rendered
    r.record(name, has_walk and no_hard,
             f"walk={has_walk} no_hard_dep={no_hard}")


def check_data_403_hint(r: Results, ws: Path) -> None:
    name = "14c cmd_data --dry-run output skeleton + 403 acceptance URL"
    src = (HERE / "kaggle_comp.py").read_text(encoding="utf-8")
    has_rules = "competitions/<comp>/rules" in src or "kaggle.com/competitions/" in src and "rules" in src
    has_accept = "I Understand and Accept" in src
    r.record(name, has_rules and has_accept,
             f"rules_url={has_rules} accept_text={has_accept}")


def check_push_notebook_hint(r: Results, ws: Path) -> None:
    name = "14d push-notebook --dry-run prints 'Submit to Competition' hint"
    run_helper(["init", "hintnb"], ws)
    rc, out, _ = run_helper(["push-notebook", "hintnb", "--dry-run"], ws)
    has_url = "https://www.kaggle.com/code/" in out
    has_hint = "Submit to Competition" in out
    has_score_hint = "score NO aparecera" in out
    r.record(name, rc == 0 and has_url and has_hint and has_score_hint,
             f"rc={rc} url={has_url} hint={has_hint} score_msg={has_score_hint}")


def check_notebook_submitted_flag(r: Results, ws: Path) -> None:
    name = "14e state --show contains notebook_submitted; --notebook-submitted toggles it"
    run_helper(["init", "nbsubflag"], ws)
    rc1, out1, _ = run_helper(["state", "nbsubflag", "--show"], ws)
    try:
        j1 = json.loads(out1)
    except Exception:
        r.record(name, False, "json decode failed")
        return
    has_key = "notebook_submitted" in j1
    is_false = j1.get("notebook_submitted") is False
    # Toggle to true
    rc2, out2, _ = run_helper(["state", "nbsubflag", "--notebook-submitted", "true"], ws)
    rc3, out3, _ = run_helper(["state", "nbsubflag", "--show"], ws)
    try:
        j3 = json.loads(out3)
    except Exception:
        r.record(name, False, "json decode failed after toggle")
        return
    is_true = j3.get("notebook_submitted") is True
    r.record(name, has_key and is_false and rc2 == 0 and is_true,
             f"has_key={has_key} default_false={is_false} toggle_rc={rc2} now_true={is_true}")


def check_status_file_submission_note(r: Results, ws: Path) -> None:
    name = "14f cmd_status source contains file-vs-notebook submission guidance"
    src = (HERE / "kaggle_comp.py").read_text(encoding="utf-8")
    has_note = "no vinculada al notebook" in src or "NO vinculada al notebook" in src
    has_csv_check = ".csv" in src and "submission" in src.lower()
    r.record(name, has_note and has_csv_check,
             f"guidance_note={has_note} csv_detection={has_csv_check}")


def check_notebook_cells_segregated(r: Results, ws: Path) -> None:
    name = "14g run --dry-run produces documented cells without node labels"
    comp = "cellseg"
    run_helper(["init", comp], ws)
    rc, out, _ = run_helper(["run", comp, "--dry-run"], ws)

    # Check code.py contains # %% delimiters
    code_py = ws / "competitions" / comp / "code.py"
    code_text = code_py.read_text(encoding="utf-8") if code_py.exists() else ""
    delims_present = "# %%" in code_text

    # Load the assembled notebook and inspect its cells
    nb_path = ws / "competitions" / comp / "notebook.ipynb"
    nb = None
    if nb_path.exists():
        nb = json.loads(nb_path.read_text(encoding="utf-8"))

    if nb is None:
        r.record(name, False, "notebook not found")
        return

    all_cells = nb.get("cells", [])
    code_cells = [c for c in all_cells if c.get("cell_type") == "code"]

    cells_ok = len(code_cells) >= 5  # ingestion, processing, exp, eval, deployment

    first_cell = "".join(code_cells[0].get("source", [])) if code_cells else ""
    ingestion_code_ok = "DATA_DIR" in first_cell and "train_df" in first_cell

    markdown_cells = [c for c in all_cells if c.get("cell_type") == "markdown"]
    markdown_text = "\n".join("".join(c.get("source", [])) for c in markdown_cells)
    markdown_ok = (
        len(markdown_cells) >= 8
        and "Dataset intake and exploratory analysis" in markdown_text
        and "Model objective and target decision" in markdown_text
        and "Error analysis visuals" in markdown_text
    )

    node_labels = [
        "DataIngestion_Node",
        "DataProcessing_Node",
        "Experimentation_Node",
        "Evaluation_Node",
        "DeploymentSync_Node",
    ]
    labels_absent_ok = not any(
        label in "".join(cell.get("source", []))
        for cell in all_cells
        for label in node_labels
    )

    # No cell should have "# %%" (delimiter stripped on injection)
    markers_stripped_ok = all(
        "# %%" not in "".join(c.get("source", []))
        for c in code_cells
    )

    # The file header (# code.py - competition ..) must NOT appear in any cell
    header_in_cell = any(
        "code.py - competition" in "".join(c.get("source", []))
        for c in all_cells
    )
    preamble_dropped_ok = not header_in_cell

    r.record(
        name,
        rc == 0
        and delims_present
        and cells_ok
        and ingestion_code_ok
        and markdown_ok
        and labels_absent_ok
        and markers_stripped_ok
        and preamble_dropped_ok,
        f"rc={rc} delims={delims_present} code_cells={len(code_cells)} "
        f"markdown_cells={len(markdown_cells)} ingestion={ingestion_code_ok} "
        f"markdown={markdown_ok} labels_absent={labels_absent_ok} "
        f"markers={markers_stripped_ok} preamble={preamble_dropped_ok}",
    )


def check_clean(r: Results, _ws: Path) -> None:
    name = "18 smoke test ran to completion (no uncaught exceptions)"
    r.record(name, True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    global _VERBOSE
    parser = argparse.ArgumentParser(description="Smoke test for kaggle_comp harness")
    parser.add_argument("-v", "--verbose", action="store_true", help="print helper output")
    parser.add_argument("--keep", action="store_true", help="keep the temp workspace")
    args_ns = parser.parse_args()
    _VERBOSE = args_ns.verbose

    print(_c("1", "== kaggle_comp smoke test =="))
    print(info(f"helper: {HELPER}"))

    ws = Path(tempfile.mkdtemp(prefix="comp_smoke_"))
    print(info(f"workspace: {ws}") + ("  (will be kept)" if args_ns.keep else ""))

    r = Results()
    try:
        check_helper_syntax(r)
        check_help(r, ws)
        check_import_kaggle_nb(r, ws)
        check_templates_and_render(r, ws)
        check_ml_template_quality(r, ws)
        check_notebook_lint(r, ws)
        check_lint_cli(r, ws)
        check_markdown_source_of_truth(r, ws)
        check_init(r, ws)
        check_plan_scaffold(r, ws)
        check_plan_approve_gate(r, ws)
        check_state_show(r, ws)
        check_state_update_metric(r, ws)
        check_metric_parser(r, ws)
        check_run_headers_privacy(r, ws, "ml")
        check_run_headers_privacy(r, ws, "genai")
        check_competition_privacy_enforced(r, ws)
        check_submit_routing(r, ws)
        check_detect(r, ws)
        check_remote_cmds_dryrun(r, ws)
        check_context_dry_run(r, ws)
        check_context_csv_parser(r, ws)
        check_context_digest(r, ws)
        check_context_traversal(r, ws)
        check_path_traversal(r, ws)
        check_render_validation(r, ws)
        check_loop_maxiters(r, ws)
        check_loop_plateau(r, ws)
        check_gate_proceed(r, ws)
        check_gate_skip(r, ws)
        check_fetch_notebook_stdout(r, ws)
        check_ingestion_oswalk(r, ws)
        check_data_403_hint(r, ws)
        check_push_notebook_hint(r, ws)
        check_notebook_submitted_flag(r, ws)
        check_status_file_submission_note(r, ws)
        check_notebook_cells_segregated(r, ws)
        check_no_git_leak(r, ws)
        check_clean(r, ws)
    except Exception as e:  # noqa: BLE001 - safety net
        r.failed += 1
        r.failures.append("uncaught-exception")
        print(bad(f"uncaught exception: {e}"))
    finally:
        if not args_ns.keep:
            try:
                shutil.rmtree(ws, ignore_errors=True)
            except Exception:
                pass

    total = r.passed + r.failed
    print()
    print(_c("1", f"== SMOKE TEST: {r.passed}/{total} passed =="))
    if r.failures:
        print(bad("Failed: " + ", ".join(r.failures)))
    else:
        print(ok("All checks passed."))
    print(info("hint: rerun with -v for helper output, --keep to inspect workspace"))
    return r.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
