"""Office documents: Word/PowerPoint/Excel → PDF, legacy formats → modern ones.

Strategy, best fidelity first:
  1. Microsoft Office via COM (Windows, Office installed) — exactly what "Save as PDF" in Word does.
  2. LibreOffice headless (any OS) — free, very good, occasionally imperfect layout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .util import UserError, sibling

WORD_EXTS = {".doc", ".docx", ".docm", ".rtf", ".odt", ".txt"}
SLIDE_EXTS = {".ppt", ".pptx", ".pptm", ".odp"}
SHEET_EXTS = {".xls", ".xlsx", ".xlsm", ".ods"}

# Office SaveAs format codes
_WD_PDF, _WD_DOCX = 17, 16
_PP_PDF, _PP_PPTX = 32, 24
_XL_XLSX = 51


def _com_available() -> bool:
    if sys.platform != "win32" or os.environ.get("FILEWIZARD_NO_MSOFFICE") == "1":
        return False
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return True


def _com_app(progid: str):
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        app = win32com.client.DispatchEx(progid)  # a fresh instance; never hijack the user's open Word
    except Exception:
        return None
    return app


def _via_msoffice(src: Path, out: Path, target: str) -> bool:
    """Return True on success, False if the needed Office app isn't installed."""
    ext = src.suffix.lower()
    s, o = str(src.resolve()), str(out.resolve())
    if ext in WORD_EXTS:
        app = _com_app("Word.Application")
        if app is None:
            return False
        try:
            app.Visible = False
            app.DisplayAlerts = 0
            # A dummy password makes protected files fail fast instead of waiting on an invisible prompt.
            doc = app.Documents.Open(s, ReadOnly=True, AddToRecentFiles=False, ConfirmConversions=False,
                                     PasswordDocument="fw-no-password", Visible=False)
            try:
                doc.SaveAs2(o, FileFormat=_WD_PDF if target == ".pdf" else _WD_DOCX)
            finally:
                doc.Close(False)
        finally:
            app.Quit()
        return True
    if ext in SLIDE_EXTS:
        app = _com_app("PowerPoint.Application")
        if app is None:
            return False
        try:
            pres = app.Presentations.Open(s, ReadOnly=True, Untitled=False, WithWindow=False)
            try:
                pres.SaveAs(o, _PP_PDF if target == ".pdf" else _PP_PPTX)
            finally:
                pres.Close()
        finally:
            app.Quit()
        return True
    if ext in SHEET_EXTS:
        app = _com_app("Excel.Application")
        if app is None:
            return False
        try:
            app.Visible = False
            app.DisplayAlerts = False
            app.AskToUpdateLinks = False
            wb = app.Workbooks.Open(s, UpdateLinks=0, ReadOnly=True, AddToMru=False, Password="fw-no-password")
            try:
                if target == ".pdf":
                    wb.ExportAsFixedFormat(0, o)  # 0 = xlTypePDF, whole workbook
                else:
                    wb.SaveAs(o, FileFormat=_XL_XLSX)
            finally:
                wb.Close(False)
        finally:
            app.Quit()
        return True
    return False


def find_soffice() -> str | None:
    env = os.environ.get("FILEWIZARD_SOFFICE")
    if env and Path(env).exists():
        return env
    for name in ("soffice", "soffice.exe", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            p = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if p.exists():
                return str(p)
    return None


def _via_libreoffice(src: Path, out: Path, target: str) -> bool:
    soffice = find_soffice()
    if not soffice:
        return False
    with tempfile.TemporaryDirectory(prefix="filewizard-") as tmp:
        # A private profile dir lets this run even while the user has LibreOffice open.
        profile = Path(tmp, "profile").as_uri()
        cmd = [soffice, f"-env:UserInstallation={profile}", "--headless", "--norestore",
               "--convert-to", target[1:], "--outdir", tmp, str(src)]
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, creationflags=flags)
        produced = Path(tmp, src.stem + target)
        if r.returncode != 0 or not produced.exists():
            raise UserError(f"LibreOffice couldn't convert {src.name}.\n{(r.stderr or r.stdout).strip()[:500]}")
        shutil.move(str(produced), out)
    return True


def convert(src: Path, target: str, out: Path | None = None) -> list[Path]:
    out = out or sibling(src, None, target)
    if _com_available():
        try:
            if _via_msoffice(src, out, target):
                return [out]
        except Exception as e:  # Office present but choked (corrupt file, dialog...) -> try LibreOffice
            if out.exists():
                out.unlink()
            if "password" in str(e).lower():
                raise UserError(f"{src.name} is password-protected. Open it, remove the password "
                                "(File → Info → Protect), save, and try again.") from e
            if not find_soffice():
                raise UserError(f"Microsoft Office couldn't convert {src.name}: {e}") from e
    if _via_libreoffice(src, out, target):
        return [out]
    raise UserError(
        "Converting Office documents needs Microsoft Office or the free LibreOffice "
        "(https://www.libreoffice.org). Neither was found."
    )


def backend_report() -> str:
    parts = []
    parts.append("Microsoft Office (COM): " + ("available" if _com_available() else "not available"))
    parts.append("LibreOffice: " + (find_soffice() or "not found"))
    return "\n".join(parts)


def to_images(src: Path, ext: str = ".png") -> list[Path]:
    """Slides/pages as images (e.g. a PowerPoint deck -> one PNG per slide)."""
    import shutil
    import tempfile

    from . import pdf

    with tempfile.TemporaryDirectory(prefix="filewizard-") as td:
        tmp_src = Path(td) / src.name
        shutil.copyfile(src, tmp_src)  # keeps the temporary PDF out of the user's folder
        tmp_pdf = Path(td) / (src.stem + ".pdf")
        convert(tmp_src, ".pdf", tmp_pdf)
        images = pdf.to_images(tmp_pdf, ext)
        # move results next to the original
        from .util import sibling, unique_dir

        if len(images) == 1:
            out = sibling(src, None, ext)
            shutil.move(str(images[0]), out)
            return [out]
        folder = unique_dir(src.with_name(f"{src.stem} ({ext[1:]})"))
        outs = []
        for i, img in enumerate(images, 1):
            dest = folder / f"{src.stem} - {'slide' if src.suffix.lower() in SLIDE_EXTS else 'page'} {i:02d}{ext}"
            shutil.move(str(img), dest)
            outs.append(dest)
        return outs
