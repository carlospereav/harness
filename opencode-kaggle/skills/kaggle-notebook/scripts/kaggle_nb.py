#!/usr/bin/env python3
"""kaggle_nb.py — Helper CLI for creating, editing and pushing private Kaggle
notebooks from opencode.

The notebook source code lives in a workspace OUTSIDE any public git repo
(default: ~/kaggle-workspace) so it never leaks to GitHub. Notebooks are
PRIVATE by default for safety; public publishing (e.g. for competition medals)
is opt-in with the ``--public`` flag.

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
    from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook  # type: ignore

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


def _resolve_kaggle_username() -> str | None:
    """Best-effort Kaggle username resolution.
    Tries: kaggle config view > KAGGLE_USERNAME env > kaggle.json > credentials.json.
    Returns the username or None if unresolvable.
    """
    # 1. `kaggle config view` (parses "username: <val>" line)
    if shutil.which("kaggle"):
        try:
            proc = subprocess.run(
                ["kaggle", "config", "view"],
                capture_output=True, text=True, encoding="utf-8", timeout=10,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if line.lower().startswith("username:"):
                        val = line.split(":", 1)[1].strip()
                        if val:
                            return val
            # stderr may also contain the username on some versions
            for line in proc.stderr.splitlines():
                line = line.strip()
                if line.lower().startswith("username:"):
                    val = line.split(":", 1)[1].strip()
                    if val:
                        return val
        except Exception:
            pass

    # 2. Environment variable
    env_user = os.environ.get("KAGGLE_USERNAME", "").strip()
    if env_user:
        return env_user

    # 3. kaggle.json (legacy)
    kaggle_json = KAGGLE_CONFIG_DIR / "kaggle.json"
    if kaggle_json.exists():
        try:
            with kaggle_json.open("r", encoding="utf-8") as f:
                creds = json.load(f)
            user = (creds.get("username") or "").strip()
            if user:
                return user
        except Exception:
            pass

    # 4. credentials.json (OAuth)
    creds_json = KAGGLE_CONFIG_DIR / "credentials.json"
    if creds_json.exists():
        try:
            with creds_json.open("r", encoding="utf-8") as f:
                creds = json.load(f)
            user = (creds.get("username") or "").strip()
            if user:
                return user
        except Exception:
            pass

    return None


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
                    internet: bool = False, datasets: Iterable[str] = (),
                    private: bool = False) -> dict:
    # Resolve the kernel id: Kaggle requires "<username>/<slug>".
    # If the slug is already an owner/slug pair, use it as-is.
    # Otherwise, prefix with the resolved username if available.
    kid = slug
    if "/" not in slug:
        user = _resolve_kaggle_username()
        if user:
            kid = f"{user}/{slug}"

    meta = {
        "id": kid,
        "title": title or slug,
        "code_file": "notebook.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": "true" if private else "false",
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


# --------------------------------------------------------------------------- #
# Percent-format cell splitting (# %% convention, compatible with VS Code /
# PyCharm / jupytext). When code.py contains ``# %%`` delimiters, the notebook
# gets one cell per segment.  When no delimiters exist, the whole code.py is
# a single code cell (**backward compatible** with scratch notebooks).
# --------------------------------------------------------------------------- #

# Matches ``# %%`` or ``#%%` `` at the start of a line (whitespace-tolerant).
_CELL_DELIM_RE = re.compile(r"^[ \t]*#[ \t]*%%", re.MULTILINE)

# ``# %% [markdown]`` (case-insensitive) — the segment becomes a markdown cell.
_MD_RE = re.compile(r"^[ \t]*#[ \t]*%%[ \t]*\[markdown\]", re.IGNORECASE)


def _is_comment_only(text: str) -> bool:
    """Return True when every non-blank line starts with ``#``."""
    return all(
        not s or s.startswith("#")
        for ln in text.splitlines()
        for s in (ln.strip(),)
    )


def split_code_into_cells(code: str) -> list[tuple[str, str]]:
    """Split Python source on ``# %%`` percent-format cell delimiters.

    Returns a list of ``(cell_type, body)`` where *cell_type* is ``"code"`` or
    ``"markdown"`` and *body* is the cell source (trailing ``\\n`` guaranteed).

    Rules (consistent with the percent-format convention):

    * If no ``# %%`` delimiter exists anywhere in *code*, the whole string is
      returned as a single ``("code", code)`` cell — **backward-compatible**
      with scratch notebooks that predate this feature.
    * Preamble (content before the first ``# %%`` line) is **dropped** when it
      is whitespace-only or **comment-only** (the ``code.py`` file header).
      Preamble containing real Python code is kept as a code cell — so the
      agent can add setup / imports at the top without them being silently
      dropped.
    * Each ``# %%`` segment is a code cell.  The marker line itself is stripped;
      the cell body starts with the node's own header comment (e.g.
      ``# DataIngestion_Node - Traditional ML``).
    * ``# %% [markdown]`` segments become **markdown** cells.  Leading ``# ``
      comment markers on each line are stripped so the notebook renders plain
      Markdown text.
    * Empty / whitespace-only segments are skipped.
    """
    if not code or not code.strip():
        return []

    # Fast path: no percent-format delimiters → single code cell, source verbatim.
    if not _CELL_DELIM_RE.search(code):
        return [("code", code)]

    lines = code.splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if _CELL_DELIM_RE.match(ln)]

    cells: list[tuple[str, str]] = []

    # -- preamble --------------------------------------------------------------
    if starts[0] > 0:
        preamble = "".join(lines[:starts[0]])
        if preamble.strip() and not _is_comment_only(preamble):
            cells.append(("code", preamble.rstrip("\n") + "\n"))

    # -- cells after each delimiter --------------------------------------------
    bounds = starts + [len(lines)]
    for k, start in enumerate(starts):
        # body = everything after the ``# %%`` marker line through the next
        # delimiter (or EOF).
        seg = lines[start + 1:bounds[k + 1]]
        is_md = bool(_MD_RE.match(lines[start]))

        if is_md:
            # Strip leading ``# `` / ``#`` from each line so the notebook
            # renders clean Markdown (percent-format convention).
            md_lines: list[str] = []
            for ln in seg:
                stripped = ln.lstrip(" \t")
                if stripped.startswith("#"):
                    s2 = stripped[1:]  # drop '#' (and optionally a space)
                    if s2.startswith(" "):
                        s2 = s2[1:]
                    md_lines.append(s2)
                else:
                    md_lines.append(ln)
            body = "".join(md_lines)
        else:
            body = "".join(seg)

        body = body.strip("\n")
        if not body.strip():
            continue
        cell_type = "markdown" if is_md else "code"
        cells.append((cell_type, body + "\n"))

    return cells


def set_notebook_code(d: Path, code: str, *, preserve_markdown: bool = True) -> None:
    """Replace all code cells with cell(s) derived from *code*.

    Splits *code* on ``# %%`` percent-format delimiters into separate code
    (and optional markdown) cells, one per segment.  When no delimiters are
    present, the whole *code* becomes a single code cell (**backward-compatible**
    with scratch notebooks that use the ``write-code`` or ``push`` commands).

    Existing **non-code** cells (e.g. hand-edited narrative markdown) are
    preserved in their original position by default; only code cells are
    replaced. Set ``preserve_markdown=False`` when the source is the complete
    notebook source of truth and stale narrative cells must be removed.
    """
    nb = load_notebook(d)
    new_cells = split_code_into_cells(code)
    new_nb_cells = [
        new_markdown_cell(src) if ctype == "markdown" else new_code_cell(src)
        for ctype, src in new_cells
    ]

    if not preserve_markdown:
        nb.cells = new_nb_cells
        save_notebook(d, nb)
        return

    code_cells = [c for c in nb.cells if c.cell_type == "code"]

    if new_nb_cells:
        if code_cells:
            first = code_cells[0]
            idx = nb.cells.index(first)
            for c in code_cells:
                nb.cells.remove(c)
            for off, nc in enumerate(new_nb_cells):
                nb.cells.insert(idx + off, nc)
        else:
            nb.cells.extend(new_nb_cells)
    else:
        # code is empty → drop all code cells, leave other cells alone
        for c in code_cells:
            nb.cells.remove(c)

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
    oauth = KAGGLE_CONFIG_DIR / "credentials.json"
    has_legacy = creds.exists()
    has_oauth = oauth.exists()
    print(f"  kaggle.json       : {creds} ({'OK' if has_legacy else 'MISSING'})")
    print(f"  credentials.json  : {oauth} ({'OK' if has_oauth else 'MISSING'})")
    if has_legacy and has_oauth:
        print("  [WARN] Both kaggle.json (legacy) and credentials.json (OAuth) exist.")
        print("         Kaggle SDK v2+ prefers credentials.json. If API calls fail,")
        print(f"         move kaggle.json aside:  mv {creds} {creds}.bak")
    elif not has_legacy and not has_oauth:
        print("  -> get your token from https://www.kaggle.com/settings -> Create New Token")
        print(f"     save it as {creds}  (format: {{\"username\":\"...\",\"key\":\"...\"}})")
        print("     OR  run:  kaggle auth login  (creates credentials.json)")


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
        private=True,  # scratch notebooks stay private by default (safety)
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

    # Verify privacy — only force private when the field is missing/empty/None.
    # If the metadata explicitly sets is_private="false" (public), honor it.
    try:
        meta = read_metadata(d)
        priv = str(meta.get("is_private", "")).lower()
        if priv not in ("true", "false"):
            print("  [WARN] is_private missing/invalid; forcing private to avoid GitHub leak")
            meta["is_private"] = "true"
            write_metadata(d, meta)
        elif priv == "true":
            print("  is_private=true  (notebook stays private)")
        else:
            print("  is_private=false  (notebook will be PUBLIC — medals/lb eligible)")
    except FileNotFoundError:
        print("  [WARN] no kernel-metadata.json; creating a private one")
        write_metadata(d, default_metadata(args.slug, private=True))
        write_empty_notebook(d)

    rc = _run(["kaggle", "kernels", "push", "-p", str(d)], dry_run=args.dry_run, cwd=d)
    if rc == 0 and not args.dry_run:
        try:
            meta = read_metadata(d)
            kid = meta.get("id", args.slug)
        except Exception:
            kid = args.slug
            meta = {}
        print(f"  pushed: https://www.kaggle.com/code/{kid}")
        # If this notebook is attached to a competition, remind the user
        # that the score won't appear on the Code tab until they click
        # "Submit to Competition" from the kernel page.
        comp_sources = meta.get("competition_sources") or []
        if comp_sources:
            print()
            print(f"  [hint] el score NO aparecera bajo el notebook hasta que la submission")
            print(f"         se origine desde el. Abre https://www.kaggle.com/code/{kid}")
            print(f"         y pulsa 'Submit to Competition' para {', '.join(comp_sources)}")
            print(f"         (consume 1 de tu cupo diario).")
            print()
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
