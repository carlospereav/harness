#!/usr/bin/env python3
"""smoke_test.py — End-to-end smoke test for the kaggle_nb helper CLI.

Runs the full notebook lifecycle (new -> write-code -> append-code -> push
--dry-run) in a throwaway temp workspace. Does NOT contact the Kaggle API
and does NOT require credentials: the only Kaggle-touching step uses
``--dry-run`` so the helper just prints the command it would run.

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
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / "kaggle_nb.py"
PY = sys.executable

# Colour helpers (optional, degrade to plain if not a tty)
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


_VERBOSE = False


def run_helper(args: list[str], workspace: Path) -> tuple[int, str, str]:
    """Run the helper with --workspace <workspace> inserted before the subcommand."""
    cmd = [PY, str(HELPER), "--workspace", str(workspace), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if _VERBOSE:
        print(info("$ " + " ".join(cmd)))
        if proc.stdout:
            print(info(proc.stdout))
        if proc.stderr:
            print(info(proc.stderr))
    return proc.returncode, proc.stdout, proc.stderr


def load_ipynb(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #

def check_helper_syntax(r: Results) -> None:
    name = "01 helper kaggle_nb.py parses as valid Python"
    try:
        ast.parse(HELPER.read_text(encoding="utf-8"))
        r.record(name, True)
    except SyntaxError as e:
        r.record(name, False, str(e))


def check_help(r: Results, _ws: Path) -> None:
    name = "02 kaggle_nb.py --help displays help"
    proc = subprocess.run([PY, str(HELPER), "--help"], capture_output=True, text=True)
    ok_help = proc.returncode == 0 and "usage:" in proc.stdout and "push" in proc.stdout
    r.record(name, ok_help, "rc=" + str(proc.returncode))


def check_setup(r: Results, ws: Path) -> None:
    name = "03 setup runs without crashing"
    rc, _, _ = run_helper(["setup"], ws)
    r.record(name, rc == 0, "rc=" + str(rc))


def check_new(r: Results, ws: Path) -> Path:
    name = "04 new demo-notebook creates ipynb + PRIVATE notebook metadata"
    d = ws / "demo-notebook"
    rc, out, _ = run_helper(["new", "demo-notebook", "--title", "Demo"], ws)
    nb_path = d / "notebook.ipynb"
    meta_path = d / "kernel-metadata.json"
    code_path = d / "code.py"
    files_ok = nb_path.exists() and meta_path.exists() and code_path.exists()
    meta_ok = False
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta_ok = (
            str(meta.get("is_private", "")).lower() == "true"
            and meta.get("kernel_type") == "notebook"
            and meta.get("title") == "Demo"
        )
    r.record(name, rc == 0 and files_ok and meta_ok, f"rc={rc} files={files_ok} meta={meta_ok}")
    return d


def check_write_code(r: Results, ws: Path, d: Path) -> str:
    name = "05 write-code writes file as the only code cell"
    src = ws / "mycode.py"
    code = "import pandas as pd\nprint('hello kaggle')\nx = 42\n"
    src.write_text(code, encoding="utf-8")
    rc, out, _ = run_helper(["write-code", "demo-notebook", "--from", str(src)], ws)
    nb = load_ipynb(d / "notebook.ipynb")
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    content_ok = len(code_cells) == 1 and "".join(code_cells[0]["source"]) == code
    r.record(name, rc == 0 and content_ok, f"cells={len(nb['cells'])} code_cells={len(code_cells)}")
    return code


def check_append_code(r: Results, ws: Path, d: Path) -> None:
    name = "06 append-code adds a second code cell"
    src = ws / "extra.py"
    extra = "# extra cell\nprint('appended')\n"
    src.write_text(extra, encoding="utf-8")
    rc, _, _ = run_helper(["append-code", "demo-notebook", "--from", str(src)], ws)
    nb = load_ipynb(d / "notebook.ipynb")
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    last_ok = len(code_cells) == 2 and "".join(code_cells[-1]["source"]) == extra
    r.record(name, rc == 0 and last_ok, f"code_cells={len(code_cells)}")


def check_push_dryrun(r: Results, ws: Path) -> None:
    name = "07 push --dry-run prints kaggle kernels push and keeps is_private"
    rc, out, _ = run_helper(["push", "demo-notebook", "--dry-run"], ws)
    push_ok = "[DRY-RUN] kaggle kernels push -p" in out
    priv_ok = "is_private=true" in out
    r.record(name, rc == 0 and push_ok and priv_ok, f"rc={rc} push={push_ok} priv={priv_ok}")


def check_traversal(r: Results, ws: Path) -> None:
    name = "08 path traversal slugs are rejected"
    bad_slugs = ["..", "..\\leak", "bad/slug", "C:\\leak", ""]
    all_rejected = True
    for slug in bad_slugs:
        rc, _, err = run_helper(["new", slug], ws)
        if rc == 0:
            all_rejected = False
            print(info(f"  slug {slug!r} was ACCEPTED (should have been rejected)"))
            break
    r.record(name, all_rejected, f"tested {len(bad_slugs)} slugs")


def check_owner_traversal(r: Results, ws: Path) -> None:
    name = "09 owner path traversal in output is rejected"
    bad_refs = ["..\\leak/demo-notebook", "bad owner/demo-notebook", "/demo-notebook"]
    all_rejected = True
    for ref in bad_refs:
        rc, _, _ = run_helper(["output", ref, "--dry-run"], ws)
        if rc == 0:
            all_rejected = False
            print(info(f"  ref {ref!r} was ACCEPTED (should have been rejected)"))
            break
    r.record(name, all_rejected, f"tested {len(bad_refs)} refs")


def check_default_metadata_private_and_slug(r: Results, ws: Path) -> None:
    name = "11 default_metadata private=False -> is_private=false; id prefixed with user/"
    # Import kaggle_nb in-process.
    sys.path.insert(0, str(HERE))
    import kaggle_nb as knb  # type: ignore

    # Stub a username via env so _resolve_kaggle_username finds it.
    old_env = os.environ.get("KAGGLE_USERNAME")
    os.environ["KAGGLE_USERNAME"] = "smoketest"
    try:
        meta_public = knb.default_metadata("my-slug", private=False)
        meta_private = knb.default_metadata("my-slug", private=True)
        meta_no_user = knb.default_metadata("owner/explicit")  # already has /
    finally:
        if old_env is not None:
            os.environ["KAGGLE_USERNAME"] = old_env
        else:
            os.environ.pop("KAGGLE_USERNAME", None)

    public_ok = str(meta_public.get("is_private", "")) == "false"
    private_ok = str(meta_private.get("is_private", "")) == "true"
    id_prefixed = meta_public.get("id", "").startswith("smoketest/")
    explicit_id_ok = meta_no_user.get("id") == "owner/explicit"
    r.record(name, public_ok and private_ok and id_prefixed and explicit_id_ok,
             f"public={public_ok} private={private_ok} id_prefixed={id_prefixed} explicit={explicit_id_ok}")


def check_clean(r: Results, _ws: Path) -> None:
    name = "10 runs to completion (no uncaught exceptions)"
    r.record(name, True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    global _VERBOSE
    parser = argparse.ArgumentParser(description="Smoke test for kaggle_nb helper")
    parser.add_argument("-v", "--verbose", action="store_true", help="print helper output")
    parser.add_argument("--keep", action="store_true", help="keep the temp workspace")
    args_ns = parser.parse_args()
    _VERBOSE = args_ns.verbose

    print(_c("1", "== kaggle_nb smoke test =="))
    print(info(f"helper: {HELPER}"))

    ws = Path(tempfile.mkdtemp(prefix="kgnb_smoke_"))
    print(info(f"workspace: {ws}") + ("  (will be kept)" if args_ns.keep else ""))

    r = Results()
    try:
        check_helper_syntax(r)
        check_help(r, ws)
        check_setup(r, ws)
        d = check_new(r, ws)
        check_write_code(r, ws, d)
        check_append_code(r, ws, d)
        check_push_dryrun(r, ws)
        check_traversal(r, ws)
        check_owner_traversal(r, ws)
        check_default_metadata_private_and_slug(r, ws)
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
    print(info(f"hint: rerun with -v for helper output, --keep to inspect workspace"))
    return r.exit_code


if __name__ == "__main__":
    raise SystemExit(main())