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

from .operations import ALL_EXTS, ANY_FILE, BACKGROUND, DIR, OPERATIONS, for_ext
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
    if ext == BACKGROUND:
        return rf"Software\Classes\Directory\Background\shell\{MENU_KEY}"
    if ext == ANY_FILE:
        return rf"Software\Classes\*\shell\{MENU_KEY}"
    if ext == DIR:
        return rf"Software\Classes\Directory\shell\{MENU_KEY}"
    return rf"{ROOT}\{ext}\shell\{MENU_KEY}"


def applies_to_unknown() -> str:
    """Advanced Query Syntax filter: any file whose extension isn't one we register."""
    return "NOT (" + " OR ".join(f'System.FileExtension:="{e}"' for e in ALL_EXTS) + ")"


CLASSIC_LIMIT = 16  # Windows shows at most 16 entries in a registry-defined cascade (measured in CI)


def _cost(ops) -> int:
    """Entries an op set occupies: every item plus one per distinct submenu path."""
    menus = {op.menu[:d] for op in ops for d in range(1, len(op.menu) + 1)}
    return len(ops) + len(menus)


def fit_classic(ops: list) -> list:
    """Choose what fits in Windows' 16-entry cascade, keeping menu order.

    Core actions first (in menu order), then secondary ones while room remains; the last
    slot is always "All actions…", which opens the searchable palette with everything."""
    palette = [op for op in ops if op.id == "palette"]
    rest = [op for op in ops if op.id != "palette"]
    if _cost(rest) + len(palette) <= CLASSIC_LIMIT:
        return rest + palette
    budget = CLASSIC_LIMIT - len(palette)
    chosen: list = []
    for op in rest:  # core actions, in menu order
        if op.priority == 1 and _cost(chosen + [op]) <= budget:
            chosen.append(op)
    # optional extras: a submenu goes in whole or not at all (half a "Speed ▸" looks broken),
    # unless that submenu is already present because of a core action
    groups: dict[tuple, list] = {}
    for op in rest:
        if op.priority == 2:
            groups.setdefault(op.menu[:1] or (op.id,), []).append(op)
    present = {op.menu[:1] for op in chosen if op.menu}
    for key, members in groups.items():
        if key in present:
            for op in members:
                if _cost(chosen + [op]) <= budget:
                    chosen.append(op)
        elif _cost(chosen + members) <= budget:
            chosen.extend(members)
    order = {op.id: i for i, op in enumerate(rest)}
    return sorted(chosen, key=lambda op: order[op.id]) + palette


def build_entries(command_prefix: str, icon: str | None, compact: bool = True,
                  hidden: set[str] | None = None) -> Entries:
    """All registry keys/values to create. `command_prefix` gets ` <op-id> "%1"` appended."""
    entries: Entries = []
    sub_idx = _submenu_index()
    gidx = {op.id: i for i, op in enumerate(OPERATIONS)}
    for ext in [*ALL_EXTS, DIR, ANY_FILE, BACKGROUND]:
        ops = for_ext(ext) if ext != ANY_FILE else [op for op in OPERATIONS if ANY_FILE in op.exts]
        if not ops:
            continue
        target = '"%V"' if ext == BACKGROUND else '"%1"'  # %V = the folder you right-clicked inside
        if hidden:
            ops = [op for op in ops if op.id not in hidden]
        if compact:
            ops = fit_classic(ops)
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
                    entries.append((key, {"MUIVerb": (REG_SZ, path[-1].replace("&", "&&")), "SubCommands": (REG_SZ, ""),
                                          "MultiSelectModel": (REG_SZ, "Player")}))
                    if depth == 1:
                        nodes[key] = (sub_idx[path], op.group)
                parent = key
            key = rf"{parent}\shell\{gidx[op.id]:03d}_{op.id}"
            vals: dict[str, tuple[str, object]] = {
                "MUIVerb": (REG_SZ, op.label_for(ext).replace("&", "&&")),  # '&' = shortcut marker
                # Without this, Explorer hides the verb when more than 15 files are selected.
                "MultiSelectModel": (REG_SZ, "Player"),
            }
            entries.append((key, vals))
            entries.append((rf"{key}\command", {"": (REG_SZ, f'{command_prefix} {op.id} {target}')}))
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
sys.exit(main(sys.argv[1:]))
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


def draw_icon(size: int = 256):
    """FileWizard's icon: a blue document with a folded corner and a gold sparkle.
    Drawn at 4x and downsampled, so edges stay smooth at every size."""
    import math

    from PIL import Image, ImageDraw

    S = size * 4
    u = S / 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # page shape: rounded rectangle with the top-right corner cut off (via a mask)
    left, top, right, bottom, fold = 30 * u, 10 * u, 196 * u, 236 * u, 56 * u
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((left, top, right, bottom), radius=26 * u, fill=255)
    md.polygon([(right - fold, top - 2 * u), (right + 2 * u, top - 2 * u), (right + 2 * u, top + fold)], fill=0)
    img.paste((41, 98, 214, 255), (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.polygon([(right - fold, top), (right, top + fold), (right - fold + 10 * u, top + fold)], fill=(147, 182, 246))
    for y, w in ((100, 100), (136, 80), (172, 56)):
        d.rounded_rectangle((62 * u, y * u, (62 + w) * u, (y + 16) * u), radius=8 * u, fill=(255, 255, 255, 240))
    # four-point sparkle, fully inside the canvas
    cx, cy, r = 190 * u, 196 * u, 50 * u
    pts = []
    for k in range(8):
        rad = r if k % 2 == 0 else r * 0.3
        ang = -math.pi / 2 + k * math.pi / 4
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    d.polygon(pts, fill=(255, 193, 7))
    return img.resize((size, size), Image.LANCZOS)


def write_icon() -> str | None:
    """Save the icon (multi-size .ico) so the menu, dialogs and installer can use it."""
    try:
        img = draw_icon(256)
    except ImportError:
        return None
    p = app_dir() / "filewizard.ico"
    img.save(p, "ICO", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return str(p)


def frozen() -> bool:
    """True when running from the standalone installer build (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def command_prefix(launcher: Path | None, mode: str = "--shell") -> str:
    """How Explorer starts FileWizard. mode: --shell (classic verbs, one process per file,
    collector merges them) or --native (the DLL passes the whole selection at once)."""
    if frozen():
        exe = Path(sys.executable)
        windowed = exe.with_name("filewizardw.exe")
        return f'"{windowed if windowed.exists() else exe}" run {mode}'
    return f'"{pythonw()}" "{launcher}" run {mode}'


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


# ---------- native shell extension (full menu, selection-aware) ----------

CLSID = "{6E2B7D59-3D4A-4C61-9F0E-FA11E3A1D0C8}"  # must match native/fwshell.cpp
HANDLER_PARENTS = (r"Software\Classes\*\shellex\ContextMenuHandlers",
                   r"Software\Classes\Directory\shellex\ContextMenuHandlers",
                   r"Software\Classes\Directory\Background\shellex\ContextMenuHandlers")


# Menu images: glyphs from Windows' own icon font (what Explorer uses), keyed by
# submenu name or action id. Codepoints exist in both Segoe MDL2 Assets and Segoe Fluent Icons.
GLYPHS = {
    "Convert to": "\ue8ab", "Compress": "\ue73f", "Resize": "\ue740", "Pages": "\ue7c3", "Rotate": "\ue7ad",
    "Speed": "\ue916", "Edit": "\ue70f", "Password": "\ue72e", "Extract": "\ue896", "More": "\ue712",
    "Stitch into one image": "\ue91b",
    "combine-pdf": "\ue8a5", "media-join": "\ue8a5", "img-contact-sheet": "\ue91b",
    "pdf-compress": "\ue73f", "img-compress": "\ue73f", "aud-compress": "\ue73f", "office-compress": "\ue73f",
    "media-trim": "\ue7a8", "pdf-ocr": "\ue721", "img-ocr": "\ue8c8", "copy-text": "\ue8c8",
    "img-strip": "\ue7ba", "media-normalize": "\ue767", "vid-mute": "\ue74f", "vid-subs": "\ue7f0",
    "media-ringtone": "\ue8d6", "arc-extract": "\ue8b7", "arc-extract-folder": "\ue8b7", "palette": "\ue721",
}
ICON_COLOR = (47, 111, 222, 255)  # mid-tone blue: readable in light and dark menus


def _glyph_font():
    import os

    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        if (fonts / name).exists():
            return fonts / name
    return None


def write_menu_icons(folder: Path) -> dict[str, str]:
    """Render GLYPHS to small .ico files. Returns {key: path}; empty when the font is missing."""
    font_path = _glyph_font()
    if not font_path:
        return {}
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return {}
    folder.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(font_path), 52)
    out: dict[str, str] = {}
    cache: dict[str, str] = {}
    for key, ch in GLYPHS.items():
        if ch not in cache:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            box = d.textbbox((0, 0), ch, font=font)
            d.text(((64 - (box[2] - box[0])) / 2 - box[0], (64 - (box[3] - box[1])) / 2 - box[1]), ch,
                   font=font, fill=ICON_COLOR)
            p = folder / f"g{ord(ch):04x}.ico"
            img.save(p, "ICO", sizes=[(16, 16), (20, 20), (24, 24), (32, 32)])
            cache[ch] = str(p)
        out[key] = cache[ch]
    return out


def manifest_lines(command: str, icon: str | None, icons: dict[str, str] | None = None) -> list[str]:
    """Menu description for the native handler (format documented in native/fwshell.cpp).

    File types with identical menus share one block, which keeps the file small."""
    icons = icons or {}

    def block(ext: str) -> list[str]:
        ops = for_ext(ext) if ext != ANY_FILE else [op for op in OPERATIONS if ANY_FILE in op.exts]
        lines: list[str] = []
        open_path: tuple[str, ...] = ()
        prev_group = None
        for op in ops:
            # top-level section change -> separator (only when we're back at the top level)
            common = 0
            while common < min(len(open_path), len(op.menu)) and open_path[common] == op.menu[common]:
                common += 1
            for _ in range(len(open_path) - common):
                lines.append("end")
            if common == 0 and prev_group is not None and op.group != prev_group:
                lines.append("sep")
            for d in range(common, len(op.menu)):
                ico = icons.get(op.menu[d]) if d == 0 else None
                lines.append(f"menu\t{op.menu[d]}" + (f"\t{ico}" if ico else ""))
            open_path = op.menu
            if common == 0 or not op.menu:
                prev_group = op.group
            flags = ("m" if op.multi else "") + ("x" if op.mixed else "")
            line = f"item\t{op.id}\t{op.label_for(ext)}\t{flags}"
            mixed = op.label if op.label_for(ext) != op.label else ""  # "Merge…" -> generic when mixed
            ico = icons.get(op.id) if not op.menu else None
            if mixed or ico:
                line += f"\t{mixed}"
            if ico:
                line += f"\t{ico}"
            lines.append(line)
        lines += ["end"] * len(open_path)
        return lines

    groups: dict[tuple[str, ...], list[str]] = {}
    for ext in [*ALL_EXTS, DIR, ANY_FILE, BACKGROUND]:
        body = tuple(block(ext))
        if body:
            groups.setdefault(body, []).append(ext)
    out = [f"command\t{command}"]
    if icon:
        out.append(f"icon\t{icon}")
    for body, exts in groups.items():
        out.append("type\t" + "\t".join(exts))
        out.extend(body)
        out.append("endtype")
    return out


def write_manifest(command: str, icon: str | None, folder: Path | None = None) -> Path:
    """Per-user: %LOCALAPPDATA%\\FileWizard. All-users: next to the DLL (the DLL looks there first).
    Icons go beside the manifest so every user account can read them."""
    folder = folder or app_dir()
    p = folder / "menu.tsv"
    icons = write_menu_icons(folder / "icons")
    if icon and folder != app_dir():
        import shutil

        shutil.copyfile(icon, folder / "filewizard.ico")
        icon = str(folder / "filewizard.ico")
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(manifest_lines(command, icon, icons)) + "\n", encoding="utf-8")
    tmp.replace(p)  # atomic: Explorer never reads half a file
    return p


def find_native_dll() -> Path | None:
    """The shell extension DLL: next to the frozen exe, $FILEWIZARD_NATIVE_DLL, or in the package."""
    import os

    env = os.environ.get("FILEWIZARD_NATIVE_DLL")
    candidates = [Path(env)] if env else []
    here = Path(sys.executable).parent if frozen() else Path(__file__).parent / "native"
    candidates += sorted(here.glob("fwshell*.dll"), key=lambda p: p.stat().st_mtime, reverse=True)
    return next((c for c in candidates if c.exists()), None)


def _machine_wide() -> bool:
    """Register for all users (HKLM, needs admin). Measured in CI: the shell reliably loads
    machine-wide handlers; per-user ones weren't picked up. The installer uses this mode and
    leaves a marker file next to the exe so later repairs/uninstalls use it too."""
    import os

    if os.environ.get("FILEWIZARD_REGISTER_MACHINE") == "1":
        return True
    return frozen() and (Path(sys.executable).parent / "machine-install").exists()


def _install_native(winreg, dll: Path, command: str, icon: str | None) -> None:
    machine = _machine_wide()
    hk = winreg.HKEY_LOCAL_MACHINE if machine else winreg.HKEY_CURRENT_USER
    write_manifest(command, icon, dll.parent if machine else None)
    if machine:  # a per-user manifest would never be read; avoid confusion
        try:
            (app_dir() / "menu.tsv").unlink()
        except OSError:
            pass
    with winreg.CreateKeyEx(hk, rf"Software\Classes\CLSID\{CLSID}", 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f"{APP_NAME} context menu")
    with winreg.CreateKeyEx(hk, rf"Software\Classes\CLSID\{CLSID}\InprocServer32", 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(dll))
        winreg.SetValueEx(k, "ThreadingModel", 0, winreg.REG_SZ, "Apartment")
    for parent in HANDLER_PARENTS:
        with winreg.CreateKeyEx(hk, rf"{parent}\{APP_NAME}", 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, CLSID)
    # Consulted when the "approved extensions only" policy is on (and, for elevated shells, from
    # the machine hive); harmless otherwise.
    for hive in {hk, winreg.HKEY_CURRENT_USER}:
        with winreg.CreateKeyEx(hive, r"Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved",
                                0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, CLSID, 0, winreg.REG_SZ, f"{APP_NAME} context menu")


def _uninstall_native(winreg) -> bool:
    found = False
    hives = [winreg.HKEY_CURRENT_USER] + ([winreg.HKEY_LOCAL_MACHINE] if _machine_wide() else [])
    for hk in hives:
        for path in [rf"{p}\{APP_NAME}" for p in HANDLER_PARENTS] + [rf"Software\Classes\CLSID\{CLSID}"]:
            try:
                winreg.OpenKey(hk, path).Close()
                found = True
            except FileNotFoundError:
                continue
            _delete_tree(winreg, hk, path)
    for hk in hives:
        try:
            with winreg.OpenKey(hk, r"Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved", 0,
                                winreg.KEY_ALL_ACCESS) as k:
                winreg.DeleteValue(k, CLSID)
        except OSError:
            pass
    return found


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
    paths += [_menu_root(DIR), _menu_root(ANY_FILE), _menu_root(BACKGROUND)]
    for path in paths:
        try:
            winreg.OpenKey(hk, path).Close()
        except FileNotFoundError:
            continue
        _delete_tree(winreg, hk, path)
        removed += 1
    if _uninstall_native(winreg):
        removed += 1
    try:
        (app_dir() / "menu.tsv").unlink()
    except OSError:
        pass
    _refresh_explorer()
    if not quiet:
        print(f"Removed the {APP_NAME} menu.")
    return removed


def _is_store_python() -> bool:
    exe = sys.executable.lower()
    return "windowsapps" in exe or "pythonsoftwarefoundation" in exe


def _say(msg: str) -> None:
    """Print and log: the installer runs us hidden, so the log is how problems get diagnosed."""
    from .util import setup_logging

    print(msg)
    setup_logging().info("install: %s", msg)


def install(mode: str = "auto") -> str:
    """mode: auto (native DLL if available), native, or classic. Returns the mode used."""
    winreg = _winreg()
    _say(f"install mode={mode} frozen={frozen()} machine={_machine_wide()} exe={sys.executable}")
    if _is_store_python() and not frozen():
        # Store apps get a private, virtualized copy of HKCU and AppData: our writes would
        # "succeed" but Explorer would never see them.
        raise SystemExit(
            "This is the Microsoft Store version of Python, which is sandboxed: Explorer can't see\n"
            "menu entries it creates. Install Python from https://www.python.org/downloads/ instead\n"
            "(tick 'Add python.exe to PATH'), then run install.bat again."
        )
    if mode == "auto":
        from .settings import load

        mode = "classic" if load().get("menu") == "classic" else "auto"
    uninstall(quiet=True)  # start clean so renamed/removed actions don't linger
    launcher = None if frozen() else write_launcher()
    icon = write_icon()
    dll = find_native_dll() if mode in ("auto", "native") else None
    if mode == "native" and not dll:
        raise SystemExit("Native menu DLL (fwshell.dll) not found. Use the installer build, or set FILEWIZARD_NATIVE_DLL.")
    if dll:
        try:
            _install_native(winreg, dll, command_prefix(launcher, "--native"), icon)
        except PermissionError as e:
            raise SystemExit("FileWizard is installed for all users: repairing the menu needs admin rights. "
                             "Right-click 'FileWizard settings' > Run as administrator, or re-run the installer.") from e
        _refresh_explorer()
        _say(f"Installed the full {APP_NAME} menu (native handler: {dll}).")
        used = "native"
    else:
        from .settings import hidden_actions

        entries = build_entries(command_prefix(launcher), icon, hidden=hidden_actions())
        hk = winreg.HKEY_CURRENT_USER
        types = {REG_SZ: winreg.REG_SZ, REG_DWORD: winreg.REG_DWORD}
        for key, vals in entries:
            with winreg.CreateKeyEx(hk, key, 0, winreg.KEY_WRITE) as k:
                for name, (typ, val) in vals.items():
                    winreg.SetValueEx(k, name, 0, types[typ], val)
        _refresh_explorer()
        _say(f"Installed the {APP_NAME} menu for {len(ALL_EXTS)} file types (classic mode: "
             "Windows limits these menus to 16 entries; 'All actions...' has the rest).")
        used = "classic"
    print("Right-click a file, then FileWizard. On Windows 11 it's under 'Show more options' (or Shift+right-click).")
    return used


def render_menu(ext: str) -> str:
    """Text picture of the FileWizard submenu for one extension, exactly as registered."""
    ext = {"folder": DIR, "dir": DIR, "any": ANY_FILE, "background": BACKGROUND}.get(ext.lower(), ext)
    ext = ext if ext in (DIR, ANY_FILE, BACKGROUND) or ext.startswith(".") else "." + ext
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


# ---------- Windows 11: always show the full menu (opt-in) ----------

_WIN11_CLASSIC_KEY = r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"


def is_windows_11() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


def win11_full_menu_enabled() -> bool:
    if not is_windows_11():
        return False
    winreg = _winreg()
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN11_CLASSIC_KEY).Close()
        return True
    except FileNotFoundError:
        return False


def set_win11_full_menu(enable: bool) -> None:
    """Windows 11 only, per user, reversible: make right-click show the full menu directly
    (no "Show more options"), so FileWizard is one click away. Takes effect after Explorer restarts."""
    if not is_windows_11():
        raise SystemExit("This setting only applies to Windows 11.")
    winreg = _winreg()
    if enable:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _WIN11_CLASSIC_KEY, 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "")  # empty default value = use the classic menu
    else:
        _delete_tree(winreg, winreg.HKEY_CURRENT_USER, _WIN11_CLASSIC_KEY.rsplit("\\", 1)[0])
    _say(f"Windows 11 full right-click menu {'on' if enable else 'off'} (restart Explorer to apply)")


def restart_explorer() -> None:
    import subprocess

    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True)
    subprocess.Popen(["explorer.exe"])
