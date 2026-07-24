#!/usr/bin/env python3
"""kaggle_nb.py — Helper CLI for creating, editing and pushing private Kaggle
notebooks from opencode.

The notebook source code lives in a workspace OUTSIDE any public git repo
(default: ~/kaggle-workspace) so it never leaks to GitHub. Notebooks are
always pushed as private kernels (``is_private: "true"``) to Kaggle.

Usage:
    python kaggle_nb.py --help
    python kaggle_nb.py setup
    python kaggle_nb.py new <slug> [--title T] [--gpu] [--internet] [--dataset D]
    python kaggle_nb.py pull <owner>/<slug>
    python kaggle_nb.py write-code <slug> --from <file.py>
    python kaggle_nb.py append-code <slug> --from <file.py>
    python kaggle_nb.py push <slug> [--dry-run]
    python kaggle_nb.py status <owner>/<slug>
    python kaggle_nb.py output <owner>/<slug> [--to DIR]
    python kaggle_nb.py list [user]

Environment:
    KAGGLE_WORKSPACE  override workspace root (default: ~/kaggle-workspace)
    KAGGLE_CONFIG_DIR override kaggle credentials dir (default: ~/.kaggle)

Requirements:
    pip install kaggle nbformat
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# Strict slug pattern: only safe filename characters. Rejects path separators,
# "..", absolute paths, drive letters, etc. — preventing path traversal.
_SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+")

NBFORMAT_AVAILABLE = False
try:
    import nbformat  # type: ignore
    from nbformat.v4 import new_code_cell, new_notebook  # type: ignore

    NBFORMAT_AVAILABLE = True
except ImportError:  # pragma: no cover - handled at runtime
    pass


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

DEFAULT_WORKSPACE = Path(os.environ.get("KAGGLE_WORKSPACE", Path.home() / "kaggle-workspace"))
KAGGLE_CONFIG_DIR = Path(os.environ.get("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle"))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def workspace_root(override: str | None = None) -> Path:
    root = Path(override) if override else DEFAULT_WORKSPACE
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_slug(slug: str) -> str:
    """Validate a notebook slug strictly to prevent path traversal.

    Rejects empty, absolute, separator-containing, ``..``-like, or otherwise
    unsafe values. Returns the slug unchanged if valid.
    """
    if not slug or not isinstance(slug, str):
        raise ValueError("slug must be a non-empty string")
    if slug in {".", ".."}:
        raise ValueError(f"invalid slug: {slug!r}")
    if Path(slug).is_absolute():
        raise ValueError(f"slug must not be absolute: {slug!r}")
    if os.sep in slug or (os.altsep and os.altsep in slug):
        raise ValueError(f"slug must not contain path separators: {slug!r}")
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            f"invalid slug: {slug!r} (allowed: letters, digits, '_', '.', '-')"
        )
    return slug


def slug_dir(slug: str, workspace: str | None = None) -> Path:
    validate_slug(slug)
    d = workspace_root(workspace) / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _split_owner_slug(ref: str) -> tuple[str, str]:
    """Split '<owner>/<slug>' and validate both parts against traversal."""
    if "/" not in ref:
        raise ValueError("expected <owner>/<slug>")
    owner, slug = ref.split("/", 1)
    if not owner:
        raise ValueError("empty owner in reference")
    # Reject backslashes / path components in the owner portion too, so that
    # the auto-generated output dir (cmd_output) cannot escape the workspace.
    if os.sep in owner or (os.altsep and os.altsep in owner):
        raise ValueError(f"invalid owner (path separator): {owner!r}")
    if not _SLUG_RE.fullmatch(owner):
        raise ValueError(f"invalid owner: {owner!r}")
    validate_slug(slug)
    return owner, slug


def _run(cmd: list[str], *, dry_run: bool = False, cwd: Path | None = None) -> int:
    """Run a command, or print it when dry-running."""
    printable = " ".join(cmd)
    if dry_run:
        print(f"[DRY-RUN] {printable}")
        return 0
    print(f"$ {printable}")
    proc = subprocess.run(cmd, cwd=cwd, check=False)
    return proc.returncode


def _kaggle_available() -> bool:
    return shutil.which("kaggle") is not None


def _kaggle_api_available() -> bool:
    try:
        import importlib

        return importlib.util.find_spec("kaggle") is not None
    except Exception:
        return False


def _ensure_nbformat() -> None:
    if NBFORMAT_AVAILABLE:
        return
    print(
        "[ERROR] 'nbformat' is required to manipulate notebooks.\n"
        "        Install it with:  pip install nbformat",
        file=sys.stderr,
    )
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# kernel-metadata.json
# --------------------------------------------------------------------------- #

def default_metadata(slug: str, *, title: str | None = None, gpu: bool = False,
                    internet: bool = False, datasets: Iterable[str] = ()) -> dict:
    meta = {
        "id": slug,
        "title": title or slug,
        "code_file": "notebook.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": "true",
        "enable_gpu": "true" if gpu else "false",
        "enable_internet": "true" if internet else "false",
        "keywords": [],
        "dataset_sources": list(datasets),
        "kernel_sources": [],
        "competition_sources": [],
    }
    return meta


def read_metadata(d: Path) -> dict:
    meta_path = d / "kernel-metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"kernel-metadata.json not found in {d}")
    with meta_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_metadata(d: Path, meta: dict) -> None:
    meta_path = d / "kernel-metadata.json"
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  wrote {meta_path}")


# --------------------------------------------------------------------------- #
# Notebook (.ipynb)
# --------------------------------------------------------------------------- #

def empty_notebook_path(d: Path) -> Path:
    return d / "notebook.ipynb"


def write_empty_notebook(d: Path) -> Path:
    _ensure_nbformat()
    nb = new_notebook()
    nb.metadata["kernelspec"] = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }
    nb.metadata["language_info"] = {"name": "python"}
    path = empty_notebook_path(d)
    with path.open("w", encoding="utf-8") as f:
        nbformat.write(nb, f, 4)
    print(f"  wrote {path}")
    return path


def load_notebook(d: Path) -> "nbformat.NotebookNode":  # type: ignore[name-defined]
    _ensure_nbformat()
    path = empty_notebook_path(d)
    if not path.exists():
        raise FileNotFoundError(f"notebook not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return nbformat.read(f, as_version=4)


def save_notebook(d: Path, nb) -> None:
    path = empty_notebook_path(d)
    with path.open("w", encoding="utf-8") as f:
        nbformat.write(nb, f, 4)
    print(f"  wrote {path}")


def set_notebook_code(d: Path, code: str) -> None:
    """Replace ALL code cells with a single cell containing ``code``."""
    nb = load_notebook(d)
    code_cells = [c for c in nb.cells if c.cell_type == "code"]
    new = new_code_cell(code)
    if code_cells:
        first = code_cells[0]
        idx = nb.cells.index(first)
        # remove existing code cells
        for c in code_cells[1:]:
            nb.cells.remove(c)
        nb.cells[idx] = new
    else:
        nb.cells.append(new)
    save_notebook(d, nb)


def append_notebook_code(d: Path, code: str) -> None:
    """Append a new code cell with ``code`` to the notebook."""
    nb = load_notebook(d)
    nb.cells.append(new_code_cell(code))
    save_notebook(d, nb)


# --------------------------------------------------------------------------- #
# code.py convenience mirror
# --------------------------------------------------------------------------- #

CODE_PY = "code.py"


def read_code_file(file_path: Path) -> str:
    if not file_path.exists():
        raise FileNotFoundError(f"code file not found: {file_path}")
    return file_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_setup(args: argparse.Namespace) -> int:
    print("== kaggle_nb setup ==")
    ws = workspace_root(args.workspace)
    print(f"  workspace: {ws}")
    ws.mkdir(parents=True, exist_ok=True)

    cli_ok = _kaggle_available()
    api_ok = _kaggle_api_available()
    print(f"  kaggle CLI : {'OK' if cli_ok else 'MISSING'}")
    print(f"  kaggle API : {'OK' if api_ok else 'MISSING'}")
    if not (cli_ok or api_ok):
        print("  -> run:  pip install kaggle nbformat")
    if not NBFORMAT_AVAILABLE:
        print("  nbformat   : MISSING -> run:  pip install nbformat")
    else:
        print("  nbformat   : OK")

    creds = KAGGLE_CONFIG_DIR / "kaggle.json"
    print(f"  credentials: {creds}  ({'OK' if creds.exists() else 'MISSING'})")
    if not creds.exists():
        print("  -> get your token from https://www.kaggle.com/settings -> Create New Token")
        print(f"     save it as {creds}  (format: {{\"username\":\"...\",\"key\":\"...\"}})")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    d = slug_dir(args.slug, args.workspace)
    print(f"== new notebook: {args.slug} -> {d} ==")
    if (d / "kernel-metadata.json").exists() and not args.force:
        print(f"  already exists (use --force to overwrite)")
        return 1
    meta = default_metadata(
        args.slug,
        title=args.title,
        gpu=args.gpu,
        internet=args.internet,
        datasets=args.dataset,
    )
    write_metadata(d, meta)
    write_empty_notebook(d)
    # mirror empty code.py for convenient editing from opencode
    safe_slug = validate_slug(args.slug).replace("\n", " ").replace("\r", " ")
    (d / CODE_PY).write_text(
        "# code.py — edit this, then run: kaggle_nb.py push " + safe_slug + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {d / CODE_PY}")
    print("  notebook is PRIVATE (is_private=true) by default")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    _, slug = _split_owner_slug(args.ref)
    d = slug_dir(slug, args.workspace)
    print(f"== pull {args.ref} -> {d} ==")
    return _run(["kaggle", "kernels", "pull", args.ref, "-p", str(d)], dry_run=args.dry_run)


def cmd_write_code(args: argparse.Namespace) -> int:
    d = slug_dir(args.slug, args.workspace)
    src = Path(args.from_file)
    code = read_code_file(src)
    print(f"== write-code {src} -> notebook {args.slug} ==")
    set_notebook_code(d, code)
    # mirror into code.py so the source-of-truth file stays in sync
    (d / CODE_PY).write_text(code, encoding="utf-8")
    print(f"  mirrored to {d / CODE_PY}")
    return 0


def cmd_append_code(args: argparse.Namespace) -> int:
    d = slug_dir(args.slug, args.workspace)
    src = Path(args.from_file)
    code = read_code_file(src)
    print(f"== append-code {src} -> notebook {args.slug} ==")
    append_notebook_code(d, code)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    d = slug_dir(args.slug, args.workspace)
    print(f"== push notebook: {args.slug} ==")
    # If code.py exists and is newer than notebook.ipynb, inject it first
    code_py = d / CODE_PY
    nb_path = empty_notebook_path(d)
    if code_py.exists():
        code_newer = not nb_path.exists() or code_py.stat().st_mtime > nb_path.stat().st_mtime
        if code_newer:
            print(f"  injecting {code_py.name} into notebook (code.py is newer)")
            set_notebook_code(d, read_code_file(code_py))

    # Verify privacy
    try:
        meta = read_metadata(d)
        if str(meta.get("is_private", "")).lower() != "true":
            print("  [WARN] is_private != 'true'; forcing private to avoid GitHub leak")
            meta["is_private"] = "true"
            write_metadata(d, meta)
        else:
            print("  is_private=true  (notebook stays private)")
    except FileNotFoundError:
        print("  [WARN] no kernel-metadata.json; creating a private one")
        write_metadata(d, default_metadata(args.slug))
        write_empty_notebook(d)

    rc = _run(["kaggle", "kernels", "push", "-p", str(d)], dry_run=args.dry_run, cwd=d)
    if rc == 0 and not args.dry_run:
        print(f"  pushed: https://www.kaggle.com/code/<user>/{args.slug}")
    return rc


def cmd_status(args: argparse.Namespace) -> int:
    _split_owner_slug(args.ref)
    return _run(["kaggle", "kernels", "status", args.ref], dry_run=args.dry_run)


def cmd_output(args: argparse.Namespace) -> int:
    _split_owner_slug(args.ref)
    dest = Path(args.to) if args.to else workspace_root(args.workspace) / (args.ref.replace("/", "_") + "_output")
    dest.mkdir(parents=True, exist_ok=True)
    return _run(["kaggle", "kernels", "output", args.ref, "-p", str(dest)], dry_run=args.dry_run)


def cmd_list(args: argparse.Namespace) -> int:
    user = args.user or os.environ.get("KAGGLE_USERNAME", "")
    cmd = ["kaggle", "kernels", "list"]
    if user:
        cmd += ["--user", user]
    return _run(cmd, dry_run=args.dry_run)


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kaggle_nb.py",
        description="Create, edit and push PRIVATE Kaggle notebooks from opencode. "
                    "Notebooks live outside any public git repo so they never leak to GitHub.",
    )
    p.add_argument("--workspace", help=f"workspace root (default: {DEFAULT_WORKSPACE})")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="check/install kaggle, nbformat, credentials, workspace")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("new", help="scaffold a new private notebook")
    sp.add_argument("slug")
    sp.add_argument("--title")
    sp.add_argument("--gpu", action="store_true")
    sp.add_argument("--internet", action="store_true")
    sp.add_argument("--dataset", action="append", default=[], metavar="OWNER/DATASET")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_new)

    sp = sub.add_parser("pull", help="pull an existing notebook")
    sp.add_argument("ref", help="<owner>/<slug>")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_pull)

    sp = sub.add_parser("write-code", help="replace notebook code cell(s) with the contents of a .py file")
    sp.add_argument("slug")
    sp.add_argument("--from", dest="from_file", required=True, help="path to .py file")
    sp.set_defaults(func=cmd_write_code)

    sp = sub.add_parser("append-code", help="append a new code cell from a .py file")
    sp.add_argument("slug")
    sp.add_argument("--from", dest="from_file", required=True, help="path to .py file")
    sp.set_defaults(func=cmd_append_code)

    sp = sub.add_parser("push", help="inject code.py (if newer) and run kaggle kernels push (private)")
    sp.add_argument("slug")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_push)

    sp = sub.add_parser("status", help="kaggle kernels status <owner>/<slug>")
    sp.add_argument("ref")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("output", help="download notebook output")
    sp.add_argument("ref")
    sp.add_argument("--to", help="destination dir")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_output)

    sp = sub.add_parser("list", help="list kernels (defaults to current user)")
    sp.add_argument("user", nargs="?", default="")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(func=cmd_list)

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