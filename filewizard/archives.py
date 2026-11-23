"""Archives: extract zip/7z/tar/gz/rar, create zip/7z.

Extraction refuses entries that would escape the target folder ("zip slip"): a crafted
archive can't overwrite files elsewhere on your PC.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from .util import UserError, report, unique_dir, unique_path

ARCHIVE_EXTS = {".zip", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".tbz2", ".xz", ".txz", ".rar", ".cab", ".iso"}
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _stem(src: Path) -> str:
    name = src.name
    for double in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if name.lower().endswith(double):
            return name[: -len(double)]
    return src.stem


def _safe_target(root: Path, member: str) -> Path:
    target = (root / member).resolve()
    if not target.is_relative_to(root.resolve()):
        raise UserError(f"Refusing to extract '{member}': it would be written outside the folder.")
    return target


def _seven_zip() -> str | None:
    for p in (shutil.which("7z"), shutil.which("7za"),
              r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if p and Path(p).exists():
            return p
    return None


def is_encrypted(src: Path) -> bool:
    ext = src.suffix.lower()
    try:
        if ext == ".zip":
            with zipfile.ZipFile(src) as z:
                return any(i.flag_bits & 0x1 for i in z.infolist())
        if ext == ".7z":
            import py7zr

            with py7zr.SevenZipFile(src) as z:
                return z.needs_password()
    except Exception:
        return False
    return False


def extract(src: Path, here: bool = False, password: str | None = None) -> list[Path]:
    """Extract into a new folder named after the archive (or next to it if `here` and it has
    a single top-level item, so you don't get folder-in-folder)."""
    ext = src.suffix.lower()
    stage = unique_dir(src.with_name(_stem(src)))
    try:
        if ext == ".zip":
            with zipfile.ZipFile(src) as z:
                members = z.infolist()
                if password:
                    z.setpassword(password.encode("utf-8"))
                for i, m in enumerate(members):
                    _safe_target(stage, m.filename)
                    if m.flag_bits & 0x1 and not password:
                        raise UserError(f"{src.name} is password-protected.")
                    try:
                        z.extract(m, stage)
                    except RuntimeError as e:  # bad password
                        raise UserError(f"Wrong password for {src.name}.") from e
                    except NotImplementedError as e:  # AES zips need 7-Zip
                        raise UserError(f"{src.name} uses AES encryption; open it with 7-Zip.") from e
                    report((i + 1) / len(members), f"Extracting {m.filename}")
        elif ext == ".7z":
            try:
                import py7zr
            except ImportError as e:
                raise UserError("7z support needs 'py7zr' (pip install py7zr).") from e
            with py7zr.SevenZipFile(src, password=password) as z:
                if z.needs_password() and not password:
                    raise UserError(f"{src.name} is password-protected.")
                for name in z.getnames():
                    _safe_target(stage, name)
                report(None, f"Extracting {src.name}")
                try:
                    z.extractall(stage)
                except Exception as e:
                    if password:
                        raise UserError(f"Wrong password for {src.name}.") from e
                    raise
        elif tarfile.is_tarfile(src):
            with tarfile.open(src) as t:
                for m in t.getmembers():
                    target = _safe_target(stage, m.name)
                    if m.issym() or m.islnk():  # links must not point outside the folder either
                        link = m.linkname if m.islnk() else str(Path(m.name).parent / m.linkname)
                        if Path(m.linkname).is_absolute():
                            raise UserError(f"Refusing to extract link '{m.name}': it points outside the folder.")
                        _safe_target(stage, link)
                    if m.isdev():
                        raise UserError(f"Refusing to extract device file '{m.name}'.")
                    del target
                try:
                    t.extractall(stage, filter="data")  # also blocks device files, absolute links
                except TypeError:  # Python < 3.10.12/3.11.4: no filters; paths were checked above
                    t.extractall(stage)
        elif ext in (".gz", ".bz2", ".xz"):  # a single compressed file, not a tarball
            import bz2
            import gzip
            import lzma

            opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[ext]
            with opener(src, "rb") as fin, open(stage / src.stem, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        else:  # .rar/.cab/.iso: 7-Zip if installed, else Windows' built-in tar (libarchive)
            tool = _seven_zip()
            if tool:
                cmd = [tool, "x", "-y", f"-o{stage}", str(src)]
            else:
                tar = Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
                if not tar.exists():
                    raise UserError(f"Opening {ext} files needs 7-Zip (https://7-zip.org).")
                cmd = [str(tar), "-xf", str(src), "-C", str(stage)]
            report(None, f"Extracting {src.name}")
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
            if r.returncode != 0:
                raise UserError(f"Couldn't extract {src.name}: {(r.stderr or r.stdout).strip()[:400]}")
            for p in stage.rglob("*"):
                _safe_target(stage, str(p.relative_to(stage)))
    except UserError:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as e:
        shutil.rmtree(stage, ignore_errors=True)
        raise UserError(f"{src.name} looks damaged or isn't a supported archive ({e}).") from e

    items = list(stage.iterdir())
    if len(items) == 1 and items[0].is_dir() and items[0].name.lower() == stage.name.lower():
        # archive already wraps everything in a folder named like itself: drop one level
        inner = items[0].rename(stage / f".fw-unwrap-{os.getpid()}")
        for child in inner.iterdir():
            shutil.move(str(child), stage / child.name)
        inner.rmdir()
    elif here and len(items) == 1:
        dest = unique_path(src.parent / items[0].name)
        shutil.move(str(items[0]), dest)
        stage.rmdir()
        return [dest]
    return [stage]


def _walk(paths: list[Path]):
    """Yield (file_path, name_inside_archive) for files and folders, keeping folder structure."""
    for p in paths:
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    yield f, f.relative_to(p.parent).as_posix()
        else:
            yield p, p.name


def _archive_name(paths: list[Path], ext: str) -> Path:
    first = paths[0]
    name = first.name if first.is_dir() else first.stem
    if len(paths) > 1:
        name = first.parent.name or "Archive"
    return unique_path(first.parent / f"{name}{ext}")


def make_zip(paths: list[Path]) -> list[Path]:
    out = _archive_name(paths, ".zip")
    entries = list(_walk(paths))
    tmp = out.with_name(f".{out.name}.partial")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for i, (f, arc) in enumerate(entries):
            # already-compressed media gains nothing from deflate: store it (much faster)
            store = f.suffix.lower() in {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".mkv", ".mp3", ".zip",
                                         ".7z", ".gz", ".heic", ".webp", ".avif", ".m4a", ".docx", ".xlsx"}
            z.write(f, arc, compress_type=zipfile.ZIP_STORED if store else zipfile.ZIP_DEFLATED)
            report((i + 1) / max(len(entries), 1), f"Adding {f.name}")
    os.replace(tmp, out)
    return [out]


def make_7z(paths: list[Path]) -> list[Path]:
    try:
        import py7zr
    except ImportError as e:
        raise UserError("7z support needs 'py7zr' (pip install py7zr).") from e
    out = _archive_name(paths, ".7z")
    tmp = out.with_name(f".{out.name}.partial")
    entries = list(_walk(paths))
    with py7zr.SevenZipFile(tmp, "w") as z:
        for i, (f, arc) in enumerate(entries):
            z.write(f, arc)
            report((i + 1) / max(len(entries), 1), f"Adding {f.name}")
    os.replace(tmp, out)
    return [out]
