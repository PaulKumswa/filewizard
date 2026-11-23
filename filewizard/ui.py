"""User interaction: prompts, message boxes, progress, file ordering.

Everything degrades gracefully: with FILEWIZARD_NONINTERACTIVE=1 (tests, scripts) or
no display, prompts fail loudly instead of hanging and messages go to stderr.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Callable, TypeVar

from .util import APP_NAME

T = TypeVar("T")


def interactive() -> bool:
    return os.environ.get("FILEWIZARD_NONINTERACTIVE") != "1"


def _tk_root():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # don't hide behind Explorer
    return root


def message(text: str, error: bool = False) -> None:
    if not interactive():
        print(("ERROR: " if error else "") + text, file=sys.stderr)
        return
    if sys.platform == "win32":
        import ctypes

        MB_ICONERROR, MB_ICONINFO, MB_TOPMOST, MB_SETFOREGROUND = 0x10, 0x40, 0x40000, 0x10000
        flags = (MB_ICONERROR if error else MB_ICONINFO) | MB_TOPMOST | MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags)
        return
    try:
        from tkinter import messagebox

        root = _tk_root()
        (messagebox.showerror if error else messagebox.showinfo)(APP_NAME, text, parent=root)
        root.destroy()
    except Exception:
        print(text, file=sys.stderr)


def ask_string(prompt: str, initial: str = "", secret: bool = False) -> str | None:
    """Return the typed text, or None if the user cancelled."""
    if not interactive():
        raise RuntimeError(f"Input needed but running non-interactively: {prompt}")
    from tkinter import simpledialog

    root = _tk_root()
    try:
        return simpledialog.askstring(
            APP_NAME, prompt, initialvalue=initial, show="•" if secret else None, parent=root
        )
    finally:
        root.destroy()


def ask_new_password() -> str | None:
    pw = ask_string("Choose a password for the PDF:", secret=True)
    if pw is None:
        return None
    if not pw:
        message("Password can't be empty.", error=True)
        return None
    again = ask_string("Type the password again:", secret=True)
    if again is None:
        return None
    if again != pw:
        message("Passwords didn't match. Nothing was changed.", error=True)
        return None
    return pw


def order_files(files: list[Path], title: str) -> list[Path] | None:
    """Let the user reorder/remove files (e.g. before merging). None = cancelled."""
    if not interactive() or len(files) < 2:
        return files
    import tkinter as tk

    root = tk.Tk()
    root.title(f"{APP_NAME} — {title}")
    root.attributes("-topmost", True)
    items = list(files)
    result: dict[str, list[Path] | None] = {"v": None}

    tk.Label(root, text="The top file goes first. Select a file and use the buttons to reorder.").pack(
        padx=10, pady=(10, 4), anchor="w"
    )
    body = tk.Frame(root)
    body.pack(fill="both", expand=True, padx=10)
    lb = tk.Listbox(body, width=70, height=min(max(len(items), 5), 20), selectmode="browse")
    lb.pack(side="left", fill="both", expand=True)

    def refresh(sel: int | None = None):
        lb.delete(0, "end")
        for i, p in enumerate(items, 1):
            lb.insert("end", f"{i:>3}.  {p.name}")
        if sel is not None and items:
            sel = max(0, min(sel, len(items) - 1))
            lb.selection_set(sel)
            lb.see(sel)

    def move(delta: int):
        cur = lb.curselection()
        if not cur:
            return
        i = cur[0]
        j = i + delta
        if 0 <= j < len(items):
            items[i], items[j] = items[j], items[i]
            refresh(j)

    def remove():
        cur = lb.curselection()
        if cur:
            items.pop(cur[0])
            refresh(cur[0])

    def reverse():
        items.reverse()
        refresh(0)

    btns = tk.Frame(body)
    btns.pack(side="left", fill="y", padx=(8, 0))
    for text, cmd in (("▲ Up", lambda: move(-1)), ("▼ Down", lambda: move(1)),
                      ("Remove", remove), ("Reverse", reverse)):
        tk.Button(btns, text=text, width=10, command=cmd).pack(pady=2)

    def ok():
        result["v"] = list(items)
        root.destroy()

    foot = tk.Frame(root)
    foot.pack(fill="x", padx=10, pady=10)
    tk.Button(foot, text="Cancel", width=10, command=root.destroy).pack(side="right")
    tk.Button(foot, text="OK", width=10, command=ok, default="active").pack(side="right", padx=6)
    root.bind("<Return>", lambda e: ok())
    root.bind("<Escape>", lambda e: root.destroy())
    refresh(0)
    root.mainloop()
    return result["v"] if result["v"] else None


def run_with_progress(title: str, work: Callable[[], T]) -> T:
    """Run `work` in a thread; show a small 'Working…' window if it takes > 1s."""
    if not interactive():
        return work()
    try:
        import tkinter as tk
    except ImportError:
        return work()

    box: dict = {}

    def target():
        try:
            box["result"] = work()
        except BaseException as e:  # re-raised in the caller's thread
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(1.0)
    if t.is_alive():
        try:
            root = tk.Tk()
        except Exception:  # no display
            t.join()
        else:
            root.title(APP_NAME)
            root.attributes("-topmost", True)
            root.resizable(False, False)
            tk.Label(root, text=f"{title}…", padx=30, pady=20).pack()
            root.protocol("WM_DELETE_WINDOW", lambda: None)  # can't safely cancel mid-write

            def poll():
                if t.is_alive():
                    root.after(150, poll)
                else:
                    root.destroy()

            poll()
            root.mainloop()
            t.join()
    if "error" in box:
        raise box["error"]
    return box["result"]
