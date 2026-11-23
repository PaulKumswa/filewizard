"""Command line entry point.

    filewizard list                          show every action and the file types it applies to
    filewizard run pdf-merge a.pdf b.pdf     run an action directly
    filewizard run pdf-extract x.pdf -p pages=1-3
    filewizard install | uninstall           add/remove the Explorer right-click menu (Windows)
    filewizard export-reg menu.reg           write the registry entries to a file to inspect
    filewizard menu .jpg                     preview the right-click menu for a file type
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
    if args.list:  # the native menu hands over big selections as a UTF-8 list file
        lst = Path(args.list)
        try:
            paths += [ln for ln in lst.read_text(encoding="utf-8").splitlines() if ln.strip()]
        finally:
            try:
                lst.unlink()
            except OSError:
                pass
    if not paths:
        print("No files given.", file=sys.stderr)
        return 2
    args.shell = args.shell or args.native  # same UI behaviour (dialogs, toast)
    if args.shell and not args.native:
        from .collector import collect

        # One Explorer click = one process per selected file; only the leader continues.
        gathered = collect(op.id, paths[0]) if len(paths) == 1 else paths
        if gathered is None:
            return 0
        paths = gathered
    files = [Path(p) for p in paths]
    log.info("run %s on %d file(s): %s", op.id, len(files), [f.name for f in files])
    if op.id == "palette":
        from .operations import for_selection

        choices = for_selection(files)
        chosen = ui.palette(files, choices) if choices else None
        if not chosen:
            return 1
        op = BY_ID[chosen]
        log.info("palette -> %s", op.id)

    from .util import PROGRESS, Cancelled

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
        outputs, errors, messages = ui.run_with_progress(
            op.label.rstrip("…"), lambda: execute(op, files, params))
    except Cancelled:
        log.info("cancelled during run")
        return 1
    except UserError as e:
        log.info("user error: %s", e)
        ui.message(str(e), error=True) if args.shell else print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        log.error("crash:\n%s", traceback.format_exc())
        text = f"Something went wrong: {type(e).__name__}: {e}\n\nDetails are in the FileWizard log."
        ui.message(text, error=True) if args.shell else print(text, file=sys.stderr)
        return 1
    finally:
        PROGRESS.cancelled = False

    for o in outputs:
        log.info("wrote %s", o)
        if not args.shell:
            print(o)
    for m in messages:
        if args.shell:
            ui.show_text(op.label, m)
        else:
            print(m)
    if errors:
        log.info("errors: %s", errors)
        text = "\n\n".join(errors)
        if outputs:
            text = f"{len(outputs)} file(s) done, but some failed:\n\n" + text
        ui.message(text, error=True) if args.shell else print(text, file=sys.stderr)
        return 1
    if args.shell:
        ui.notify_done(outputs)
    return 0


def cmd_list(args) -> int:
    from .operations import OPERATIONS

    group = None
    for op in OPERATIONS:
        if op.group != group:
            group = op.group
            print(f"\n[{group}]")
        exts = " ".join(sorted(e for e in op.exts if e not in op.exclude_exts))
        params = (" [" + ", ".join(f"-p {n}=" for n in op.param_names) + "]") if op.param_names else ""
        exts = exts if len(exts) < 70 else exts[:67] + "..."
        print(f"  {op.id:<22} {op.full_label:<44} {exts}{params}")
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


def doctor_text() -> str:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_doctor(None)
    return buf.getvalue()


def cmd_settings(args) -> int:
    from . import ui
    from .shell_menu import install, uninstall
    from .util import app_dir

    ui.settings_window(doctor_text, install, lambda: uninstall(quiet=True), app_dir() / "filewizard.log")
    return 0


def cmd_doctor(args) -> int:
    from .media import ffmpeg
    from .office import backend_report
    from .pdf import tessdata

    def check(mod, label, hint=None):
        try:
            __import__(mod)
            print(f"  [ok] {label}")
        except ImportError:
            print(f"  [--] {label} ({hint or 'pip install ' + mod.replace('_', '-')})")

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
    check("rawpy", "rawpy (camera RAW photos)")
    check("py7zr", "py7zr (7z archives)")
    check("openpyxl", "openpyxl (Excel)")
    check("rembg", "rembg (AI background removal, optional)", "pip install rembg")
    try:
        print(f"  [ok] ffmpeg (video/audio): {ffmpeg()}")
    except Exception:
        print("  [--] ffmpeg (video/audio) (pip install imageio-ffmpeg)")
    td = tessdata()
    print(f"  [{'ok' if td else '--'}] Tesseract OCR" + (f": {td}" if td else
          " (optional: https://github.com/UB-Mannheim/tesseract/wiki)"))
    print(backend_report())
    return 0


def _safe_console() -> None:
    """Windows consoles often use cp1252; never crash on a filename or symbol it can't show."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    ap = argparse.ArgumentParser(prog="filewizard", description="Right-click file conversions and PDF tools.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run an action on files")
    r.add_argument("--shell", action="store_true", help=argparse.SUPPRESS)  # launched by a classic verb
    r.add_argument("--native", action="store_true", help=argparse.SUPPRESS)  # launched by the native menu
    r.add_argument("--list", help=argparse.SUPPRESS)
    r.add_argument("-p", "--param", action="append", default=[], metavar="KEY=VALUE")
    r.add_argument("op")
    r.add_argument("files", nargs="*")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("list", help="list actions").set_defaults(fn=cmd_list)
    m = sub.add_parser("menu", help="preview the right-click menu for a file type, e.g. `menu .jpg`")
    m.add_argument("ext")
    m.set_defaults(fn=lambda a: print(__import__("filewizard.shell_menu", fromlist=["x"]).render_menu(a.ext)) or 0)
    sub.add_parser("doctor", help="check optional dependencies").set_defaults(fn=cmd_doctor)
    sub.add_parser("settings", help="open the settings window").set_defaults(fn=cmd_settings)
    e = sub.add_parser("export-reg", help="write the menu's registry entries to a .reg file")
    e.add_argument("out", nargs="?")
    e.set_defaults(fn=cmd_export_reg)

    def _install(a):
        from .shell_menu import install
        install("classic" if a.classic else "auto")
        return 0

    def _uninstall(_):
        from .shell_menu import uninstall
        uninstall()
        return 0

    ins = sub.add_parser("install", help="add the right-click menu (Windows)")
    ins.add_argument("--classic", action="store_true", help="registry-only menu (no shell extension DLL)")
    ins.set_defaults(fn=_install)
    sub.add_parser("uninstall", help="remove the right-click menu (Windows)").set_defaults(fn=_uninstall)

    args = ap.parse_args(argv)
    return args.fn(args)
