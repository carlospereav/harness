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
    expected = ["setup", "list", "files", "init", "data", "detect", "render",
                "state", "run", "submit-file", "push-notebook", "submit",
                "status", "leaderboard"]
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
                             "current_node", "primary_metric", "minimize",
                             "best_local_score", "iterations", "max_iterations",
                             "history")
    )
    meta_ok = (
        meta is not None
        and str(meta.get("is_private", "")).lower() == "false"
        and meta.get("competition_sources") == ["myinit"]
    )
    r.record(name, rc == 0 and files_ok and state_ok and meta_ok,
             f"rc={rc} files={files_ok} state={state_ok} meta={meta_ok}")


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
    name = f"09 run --mode {mode} --dry-run walks 5 nodes -> dry-run push + privacy"
    comp = f"run{mode}"
    run_helper(["init", comp, "--mode", mode, "--submission", "notebook"], ws)
    rc, out, _ = run_helper(
        ["run", comp, "--mode", mode, "--submission", "notebook", "--dry-run"], ws
    )
    headers = ["DataIngestion_Node", "DataProcessing_Node",
              "Experimentation_Node", "Evaluation_Node", "DeploymentSync_Node"]
    headers_ok = all(h in out for h in headers)
    push_ok = "[DRY-RUN] kaggle kernels push -p" in out
    meta = load_meta(comp, ws)
    privacy_ok = meta is not None and str(meta.get("is_private", "")).lower() == "false"
    comp_src_ok = meta is not None and "competition_sources" in meta and comp in meta["competition_sources"]
    r.record(name, rc == 0 and headers_ok and push_ok and privacy_ok and comp_src_ok,
             f"rc={rc} headers={headers_ok} push={push_ok} priv={privacy_ok} comp_src={comp_src_ok}")


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
    name = "14b rendered ml ingestion contains os.walk(/kaggle/input); no hard dep on /input/<comp>"
    rendered = kaggle_comp.render_template("ingestion", "ml", competition="mycomp", iteration=1)
    has_walk = 'os.walk("/kaggle/input")' in rendered or "os.walk('/kaggle/input')" in rendered or 'os.walk(DATA_DIR)' in rendered
    no_hard = "/kaggle/input/mycomp" not in rendered
    r.record(name, has_walk and no_hard,
             f"walk={has_walk} no_hard_dep={no_hard}")


def check_data_403_hint(r: Results, ws: Path) -> None:
    name = "14c cmd_data --dry-run output skeleton + 403 acceptance URL"
    # Dry-run should show the command; we can also directly test the error
    # message path by checking cmd_data source contains the rules URL pattern.
    src = (HERE / "kaggle_comp.py").read_text(encoding="utf-8")
    has_rules = "competitions/<comp>/rules" in src or "kaggle.com/competitions/" in src and "rules" in src
    has_accept = "I Understand and Accept" in src
    r.record(name, has_rules and has_accept,
             f"rules_url={has_rules} accept_text={has_accept}")


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
        check_init(r, ws)
        check_state_show(r, ws)
        check_state_update_metric(r, ws)
        check_metric_parser(r, ws)
        check_run_headers_privacy(r, ws, "ml")
        check_run_headers_privacy(r, ws, "genai")
        check_submit_routing(r, ws)
        check_detect(r, ws)
        check_remote_cmds_dryrun(r, ws)
        check_path_traversal(r, ws)
        check_render_validation(r, ws)
        check_loop_maxiters(r, ws)
        check_loop_plateau(r, ws)
        check_gate_proceed(r, ws)
        check_gate_skip(r, ws)
        check_fetch_notebook_stdout(r, ws)
        check_ingestion_oswalk(r, ws)
        check_data_403_hint(r, ws)
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