"""Turn Explorer's one-process-per-selected-file into one job.

When you select 5 PDFs and click "Merge", Explorer starts FileWizard 5 times, each with a
single path. Every process drops its path into a queue folder; the first one to grab the
leader lock waits until no new paths arrive for a moment, then handles them all. The
others exit immediately.
"""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from pathlib import Path

SETTLE_SECONDS = 1.2   # quiet period with no new arrivals before the leader starts
MAX_WAIT_SECONDS = 15  # cap on gathering, for huge selections
STALE_LOCK_SECONDS = 120


def _queue_dir(op_id: str) -> Path:
    d = Path(tempfile.gettempdir()) / "filewizard-queue" / op_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _enqueue(q: Path, path: str) -> None:
    name = f"{time.time_ns()}-{uuid.uuid4().hex}"
    tmp = q / (name + ".tmp")
    tmp.write_text(path, encoding="utf-8")
    os.replace(tmp, q / (name + ".item"))  # atomic: the leader never reads half a path


def _items(q: Path) -> list[Path]:
    return sorted(q.glob("*.item"))


def _try_lead(lock: Path) -> bool:
    try:
        lock.mkdir()  # atomic on every OS/filesystem we care about
        return True
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime > STALE_LOCK_SECONDS:  # leader crashed
                lock.rmdir()
                lock.mkdir()
                return True
        except OSError:
            pass
        return False


def collect(op_id: str, path: str, settle: float = SETTLE_SECONDS) -> list[str] | None:
    """Return every path selected for this op if we're the leader, else None."""
    q = _queue_dir(op_id)
    _enqueue(q, path)
    lock = q / "leader.lock"
    if not _try_lead(lock):
        return None

    gathered: list[str] = []
    while True:
        start = last_change = time.monotonic()
        count = len(_items(q))
        while True:
            time.sleep(0.1)
            n = len(_items(q))
            now = time.monotonic()
            if n != count:
                count, last_change = n, now
            if now - last_change >= settle or now - start >= MAX_WAIT_SECONDS:
                break
        for item in _items(q):
            try:
                gathered.append(item.read_text(encoding="utf-8"))
                item.unlink()
            except OSError:
                pass
        lock.rmdir()
        # A straggler may have queued after our last scan and then failed to get the lock.
        if _items(q) and _try_lead(lock):
            continue
        break
    # Explorer can pass the same file twice (e.g. via a shortcut); keep first occurrence.
    seen: set[str] = set()
    return [p for p in gathered if not (p in seen or seen.add(p))]
