"""User interaction: prompts, message boxes, progress, notifications, file ordering.

Everything degrades gracefully: with FILEWIZARD_NONINTERACTIVE=1 (tests, scripts) or
no display, prompts fail loudly instead of hanging and messages go to the log/stderr.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable, TypeVar

from .util import APP_NAME, PROGRESS

T = TypeVar("T")
log = logging.getLogger("filewizard")

ACCENT = "#3462dc"


def interactive() -> bool:
    return os.environ.get("FILEWIZARD_NONINTERACTIVE") != "1"


def _stderr(text: str) -> None:
    if sys.stderr is not None:  # pythonw has no console
        print(text, file=sys.stderr)
    log.info("message: %s", text)


def _dpi_aware() -> None:
    """Crisp (not blurry) dialogs on high-DPI laptop screens."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def _root(title: str = APP_NAME, hidden: bool = True):
    import tkinter as tk

    _dpi_aware()
    root = tk.Tk()
    root.title(title)
    if hidden:
        root.withdraw()
    root.attributes("-topmost", True)  # don't hide behind Explorer
    try:
        from .util import app_dir

        ico = app_dir() / "filewizard.ico"
        if ico.exists() and sys.platform == "win32":
            root.iconbitmap(str(ico))
    except Exception:
        pass
    return root


def _center(win, w: int | None = None, h: int | None = None):
    win.update_idletasks()
    w = w or win.winfo_reqwidth()
    h = h or win.winfo_reqheight()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"+{x}+{y}")


# ---------- simple dialogs ----------

def message(text: str, error: bool = False) -> None:
    if not interactive():
        _stderr(("ERROR: " if error else "") + text)
        return
    if sys.platform == "win32":
        import ctypes

        MB_ICONERROR, MB_ICONINFO, MB_TOPMOST, MB_SETFOREGROUND = 0x10, 0x40, 0x40000, 0x10000
        flags = (MB_ICONERROR if error else MB_ICONINFO) | MB_TOPMOST | MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags)
        return
    try:
        from tkinter import messagebox

        root = _root()
        (messagebox.showerror if error else messagebox.showinfo)(APP_NAME, text, parent=root)
        root.destroy()
    except Exception:
        _stderr(text)


def confirm(text: str) -> bool:
    if not interactive():
        return True
    from tkinter import messagebox

    root = _root()
    try:
        return bool(messagebox.askokcancel(APP_NAME, text, parent=root))
    finally:
        root.destroy()


def ask_string(prompt: str, initial: str = "", secret: bool = False) -> str | None:
    """Return the typed text, or None if the user cancelled."""
    if not interactive():
        raise RuntimeError(f"Input needed but running non-interactively: {prompt}")
    import tkinter as tk
    from tkinter import ttk

    root = _root(hidden=False)
    root.resizable(False, False)
    result: dict[str, str | None] = {"v": None}
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=prompt, justify="left", wraplength=460).pack(anchor="w")
    var = tk.StringVar(value=initial)
    entry = ttk.Entry(frm, textvariable=var, width=50, show="•" if secret else "")
    entry.pack(fill="x", pady=(10, 12))
    entry.focus_set()
    entry.select_range(0, "end")

    def ok(_=None):
        result["v"] = var.get()
        root.destroy()

    btns = ttk.Frame(frm)
    btns.pack(fill="x")
    ttk.Button(btns, text="Cancel", command=root.destroy).pack(side="right")
    ttk.Button(btns, text="OK", command=ok).pack(side="right", padx=(0, 6))
    root.bind("<Return>", ok)
    root.bind("<Escape>", lambda e: root.destroy())
    _center(root)
    root.after(50, lambda: (root.lift(), root.focus_force(), entry.focus_set()))
    root.mainloop()
    return result["v"]


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


def show_text(title: str, text: str) -> None:
    """Read-only text with a Copy button (checksums, OCR previews)."""
    if not interactive():
        _stderr(text)
        return
    import tkinter as tk
    from tkinter import ttk

    root = _root(f"{APP_NAME} — {title}", hidden=False)
    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)
    box = tk.Text(frm, width=90, height=min(24, text.count("\n") + 2), font=("Consolas", 10), wrap="none")
    box.insert("1.0", text)
    box.configure(state="disabled")
    box.pack(fill="both", expand=True)

    def copy():
        root.clipboard_clear()
        root.clipboard_append(text)
        copy_btn.configure(text="Copied ✓")

    btns = ttk.Frame(frm)
    btns.pack(fill="x", pady=(8, 0))
    ttk.Button(btns, text="Close", command=root.destroy).pack(side="right")
    copy_btn = ttk.Button(btns, text="Copy", command=copy)
    copy_btn.pack(side="right", padx=6)
    root.bind("<Escape>", lambda e: root.destroy())
    _center(root)
    root.mainloop()


# ---------- ordering (merge/combine/join) ----------

def order_files(files: list[Path], title: str, add_types: Iterable[str] | None = None) -> list[Path] | None:
    """Let the user reorder/add/remove files before merging. None = cancelled."""
    if not interactive():
        return files
    import tkinter as tk
    from tkinter import filedialog, ttk

    root = _root(f"{APP_NAME} — {title}", hidden=False)
    items = list(files)
    result: dict[str, list[Path] | None] = {"v": None}
    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="The top file goes first. Drag to reorder, or use the buttons.").pack(anchor="w", pady=(0, 6))
    body = ttk.Frame(frm)
    body.pack(fill="both", expand=True)
    lb = tk.Listbox(body, width=70, height=min(max(len(items), 8), 20), selectmode="browse",
                    activestyle="none", font=("Segoe UI", 10))
    lb.pack(side="left", fill="both", expand=True)
    ok_btn = None

    def refresh(sel: int | None = None):
        lb.delete(0, "end")
        for i, p in enumerate(items, 1):
            lb.insert("end", f"{i:>3}.  {p.name}")
        if sel is not None and items:
            sel = max(0, min(sel, len(items) - 1))
            lb.selection_set(sel)
            lb.see(sel)
        if ok_btn is not None:
            ok_btn.state(["!disabled"] if len(items) >= 2 else ["disabled"])

    def move(delta: int):
        cur = lb.curselection()
        if cur and 0 <= cur[0] + delta < len(items):
            i, j = cur[0], cur[0] + delta
            items[i], items[j] = items[j], items[i]
            refresh(j)

    def remove():
        cur = lb.curselection()
        if cur:
            items.pop(cur[0])
            refresh(cur[0])

    def add():
        pats = " ".join(f"*{e}" for e in sorted(add_types or [])) or "*.*"
        picked = filedialog.askopenfilenames(parent=root, title="Add files",
                                             initialdir=str(items[0].parent) if items else None,
                                             filetypes=[("Supported files", pats), ("All files", "*.*")])
        for p in picked:
            if Path(p) not in items:
                items.append(Path(p))
        refresh(len(items) - 1)

    def sort_by_name():
        from .util import natural_key

        items.sort(key=natural_key)
        refresh(0)

    # drag to reorder
    drag = {"i": None}
    lb.bind("<Button-1>", lambda e: drag.update(i=lb.nearest(e.y)))

    def on_drag(e):
        j = lb.nearest(e.y)
        i = drag["i"]
        if i is not None and j != i and 0 <= j < len(items):
            items.insert(j, items.pop(i))
            drag["i"] = j
            refresh(j)

    lb.bind("<B1-Motion>", on_drag)

    side = ttk.Frame(body)
    side.pack(side="left", fill="y", padx=(8, 0))
    for text, cmd in (("▲ Up", lambda: move(-1)), ("▼ Down", lambda: move(1)), ("Add files…", add),
                      ("Remove", remove), ("Sort A→Z", sort_by_name),
                      ("Reverse", lambda: (items.reverse(), refresh(0)))):
        ttk.Button(side, text=text, width=12, command=cmd).pack(pady=2)

    def ok(_=None):
        if len(items) >= 2:
            result["v"] = list(items)
            root.destroy()

    foot = ttk.Frame(frm)
    foot.pack(fill="x", pady=(10, 0))
    ttk.Button(foot, text="Cancel", command=root.destroy).pack(side="right")
    ok_btn = ttk.Button(foot, text="Continue", command=ok)
    ok_btn.pack(side="right", padx=6)
    hint = ttk.Label(foot, text="", foreground="#666")
    hint.pack(side="left")
    if len(items) < 2:
        hint.configure(text="Only one file selected — use “Add files…” to add more.")
    root.bind("<Return>", ok)
    root.bind("<Escape>", lambda e: root.destroy())
    refresh(0)
    _center(root)
    root.mainloop()
    return result["v"]


# ---------- progress ----------

def run_with_progress(title: str, work: Callable[[], T]) -> T:
    """Run `work` in a thread; after 0.8 s show a progress window with a Cancel button."""
    if not interactive():
        return work()
    try:
        from tkinter import ttk
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
    t.join(0.8)
    if t.is_alive():
        try:
            root = _root(APP_NAME, hidden=False)
        except Exception:  # no display
            t.join()
        else:
            root.resizable(False, False)
            frm = ttk.Frame(root, padding=16)
            frm.pack(fill="both", expand=True)
            head = ttk.Label(frm, text=f"{title}…", font=("Segoe UI", 11, "bold"))
            head.pack(anchor="w")
            detail = ttk.Label(frm, text="", width=60, foreground="#555")
            detail.pack(anchor="w", pady=(4, 8))
            bar = ttk.Progressbar(frm, length=420, mode="indeterminate")
            bar.pack(fill="x")
            bar.start(12)
            pct = ttk.Label(frm, text="")
            pct.pack(anchor="e")
            state = {"det": False}

            def cancel():
                PROGRESS.cancelled = True
                cancel_btn.state(["disabled"])
                head.configure(text="Cancelling…")

            cancel_btn = ttk.Button(frm, text="Cancel", command=cancel)
            cancel_btn.pack(anchor="e", pady=(6, 0))
            root.protocol("WM_DELETE_WINDOW", cancel)

            def poll():
                if not t.is_alive():
                    root.destroy()
                    return
                f = PROGRESS.fraction
                if f is not None:
                    if not state["det"]:
                        bar.stop()
                        bar.configure(mode="determinate", maximum=1000)
                        state["det"] = True
                    bar["value"] = f * 1000
                    pct.configure(text=f"{f * 100:.0f}%")
                if PROGRESS.text:
                    detail.configure(text=PROGRESS.text[:90])
                root.after(120, poll)

            _center(root)
            poll()
            root.mainloop()
            t.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


# ---------- "done" notification ----------

def reveal(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def open_file(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - opening the user's own output
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def notify_done(outputs: list[Path], summary: str = "") -> None:
    """Small toast in the bottom-right corner: what was made, with Open / Show in folder."""
    if not interactive() or not outputs or os.environ.get("FILEWIZARD_NO_NOTIFY") == "1":
        return
    from .settings import load

    if not load().get("notify", True):
        return
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        return
    root = _root(APP_NAME, hidden=False)
    root.overrideredirect(True)
    root.configure(bg=ACCENT)
    inner = tk.Frame(root, bg="white", padx=14, pady=12)
    inner.pack(fill="both", expand=True, padx=(4, 0))
    first = outputs[0]
    title = "✓  Done" if len(outputs) == 1 else f"✓  Done — {len(outputs)} files"
    tk.Label(inner, text=title, bg="white", fg=ACCENT, font=("Segoe UI", 11, "bold")).pack(anchor="w")
    name = first.name if len(outputs) == 1 else f"{first.name}  +{len(outputs) - 1} more"
    tk.Label(inner, text=name[:60], bg="white", fg="#222", font=("Segoe UI", 10)).pack(anchor="w")
    if summary:
        tk.Label(inner, text=summary[:80], bg="white", fg="#666", font=("Segoe UI", 9)).pack(anchor="w")
    row = tk.Frame(inner, bg="white")
    row.pack(anchor="w", pady=(8, 0))

    def act(fn):
        try:
            fn()
        finally:
            root.destroy()

    if first.is_file():
        ttk.Button(row, text="Open", command=lambda: act(lambda: open_file(first))).pack(side="left")
    ttk.Button(row, text="Show in folder", command=lambda: act(lambda: reveal(first))).pack(side="left", padx=6)
    ttk.Button(row, text="✕", width=3, command=root.destroy).pack(side="left")
    root.update_idletasks()
    w, h = max(root.winfo_reqwidth(), 340), root.winfo_reqheight()
    x = root.winfo_screenwidth() - w - 24
    y = root.winfo_screenheight() - h - 72  # above the taskbar
    root.geometry(f"{w}x{h}+{x}+{y}")
    # stay while hovered, otherwise fade out after a few seconds
    state = {"hover": False}
    root.bind("<Enter>", lambda e: state.update(hover=True))
    root.bind("<Leave>", lambda e: state.update(hover=False))

    def tick(left=7.0):
        if state["hover"]:
            root.after(500, tick, 3.0)
        elif left <= 0:
            root.destroy()
        else:
            root.after(500, tick, left - 0.5)

    tick()
    root.mainloop()


# ---------- command palette ("All actions…") ----------

def palette(files: list[Path], ops: list) -> str | None:
    """Searchable list of every action that applies to the selection. Returns an op id."""
    if not interactive():
        raise RuntimeError("The action palette needs a desktop session.")
    import tkinter as tk
    from tkinter import ttk

    n = len(files)
    title = files[0].name if n == 1 else f"{n} files selected"
    root = _root(f"{APP_NAME} — {title}", hidden=False)
    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=f"What should FileWizard do with {title}?",
              font=("Segoe UI", 11, "bold")).pack(anchor="w")
    q = tk.StringVar()
    entry = ttk.Entry(frm, textvariable=q, width=60, font=("Segoe UI", 11))
    entry.pack(fill="x", pady=(8, 6))
    tree = ttk.Treeview(frm, show="tree", height=18, selectmode="browse")
    tree.pack(fill="both", expand=True)
    tree.column("#0", width=520)
    result: dict[str, str | None] = {"v": None}
    section_names = {"convert": "Convert", "combine": "Combine", "edit": "Edit", "extract": "Extract",
                     "archive": "Archive", "more": "More"}

    def fill(*_):
        tree.delete(*tree.get_children())
        words = q.get().lower().split()
        groups: dict[str, str] = {}
        first = None
        for op in ops:
            hay = f"{op.full_label} {op.id} {op.help}".lower()
            if not all(w in hay for w in words):
                continue
            g = op.menu[0] if op.menu else section_names.get(op.group, op.group.title())
            if g not in groups:
                groups[g] = tree.insert("", "end", text=g, open=True)
            label = op.label if op.menu and op.menu[0] == g else op.full_label
            iid = tree.insert(groups[g], "end", iid=op.id, text="    " + label.replace(" ▸ ", " › "))
            first = first or iid
        if first:
            tree.selection_set(first)
            tree.see(first)
            tree.yview_moveto(0)  # keep the first section heading visible

    def choose(_=None):
        sel = tree.selection()
        if sel and sel[0] in {op.id for op in ops}:
            result["v"] = sel[0]
            root.destroy()

    def move(delta):
        items = [i for g in tree.get_children() for i in tree.get_children(g)]
        if not items:
            return "break"
        cur = tree.selection()
        i = items.index(cur[0]) if cur and cur[0] in items else -1
        nxt = items[max(0, min(len(items) - 1, i + delta))]
        tree.selection_set(nxt)
        tree.see(nxt)
        return "break"

    q.trace_add("write", fill)
    entry.bind("<Down>", lambda e: move(1))
    entry.bind("<Up>", lambda e: move(-1))
    root.bind("<Return>", choose)
    root.bind("<Escape>", lambda e: root.destroy())
    tree.bind("<Double-1>", choose)
    foot = ttk.Frame(frm)
    foot.pack(fill="x", pady=(8, 0))
    ttk.Label(foot, text="Type to search · ↑↓ to pick · Enter to run", foreground="#666").pack(side="left")
    ttk.Button(foot, text="Cancel", command=root.destroy).pack(side="right")
    ttk.Button(foot, text="Run", command=choose).pack(side="right", padx=6)
    fill()
    _center(root)
    root.after(50, lambda: (root.lift(), root.focus_force(), entry.focus_set()))
    root.mainloop()
    return result["v"]


# ---------- settings window ----------

def settings_window(doctor_text: Callable[[], str], reinstall: Callable[[], None],
                    remove: Callable[[], None], log_path: Path) -> None:
    import tkinter as tk
    from tkinter import ttk

    from . import __version__
    from .settings import load, save

    root = _root(f"{APP_NAME} settings", hidden=False)
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=f"{APP_NAME} {__version__}", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    ttk.Label(frm, text="Right-click any file → FileWizard. On Windows 11: “Show more options” or Shift+right-click.",
              foreground="#555", wraplength=460).pack(anchor="w", pady=(2, 12))

    cfg = load()
    notify = tk.BooleanVar(value=cfg.get("notify", True))
    ttk.Checkbutton(frm, text="Show a “Done” notification with Open / Show in folder",
                    variable=notify, command=lambda: save({"notify": notify.get()})).pack(anchor="w")
    out_mode = tk.StringVar(value=cfg.get("output", "beside"))
    ttk.Label(frm, text="Save results:").pack(anchor="w", pady=(10, 0))
    ttk.Radiobutton(frm, text="Next to the original file", value="beside", variable=out_mode,
                    command=lambda: save({"output": out_mode.get()})).pack(anchor="w", padx=12)
    ttk.Radiobutton(frm, text="In a “FileWizard” folder next to it", value="subfolder", variable=out_mode,
                    command=lambda: save({"output": out_mode.get()})).pack(anchor="w", padx=12)

    status = ttk.Label(frm, text="", foreground=ACCENT)

    def do(fn, ok_text):
        try:
            fn()
            status.configure(text=ok_text)
        except BaseException as e:  # SystemExit from install on Store Python etc.
            status.configure(text=str(e)[:200], foreground="#b00020")

    row = ttk.Frame(frm)
    row.pack(fill="x", pady=(14, 4))
    ttk.Button(row, text="Repair right-click menu", command=lambda: do(reinstall, "Menu re-installed ✓")).pack(side="left")
    ttk.Button(row, text="Remove right-click menu", command=lambda: do(remove, "Menu removed.")).pack(side="left", padx=6)
    row2 = ttk.Frame(frm)
    row2.pack(fill="x", pady=4)
    ttk.Button(row2, text="Check optional tools…", command=lambda: show_text("Optional tools", doctor_text())).pack(side="left")
    ttk.Button(row2, text="Open log", command=lambda: log_path.exists() and open_file(log_path)).pack(side="left", padx=6)
    status.pack(anchor="w", pady=(8, 0))
    ttk.Button(frm, text="Close", command=root.destroy).pack(anchor="e", pady=(12, 0))
    _center(root)
    root.mainloop()
