"""Command line entry point.

    filewizard list                          show every action and the file types it applies to
    filewizard run pdf-merge a.pdf b.pdf     run an action directly
    filewizard run pdf-extract x.pdf -p pages=1-3
    filewizard install | uninstall           add/remove the Explorer right-click menu (Windows)
    filewizard export-reg menu.reg           write the registry entries to a file to inspect
    filewizard doctor                        check which optional pieces are available
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .util import UserError, setup_logging


def _parse_params(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"Bad -p value '{p}', expected key=value")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def cmd_run(args) -> int:
    from . import ui
    from .operations import BY_ID, execute

    log = setup_logging()
    op = BY_ID.get(args.op)
    if op is None:
        msg = f"Unknown action '{args.op}'. Run `filewizard list`."
        ui.message(msg, error=True) if args.shell else print(msg, file=sys.stderr)
        return 2

    paths = list(args.files)
    if args.shell:
        from .collector import collect

        # One Explorer click = one process per selected file; only the leader continues.
        gathered = collect(op.id, paths[0]) if len(paths) == 1 else paths
        if gathered is None:
            return 0
        paths = gathered
    files = [Path(p) for p in paths]
    log.info("run %s on %d file(s): %s", op.id, len(files), [f.name for f in files])

    try:
        missing = [f for f in files if not f.exists()]
        if missing:
            raise UserError("File not found: " + ", ".join(map(str, missing)))
        params = _parse_params(args.param)
        if op.ask and not (op.param_names and all(n in params for n in op.param_names)):
            if not ui.interactive() and op.param_names:
                raise UserError(f"'{op.id}' needs: " + ", ".join(f"-p {n}=…" for n in op.param_names))
            asked = op.ask(files)
            if asked is None:
                log.info("cancelled by user")
                return 1
            params = {**asked, **params}
        outputs, errors = ui.run_with_progress(op.label.rstrip("…"), lambda: execute(op, files, params))
    except UserError as e:
        log.info("user error: %s", e)
        ui.message(str(e), error=True) if args.shell else print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        log.error("crash:\n%s", traceback.format_exc())
        text = f"Something went wrong: {type(e).__name__}: {e}\n\nDetails are in the FileWizard log."
        ui.message(text, error=True) if args.shell else print(text, file=sys.stderr)
        return 1

    for o in outputs:
        log.info("wrote %s", o)
        if not args.shell:
            print(o)
    if errors:
        log.info("errors: %s", errors)
        text = "\n\n".join(errors)
        if outputs:
            text = f"{len(outputs)} file(s) done, but some failed:\n\n" + text
        ui.message(text, error=True) if args.shell else print(text, file=sys.stderr)
        return 1
    return 0


def cmd_list(args) -> int:
    from .operations import OPERATIONS

    group = None
    for op in OPERATIONS:
        if op.group != group:
            group = op.group
            print(f"\n{group}")
        exts = " ".join(sorted(e for e in op.exts if e not in op.exclude_exts))
        params = (" [" + ", ".join(f"-p {n}=" for n in op.param_names) + "]") if op.param_names else ""
        print(f"  {op.id:<20} {op.label:<32} {exts}{params}")
    return 0


def cmd_export_reg(args) -> int:
    from .shell_menu import build_entries, command_prefix, to_reg_file
    from .util import app_dir

    text = to_reg_file(build_entries(command_prefix(app_dir() / "launch.pyw"), None))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-16")  # what regedit expects
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_doctor(args) -> int:
    from .office import backend_report

    def check(mod, label):
        try:
            __import__(mod)
            print(f"  [ok] {label}")
        except ImportError:
            print(f"  [--] {label} (pip install {mod.replace('_', '-')})")

    print(f"Python {sys.version.split()[0]} at {sys.executable}")
    check("pymupdf", "PyMuPDF (PDF tools)")
    check("PIL", "Pillow (images)")
    check("pillow_heif", "pillow-heif (iPhone HEIC photos)")
    check("pdf2docx", "pdf2docx (PDF → Word)")
    if sys.platform == "win32":
        check("win32com", "pywin32 (drive Microsoft Office)")
    try:
        import tkinter  # noqa: F401
        print("  [ok] tkinter (prompts)")
    except ImportError:
        print("  [--] tkinter (prompts) — reinstall Python with the Tcl/Tk option")
    print(backend_report())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="filewizard", description="Right-click file conversions and PDF tools.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run an action on files")
    r.add_argument("--shell", action="store_true", help=argparse.SUPPRESS)  # launched from Explorer
    r.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VALUE")
    r.add_argument("op")
    r.add_argument("files", nargs="+")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("list", help="list actions").set_defaults(fn=cmd_list)
    sub.add_parser("doctor", help="check optional dependencies").set_defaults(fn=cmd_doctor)
    e = sub.add_parser("export-reg", help="write the menu's registry entries to a .reg file")
    e.add_argument("out", nargs="?")
    e.set_defaults(fn=cmd_export_reg)

    def _install(_):
        from .shell_menu import install
        install()
        return 0

    def _uninstall(_):
        from .shell_menu import uninstall
        uninstall()
        return 0

    sub.add_parser("install", help="add the right-click menu (Windows)").set_defaults(fn=_install)
    sub.add_parser("uninstall", help="remove the right-click menu (Windows)").set_defaults(fn=_uninstall)

    args = ap.parse_args(argv)
    return args.fn(args)
