"""Register/unregister the Explorer right-click menu.

Uses per-user keys under HKEY_CURRENT_USER\\Software\\Classes\\SystemFileAssociations,
so it needs no admin rights and works regardless of which app opens the file type.
Layout per extension:

    SystemFileAssociations\\.pdf\\shell\\FileWizard            MUIVerb, SubCommands="", Icon
    SystemFileAssociations\\.pdf\\shell\\FileWizard\\shell\\03_pdf-merge          MUIVerb
    SystemFileAssociations\\.pdf\\shell\\FileWizard\\shell\\03_pdf-merge\\command  (default) = launcher
"""

from __future__ import annotations

import sys
from pathlib import Path

from .operations import ALL_EXTS, ANY_FILE, DIR, OPERATIONS, for_ext
from .util import APP_NAME, app_dir

ROOT = r"Software\Classes\SystemFileAssociations"
MENU_KEY = APP_NAME
ECF_SEPARATORBEFORE = 0x20

REG_SZ, REG_DWORD = "REG_SZ", "REG_DWORD"
Entries = list[tuple[str, dict[str, tuple[str, object]]]]


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def _submenu_index() -> dict[tuple[str, ...], int]:
    """Stable sort position for each submenu: where its first item sits in the global catalog.

    Using global positions (not per-extension ones) keeps every verb's registry key name
    identical across file types, which is what lets Explorer run one command over a mixed
    selection (say a .jpg and a .png together).
    """
    idx: dict[tuple[str, ...], int] = {}
    for i, op in enumerate(OPERATIONS):
        for depth in range(1, len(op.menu) + 1):
            idx.setdefault(op.menu[:depth], i)
    return idx


def _menu_root(ext: str) -> str:
    if ext == ANY_FILE:
        return rf"Software\Classes\*\shell\{MENU_KEY}"
    if ext == DIR:
        return rf"Software\Classes\Directory\shell\{MENU_KEY}"
    return rf"{ROOT}\{ext}\shell\{MENU_KEY}"


def applies_to_unknown() -> str:
    """Advanced Query Syntax filter: any file whose extension isn't one we register."""
    return "NOT (" + " OR ".join(f'System.FileExtension:="{e}"' for e in ALL_EXTS) + ")"


def build_entries(command_prefix: str, icon: str | None) -> Entries:
    """All registry keys/values to create. `command_prefix` gets ` <op-id> "%1"` appended."""
    entries: Entries = []
    sub_idx = _submenu_index()
    gidx = {op.id: i for i, op in enumerate(OPERATIONS)}
    for ext in [*ALL_EXTS, DIR, ANY_FILE]:
        ops = for_ext(ext) if ext != ANY_FILE else [op for op in OPERATIONS if ANY_FILE in op.exts]
        if not ops:
            continue
        base = _menu_root(ext)
        top = {"MUIVerb": (REG_SZ, APP_NAME), "SubCommands": (REG_SZ, "")}
        if ext == ANY_FILE:
            # Only for types without their own FileWizard menu, so nothing shows twice.
            top["AppliesTo"] = (REG_SZ, applies_to_unknown())
        if icon:
            top["Icon"] = (REG_SZ, icon)
        entries.append((base, top))

        # Top-level nodes (items and submenus) in display order, to place separators.
        nodes: dict[str, tuple[int, str]] = {}  # key name -> (sort index, group)
        made_menus: set[tuple[str, ...]] = set()
        for op in ops:
            parent = base
            for depth in range(1, len(op.menu) + 1):
                path = op.menu[:depth]
                key = rf"{parent}\shell\{sub_idx[path]:03d}_{_slug(path[-1])}"
                if path not in made_menus:
                    made_menus.add(path)
                    entries.append((key, {"MUIVerb": (REG_SZ, path[-1]), "SubCommands": (REG_SZ, ""),
                                          "MultiSelectModel": (REG_SZ, "Player")}))
                    if depth == 1:
                        nodes[key] = (sub_idx[path], op.group)
                parent = key
            key = rf"{parent}\shell\{gidx[op.id]:03d}_{op.id}"
            vals: dict[str, tuple[str, object]] = {
                "MUIVerb": (REG_SZ, op.label_for(ext)),
                # Without this, Explorer hides the verb when more than 15 files are selected.
                "MultiSelectModel": (REG_SZ, "Player"),
            }
            entries.append((key, vals))
            entries.append((rf"{key}\command", {"": (REG_SZ, f'{command_prefix} {op.id} "%1"')}))
            if not op.menu:
                nodes[key] = (gidx[op.id], op.group)

        prev = None
        index = {k: i for i, (k, _) in enumerate(entries)}
        for key, (_, group) in sorted(nodes.items(), key=lambda kv: kv[1][0]):
            if prev is not None and group != prev:
                entries[index[key]][1]["CommandFlags"] = (REG_DWORD, ECF_SEPARATORBEFORE)
            prev = group
    return entries


def to_reg_file(entries: Entries) -> str:
    """Render entries as a .reg file (handy for inspecting what install does)."""
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["Windows Registry Editor Version 5.00", ""]
    for key, vals in entries:
        lines.append(f"[HKEY_CURRENT_USER\\{key}]")
        for name, (typ, val) in vals.items():
            left = "@" if name == "" else q(name)
            right = f"dword:{int(val):08x}" if typ == REG_DWORD else q(str(val))
            lines.append(f"{left}={right}")
        lines.append("")
    return "\r\n".join(lines)


# ---------- launcher & icon ----------

LAUNCHER = '''\
# Auto-generated by FileWizard. Explorer runs this; it starts FileWizard without a console window.
# Running a script (rather than `python -m`) keeps the clicked folder off sys.path, so a stray
# .py file next to your documents can't hijack the import.
import sys
PACKAGE_PARENT = {pkg_parent!r}
if PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, PACKAGE_PARENT)
from filewizard.cli import main
sys.exit(main(["run", "--shell"] + sys.argv[1:]))
'''


def pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def write_launcher() -> Path:
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    p = app_dir() / "launch.pyw"
    p.write_text(LAUNCHER.format(pkg_parent=pkg_parent), encoding="utf-8")
    return p


def write_icon() -> str | None:
    """Draw a simple icon so the menu entry is easy to spot."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    s = 256
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((28, 12, 200, 244), radius=22, fill=(52, 98, 220))
    d.polygon([(150, 12), (200, 62), (150, 62)], fill=(150, 180, 250))
    for y in (100, 140, 180):
        d.rounded_rectangle((60, y, 168, y + 16), radius=8, fill=(255, 255, 255))
    # the "wand" star
    cx, cy, r = 200, 196, 46
    star = []
    import math
    for k in range(10):
        rad = r if k % 2 == 0 else r * 0.45
        a = math.pi / 2 + k * math.pi / 5
        star.append((cx + rad * math.cos(a), cy - rad * math.sin(a)))
    d.polygon(star, fill=(255, 196, 0), outline=(120, 80, 0))
    p = app_dir() / "filewizard.ico"
    img.save(p, "ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)])
    return str(p)


def command_prefix(launcher: Path) -> str:
    return f'"{pythonw()}" "{launcher}"'


# ---------- registry I/O (Windows only) ----------

def _winreg():
    if sys.platform != "win32":
        raise SystemExit("Installing the right-click menu only works on Windows. "
                         "Use `filewizard export-reg` to see what would be written.")
    import winreg

    return winreg


def _delete_tree(winreg, root, path: str) -> None:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    child = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(winreg, root, rf"{path}\{child}")
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def _refresh_explorer() -> None:
    import ctypes

    SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0
    ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


def uninstall(quiet: bool = False) -> int:
    winreg = _winreg()
    hk = winreg.HKEY_CURRENT_USER
    removed = 0
    try:
        with winreg.OpenKey(hk, ROOT) as k:
            subkeys = []
            i = 0
            while True:
                try:
                    subkeys.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
    except FileNotFoundError:
        subkeys = []
    paths = [rf"{ROOT}\{ext}\shell\{MENU_KEY}" for ext in subkeys]  # scan: cleans old versions too
    paths += [_menu_root(DIR), _menu_root(ANY_FILE)]
    for path in paths:
        try:
            winreg.OpenKey(hk, path).Close()
        except FileNotFoundError:
            continue
        _delete_tree(winreg, hk, path)
        removed += 1
    _refresh_explorer()
    if not quiet:
        print(f"Removed the {APP_NAME} menu from {removed} file types.")
    return removed


def _is_store_python() -> bool:
    exe = sys.executable.lower()
    return "windowsapps" in exe or "pythonsoftwarefoundation" in exe


def install() -> None:
    winreg = _winreg()
    if _is_store_python():
        # Store apps get a private, virtualized copy of HKCU and AppData: our writes would
        # "succeed" but Explorer would never see them.
        raise SystemExit(
            "This is the Microsoft Store version of Python, which is sandboxed: Explorer can't see\n"
            "menu entries it creates. Install Python from https://www.python.org/downloads/ instead\n"
            "(tick 'Add python.exe to PATH'), then run install.bat again."
        )
    uninstall(quiet=True)  # start clean so renamed/removed actions don't linger
    launcher = write_launcher()
    icon = write_icon()
    entries = build_entries(command_prefix(launcher), icon)
    hk = winreg.HKEY_CURRENT_USER
    types = {REG_SZ: winreg.REG_SZ, REG_DWORD: winreg.REG_DWORD}
    for key, vals in entries:
        with winreg.CreateKeyEx(hk, key, 0, winreg.KEY_WRITE) as k:
            for name, (typ, val) in vals.items():
                winreg.SetValueEx(k, name, 0, types[typ], val)
    _refresh_explorer()
    print(f"Installed the {APP_NAME} menu for {len(ALL_EXTS)} file types.")
    print(f"Launcher: {launcher}")
    print("Right-click a PDF, image or Office file. On Windows 11, it's under 'Show more options'"
          " (or hold Shift while right-clicking).")


def render_menu(ext: str) -> str:
    """Text picture of the FileWizard submenu for one extension, exactly as registered."""
    ext = {"folder": DIR, "dir": DIR, "any": ANY_FILE}.get(ext.lower(), ext)
    ext = ext if ext in (DIR, ANY_FILE) or ext.startswith(".") else "." + ext
    ext = ext.lower()
    base = _menu_root(ext)
    entries = {k: v for k, v in build_entries("cmd", None) if k.startswith(base)}
    if base not in entries:
        return f"(no FileWizard menu for {ext})"

    def children(key: str) -> list[str]:
        pre = key + "\\shell\\"
        return sorted(k for k in entries if k.startswith(pre) and "\\" not in k[len(pre):])

    lines = [f"{APP_NAME}  ▸"]

    def walk(key: str, depth: int):
        for child in children(key):
            vals = entries[child]
            if "CommandFlags" in vals:
                lines.append("    " * depth + "────────────")
            label = vals["MUIVerb"][1]
            sub = "SubCommands" in vals
            lines.append("    " * depth + label + ("  ▸" if sub else ""))
            if sub:
                walk(child, depth + 1)

    walk(base, 1)
    return "\n".join(lines)
