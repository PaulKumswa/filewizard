"""Small helpers shared by every operation."""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

APP_NAME = "FileWizard"


def app_dir() -> Path:
    """Per-user folder for logs, the icon and batch queues."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging() -> logging.Logger:
    log = logging.getLogger("filewizard")
    if not log.handlers:
        try:
            h = logging.FileHandler(app_dir() / "filewizard.log", encoding="utf-8")
        except OSError:
            h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(asctime)s [%(process)d] %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log


class UserError(Exception):
    """An error whose message is fit to show the user as-is."""


def unique_path(path: Path) -> Path:
    """Return `path`, or `name (2).ext`, `name (3).ext`... so nothing is overwritten."""
    path = Path(path)
    if not path.exists():
        return path
    i = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def unique_dir(path: Path) -> Path:
    d = unique_path(path)
    d.mkdir(parents=True)
    return d


def sibling(src: Path, suffix_label: str | None, ext: str) -> Path:
    """Output path next to `src`: `report (merged).pdf`, or `photo.png` with no label."""
    stem = src.stem if not suffix_label else f"{src.stem} ({suffix_label})"
    return unique_path(src.with_name(stem + ext))


def natural_key(p: Path | str):
    """Sort 'page2' before 'page10'."""
    s = Path(p).name.lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def parse_page_ranges(spec: str, page_count: int) -> list[int]:
    """Parse '1-3, 5, 8-' (1-based, inclusive) into 0-based page indexes, in order given.

    '-3' means 1-3, '8-' means 8 to the end, 'end'/'last' means the last page.
    """
    if not spec or not spec.strip():
        raise UserError("No pages given.")
    pages: list[int] = []
    for raw in re.split(r"[,;\s]+", spec.strip()):
        if not raw:
            continue
        part = raw.lower().replace("last", str(page_count)).replace("end", str(page_count))
        m = re.fullmatch(r"(\d*)\s*-\s*(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else page_count
        elif part.isdigit():
            a = b = int(part)
        else:
            raise UserError(f"Couldn't understand page range '{raw}'. Use something like 1-3,5,8-")
        if a < 1 or b > page_count or a > b:
            raise UserError(f"Range '{raw}' is outside this document (it has {page_count} pages).")
        pages.extend(range(a - 1, b))
    return pages


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"
