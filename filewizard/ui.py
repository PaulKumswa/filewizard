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

LIGHT = {"bg": "#f3f3f3", "fg": "#1b1b1b", "muted": "#5c5c5c", "field": "#ffffff", "button": "#fdfdfd",
         "border": "#c8c8c8", "select": "#cfe0fc", "canvas": "#eef0f4", "accent": "#3462dc"}
DARK = {"bg": "#202020", "fg": "#f2f2f2", "muted": "#a8a8a8", "field": "#2b2b2b", "button": "#2d2d2d",
        "border": "#3c3c3c", "select": "#2b4a80", "canvas": "#181818", "accent": "#6b9cff"}


def dark_mode() -> bool:
    """Follow Windows' 'app mode' (Settings > Personalization > Colors). FILEWIZARD_THEME overrides."""
    forced = os.environ.get("FILEWIZARD_THEME")
    if forced in ("dark", "light"):
        return forced == "dark"
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


THEME = DARK if dark_mode() else LIGHT


def _apply_theme(root) -> None:
    """Colour ttk + classic Tk widgets to match the Windows light/dark setting."""
    from tkinter import ttk

    t = THEME
    style = ttk.Style(root)
    if THEME is DARK or sys.platform != "win32":
        style.theme_use("clam")  # the native Windows theme can't be recoloured
    if THEME is DARK:
        style.configure(".", background=t["bg"], foreground=t["fg"], fieldbackground=t["field"],
                        bordercolor=t["border"], lightcolor=t["bg"], darkcolor=t["bg"], troughcolor=t["field"])
        style.configure("TButton", background=t["button"], foreground=t["fg"], padding=(10, 4))
        style.map("TButton", background=[("active", t["border"]), ("disabled", t["bg"])],
                  foreground=[("disabled", t["muted"])])
        style.configure("TEntry", fieldbackground=t["field"], foreground=t["fg"], insertcolor=t["fg"])
        style.configure("Treeview", background=t["field"], fieldbackground=t["field"], foreground=t["fg"])
        style.map("Treeview", background=[("selected", t["select"])])
        style.configure("TCheckbutton", background=t["bg"], foreground=t["fg"])
        style.configure("TRadiobutton", background=t["bg"], foreground=t["fg"])
        style.configure("Horizontal.TProgressbar", background=t["accent"], troughcolor=t["field"])
        root.configure(background=t["bg"])
        root.option_add("*Listbox.background", t["field"])
        root.option_add("*Listbox.foreground", t["fg"])
        root.option_add("*Listbox.selectBackground", t["select"])
        root.option_add("*Text.background", t["field"])
        root.option_add("*Text.foreground", t["fg"])
        root.option_add("*Canvas.background", t["canvas"])
        if sys.platform == "win32":  # dark title bar (DWMWA_USE_IMMERSIVE_DARK_MODE = 20)
            try:
                import ctypes

                root.update_idletasks()
                hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
                val = ctypes.c_int(1)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val))
            except Exception:
                pass


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
        _apply_theme(root)
    except Exception:
        pass
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
    hint = ttk.Label(foot, text="", foreground=THEME["muted"])
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
            detail = ttk.Label(frm, text="", width=60, foreground=THEME["muted"])
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


def recycle(paths: list[Path]) -> None:
    """Send files/folders to the Recycle Bin (recoverable), not a permanent delete."""
    paths = [p for p in paths if p.exists()]
    if not paths:
        return
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT = 3, 0x40, 0x10, 0x4
        op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom="\0".join(str(p) for p in paths) + "\0\0",
                             fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT)
        ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    else:
        import shutil

        for p in paths:
            shutil.rmtree(p) if p.is_dir() else p.unlink()


def notify_done(outputs: list[Path], summary: str = "", undoable: bool = True) -> None:
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
    t = THEME
    root.configure(bg=t["accent"])
    inner = tk.Frame(root, bg=t["field"], padx=14, pady=12)
    inner.pack(fill="both", expand=True, padx=(4, 0))
    first = outputs[0]
    title = "✓  Done" if len(outputs) == 1 else f"✓  Done — {len(outputs)} files"
    tk.Label(inner, text=title, bg=t["field"], fg=t["accent"], font=("Segoe UI", 11, "bold")).pack(anchor="w")
    name = first.name if len(outputs) == 1 else f"{first.name}  +{len(outputs) - 1} more"
    tk.Label(inner, text=name[:60], bg=t["field"], fg=t["fg"], font=("Segoe UI", 10)).pack(anchor="w")
    if summary:
        tk.Label(inner, text=summary[:80], bg=t["field"], fg=t["muted"], font=("Segoe UI", 9)).pack(anchor="w")
    row = tk.Frame(inner, bg=t["field"])
    row.pack(anchor="w", pady=(8, 0))

    def act(fn):
        try:
            fn()
        finally:
            root.destroy()

    if first.is_file():
        ttk.Button(row, text="Open", command=lambda: act(lambda: open_file(first))).pack(side="left")
    ttk.Button(row, text="Show in folder", command=lambda: act(lambda: reveal(first))).pack(side="left", padx=6)
    if undoable:
        ttk.Button(row, text="Undo", command=lambda: act(lambda: recycle(outputs))).pack(side="left", padx=(0, 6))
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
    ttk.Label(foot, text="Type to search · ↑↓ to pick · Enter to run", foreground=THEME["muted"]).pack(side="left")
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
              foreground=THEME["muted"], wraplength=460).pack(anchor="w", pady=(2, 12))

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

    menu_style = tk.StringVar(value=cfg.get("menu", "auto"))
    ttk.Label(frm, text="Menu style:").pack(anchor="w", pady=(10, 0))

    def set_style():
        save({"menu": menu_style.get()})
        do(reinstall, "Menu re-installed ✓")

    ttk.Radiobutton(frm, text="Full menu: every action, adapts to your selection (recommended)", value="auto",
                    variable=menu_style, command=set_style).pack(anchor="w", padx=12)
    ttk.Radiobutton(frm, text="Classic: simpler, top actions + “All actions…” (use if the full menu doesn't show)",
                    value="classic", variable=menu_style, command=set_style).pack(anchor="w", padx=12)

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


# ---------- visual page picker ----------

def pages_to_spec(selected: list[int]) -> str:
    """[0,1,2,4,7,8] -> '1-3,5,8-9' (1-based ranges)."""
    out, run_start, prev = [], None, None
    for p in sorted(set(selected)):
        if run_start is None:
            run_start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            out.append(f"{run_start + 1}" if run_start == prev else f"{run_start + 1}-{prev + 1}")
            run_start = prev = p
    if run_start is not None:
        out.append(f"{run_start + 1}" if run_start == prev else f"{run_start + 1}-{prev + 1}")
    return ",".join(out)


def pick_pages(pdf: Path, verb: str, max_pages: int = 400) -> str | None:
    """Thumbnail grid: click pages to select them. Returns a page spec like '1-3,5' or None."""
    import tkinter as tk
    from tkinter import ttk

    import pymupdf

    doc = pymupdf.open(pdf)
    n = doc.page_count
    if n > max_pages:
        doc.close()
        return ask_string(f"Pages to {verb}, e.g.  1-3, 5, 8-   ({pdf.name} has {n} pages)")
    root = _root(f"{APP_NAME} — {verb.title()} pages: {pdf.name}", hidden=False)
    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)
    head = ttk.Label(frm, text=f"Click the pages to {verb}.", font=("Segoe UI", 11, "bold"))
    head.pack(anchor="w")
    canvas = tk.Canvas(frm, width=760, height=520, background=THEME["canvas"], highlightthickness=0)
    bar = ttk.Scrollbar(frm, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side="left", fill="both", expand=True, pady=(8, 0))
    bar.pack(side="left", fill="y", pady=(8, 0))
    grid = tk.Frame(canvas, background=THEME["canvas"])
    canvas.create_window((0, 0), window=grid, anchor="nw")
    selected: set[int] = set()
    images, tiles = [], []
    cols, thumb = 5, 130
    spec = tk.StringVar()
    result: dict[str, str | None] = {"v": None}

    def paint(i):
        tiles[i].configure(background=THEME["accent"] if i in selected else THEME["canvas"])

    def toggle(i, _=None):
        selected.symmetric_difference_update({i})
        paint(i)
        spec.set(pages_to_spec(sorted(selected)))

    for i, page in enumerate(doc):
        zoom = thumb / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        img = tk.PhotoImage(data=pix.tobytes("ppm"))
        images.append(img)
        tile = tk.Frame(grid, background=THEME["canvas"], padx=4, pady=4, cursor="hand2")
        tile.grid(row=i // cols, column=i % cols, padx=6, pady=6)
        lab = tk.Label(tile, image=img, borderwidth=1, relief="solid")
        lab.pack()
        num = tk.Label(tile, text=str(i + 1), background=THEME["canvas"], foreground=THEME["fg"])
        num.pack()
        for w in (tile, lab, num):
            w.bind("<Button-1>", lambda e, i=i: toggle(i))
        tiles.append(tile)
        if i % 20 == 0:
            root.update()
    doc.close()
    grid.update_idletasks()
    canvas.configure(scrollregion=canvas.bbox("all"))
    canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
    canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
    canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

    def apply_typed(*_):
        from .util import UserError, parse_page_ranges

        try:
            idx = set(parse_page_ranges(spec.get(), n)) if spec.get().strip() else set()
        except UserError:
            return
        selected.clear()
        selected.update(idx)
        for i in range(n):
            paint(i)

    foot = ttk.Frame(root, padding=(10, 0, 10, 10))
    foot.pack(fill="x")
    ttk.Label(foot, text="Pages:").pack(side="left")
    entry = ttk.Entry(foot, textvariable=spec, width=28)
    entry.pack(side="left", padx=6)
    entry.bind("<KeyRelease>", apply_typed)
    ttk.Button(foot, text="All", command=lambda: (spec.set(f"1-{n}"), apply_typed())).pack(side="left")
    ttk.Button(foot, text="None", command=lambda: (spec.set(""), apply_typed())).pack(side="left", padx=4)

    def ok(_=None):
        if spec.get().strip():
            result["v"] = spec.get()
            root.destroy()

    ttk.Button(foot, text="Cancel", command=root.destroy).pack(side="right")
    ttk.Button(foot, text=verb.title(), command=ok).pack(side="right", padx=6)
    root.bind("<Return>", ok)
    root.bind("<Escape>", lambda e: root.destroy())
    _center(root)
    root.mainloop()
    return result["v"]


# ---------- place a signature ----------

def place_signature(pdf: Path, sig_png: bytes) -> dict | None:
    """Show a page; click to place the signature, wheel to resize, ◀ ▶ to change page."""
    import tkinter as tk
    from tkinter import ttk

    import pymupdf

    doc = pymupdf.open(pdf)
    state = {"page": doc.page_count - 1, "x": None, "y": None, "w": 0.28}  # w: fraction of page width
    root = _root(f"{APP_NAME} — Sign {pdf.name}", hidden=False)
    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Click where the signature goes. Scroll to resize.",
              font=("Segoe UI", 11, "bold")).pack(anchor="w")
    canvas = tk.Canvas(frm, background=THEME["canvas"], highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True, pady=8)
    sig_doc = pymupdf.open("png", sig_png)
    sig_ratio = sig_doc[0].rect.height / sig_doc[0].rect.width
    sig_doc.close()
    view = {"scale": 1.0, "img": None, "sig": None, "sig_w": 0}
    max_h = min(760, root.winfo_screenheight() - 220)

    def render():
        page = doc[state["page"]]
        view["scale"] = max_h / page.rect.height
        pix = page.get_pixmap(matrix=pymupdf.Matrix(view["scale"], view["scale"]), alpha=False)
        view["img"] = tk.PhotoImage(data=pix.tobytes("ppm"))
        canvas.configure(width=pix.width, height=pix.height)
        canvas.delete("all")
        canvas.create_image(0, 0, image=view["img"], anchor="nw")
        pagelbl.configure(text=f"Page {state['page'] + 1} of {doc.page_count}")
        draw_sig()

    def draw_sig():
        canvas.delete("sig")
        if state["x"] is None:
            return
        page = doc[state["page"]]
        w_px = max(20, int(page.rect.width * state["w"] * view["scale"]))
        if view["sig_w"] != w_px:
            from PIL import Image
            import io

            im = Image.open(io.BytesIO(sig_png)).convert("RGBA")
            im = im.resize((w_px, max(1, int(w_px * sig_ratio))))
            buf = io.BytesIO()
            im.save(buf, "PNG")
            view["sig"] = tk.PhotoImage(data=buf.getvalue())
            view["sig_w"] = w_px
        canvas.create_image(state["x"], state["y"], image=view["sig"], tags="sig")
        h_px = int(w_px * sig_ratio)
        canvas.create_rectangle(state["x"] - w_px / 2, state["y"] - h_px / 2, state["x"] + w_px / 2,
                                state["y"] + h_px / 2, outline=ACCENT, dash=(3, 2), tags="sig")

    def click(e):
        state["x"], state["y"] = e.x, e.y
        draw_sig()

    def wheel(e):
        delta = e.delta if e.delta else (120 if e.num == 4 else -120)
        state["w"] = max(0.05, min(0.9, state["w"] * (1.1 if delta > 0 else 0.9)))
        draw_sig()

    def turn(d):
        state["page"] = max(0, min(doc.page_count - 1, state["page"] + d))
        render()

    canvas.bind("<Button-1>", click)
    canvas.bind("<B1-Motion>", click)
    canvas.bind("<MouseWheel>", wheel)
    canvas.bind("<Button-4>", wheel)
    canvas.bind("<Button-5>", wheel)
    foot = ttk.Frame(frm)
    foot.pack(fill="x")
    ttk.Button(foot, text="◀", width=3, command=lambda: turn(-1)).pack(side="left")
    pagelbl = ttk.Label(foot, text="")
    pagelbl.pack(side="left", padx=6)
    ttk.Button(foot, text="▶", width=3, command=lambda: turn(1)).pack(side="left")
    result: dict = {"v": None}

    def ok(_=None):
        if state["x"] is None:
            return
        page = doc[state["page"]]
        s = view["scale"]
        result["v"] = {"page": str(state["page"] + 1), "x": f"{state['x'] / s:.1f}", "y": f"{state['y'] / s:.1f}",
                       "width": f"{page.rect.width * state['w']:.1f}"}
        root.destroy()

    ttk.Button(foot, text="Cancel", command=root.destroy).pack(side="right")
    ttk.Button(foot, text="Sign", command=ok).pack(side="right", padx=6)
    root.bind("<Return>", ok)
    root.bind("<Escape>", lambda e: root.destroy())
    render()
    _center(root)
    root.mainloop()
    doc.close()
    return result["v"]
