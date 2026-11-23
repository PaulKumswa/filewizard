"""Tools that work on any file: checksums, batch rename."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from .util import UserError, report


def checksums(files: list[Path]) -> tuple[list[Path], str]:
    """SHA-256 (and MD5, which some download pages still list) for each file."""
    lines = []
    total = sum(f.stat().st_size for f in files if f.is_file()) or 1
    done = 0
    for f in files:
        if not f.is_file():
            continue
        sha, md5 = hashlib.sha256(), hashlib.md5()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                sha.update(chunk)
                md5.update(chunk)
                done += len(chunk)
                report(done / total, f"Hashing {f.name}")
        lines.append(f"{f.name}\n  SHA-256: {sha.hexdigest()}\n  MD5:     {md5.hexdigest()}")
    return [], "\n\n".join(lines)


def taken_date(f: Path) -> datetime | None:
    """When a photo was taken (EXIF DateTimeOriginal), if it has one."""
    try:
        from PIL import Image

        from .images import IMAGE_EXTS, _register_heif

        if f.suffix.lower() not in IMAGE_EXTS:
            return None
        if f.suffix.lower() in (".heic", ".heif", ".avif"):
            _register_heif()
        with Image.open(f) as im:
            exif = im.getexif()
            raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)
        return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S") if raw else None
    except Exception:
        return None


def _expand(pattern: str, f: Path, n: int, width: int) -> str:
    stat = f.stat()
    date = datetime.fromtimestamp(stat.st_mtime)

    def sub(m: re.Match) -> str:
        key, fmt = m.group(1), m.group(2)
        if key == "n":
            digits = int(fmt) if fmt and fmt.isdigit() else width
            return f"{n:0{digits}d}"
        if key == "name":
            return f.stem
        if key == "date":
            return date.strftime(fmt or "%Y-%m-%d")
        if key == "taken":
            return (taken_date(f) or date).strftime(fmt or "%Y-%m-%d")
        if key == "ext":
            return f.suffix.lstrip(".")
        return m.group(0)

    return re.sub(r"\{(n|name|date|taken|ext)(?::([^}]*))?\}", sub, pattern)


def plan_rename(files: list[Path], pattern: str) -> list[tuple[Path, Path]]:
    """'Trip {n}' -> Trip 1.jpg, Trip 2.jpg ... ; tokens: {n} {n:3} {name} {date} {date:%Y%m%d} {taken} {ext}.
    A pattern without {n} gets ' {n}' appended so names stay unique."""
    pattern = pattern.strip()
    if not pattern:
        raise UserError("Give a name pattern, e.g.  Vacation {n}")
    if re.search(r'[<>:"/\\|?*]', re.sub(r"\{[^}]*\}", "", pattern)):
        raise UserError('File names can\'t contain < > : " / \\ | ? *')
    if "{n" not in pattern and len(files) > 1:
        pattern += " {n}"
    width = max(2, len(str(len(files))))
    plan = []
    for i, f in enumerate(files, 1):
        new = f.with_name(_expand(pattern, f, i, width) + f.suffix)
        plan.append((f, new))
    targets = [n for _, n in plan]
    if len({t.name.lower() for t in targets}) != len(targets):
        raise UserError("That pattern gives several files the same name. Add {n}.")
    sources = {f.resolve() for f in files}
    for _, t in plan:
        if t.exists() and t.resolve() not in sources:
            raise UserError(f"'{t.name}' already exists; nothing was renamed.")
    return plan


def rename(files: list[Path], pattern: str) -> list[Path]:
    plan = plan_rename(files, pattern)
    # two phases via temporary names so swaps (a->b, b->a) can't collide
    temps = []
    for i, (src, _) in enumerate(plan):
        tmp = src.with_name(f".fw-rename-{i}-{src.name}")
        src.rename(tmp)
        temps.append(tmp)
    out = []
    for tmp, (_, dst) in zip(temps, plan):
        tmp.rename(dst)
        out.append(dst)
    return out


def details(files: list[Path]) -> tuple[list[Path], str]:
    """'Show details': the useful facts about each file in one copyable window."""
    from .util import human_size

    blocks = []
    for f in files:
        st = f.stat()
        lines = [f.name, f"  Size: {human_size(st.st_size) if f.is_file() else '(folder)'}",
                 f"  Modified: {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}"]
        ext = f.suffix.lower()
        extra = ""
        try:
            from .images import IMAGE_EXTS, describe
            from .media import AUDIO_EXTS, VIDEO_EXTS

            if ext in IMAGE_EXTS:
                extra = describe(f)
            elif ext in VIDEO_EXTS | AUDIO_EXTS:
                extra = _media_details(f)
            elif ext in (".pdf", ".epub", ".xps", ".mobi"):
                extra = _pdf_details(f)
        except Exception as e:  # details are best-effort
            extra = f"(couldn't read details: {e})"
        lines += ["  " + ln for ln in extra.splitlines() if ln]
        blocks.append("\n".join(lines))
    return [], "\n\n".join(blocks)


def _media_details(f: Path) -> str:
    from .media import probe

    i = probe(f)
    out = [f"Duration: {int(i.duration // 3600):d}:{int(i.duration % 3600 // 60):02d}:{i.duration % 60:05.2f}"]
    if i.vcodec:
        out.append(f"Video: {i.width} × {i.height}, {i.vcodec}" + (f", {i.fps:g} fps" if i.fps else ""))
    if i.acodec:
        out.append(f"Audio: {i.acodec}")
    if i.duration:
        out.append(f"Average bitrate: {f.stat().st_size * 8 / i.duration / 1000:.0f} kbps")
    return "\n".join(out)


def _pdf_details(f: Path) -> str:
    import pymupdf

    d = pymupdf.open(f)
    try:
        out = [f"Pages: {d.page_count}"]
        if d.needs_pass:
            out.append("Password-protected")
            return "\n".join(out)
        if d.page_count:
            r = d[0].rect
            out.append(f"Page size: {r.width / 72:.2f} × {r.height / 72:.2f} in")
        meta = {k: v for k, v in (d.metadata or {}).items() if v and k in ("title", "author", "creator", "producer")}
        out += [f"{k.title()}: {v}" for k, v in meta.items()]
        if d.is_pdf:
            has_text = any(p.get_text().strip() for p in list(d)[:5])
            out.append("Text: selectable" if has_text else "Text: none (scanned? try 'Make searchable')")
            if d.metadata.get("encryption"):
                out.append(f"Encryption: {d.metadata['encryption']}")
        return "\n".join(out)
    finally:
        d.close()
