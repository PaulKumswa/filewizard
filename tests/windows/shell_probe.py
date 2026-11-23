"""Drive the real Windows shell context menu, the way Explorer does.

Builds the context menu for a selection of files through IShellFolder/IContextMenu
(the same COM objects Explorer uses), dumps the FileWizard submenu, and "clicks"
items by invoking their command IDs. Used by CI on windows-latest.

    python tests/windows/shell_probe.py dump  FILE [FILE...]
    python tests/windows/shell_probe.py click "Convert to/PNG" FILE [FILE...]
"""

from __future__ import annotations

import os
import sys

import pythoncom
import win32con
import win32gui
import win32gui_struct
from win32com.shell import shell, shellcon

ID_FIRST, ID_LAST = 1, 0x7FFF


def _context_menu(paths: list[str]):
    folder_path = os.path.dirname(os.path.abspath(paths[0]))
    desktop = shell.SHGetDesktopFolder()
    _, folder_pidl, _ = desktop.ParseDisplayName(0, None, folder_path)
    folder = desktop.BindToObject(folder_pidl, None, shell.IID_IShellFolder)
    pidls = [folder.ParseDisplayName(0, None, os.path.basename(p))[1] for p in paths]
    res = folder.GetUIObjectOf(0, pidls, shell.IID_IContextMenu, 0)
    cm = res[1] if isinstance(res, tuple) else res
    hmenu = win32gui.CreatePopupMenu()
    cm.QueryContextMenu(hmenu, 0, ID_FIRST, ID_LAST, shellcon.CMF_NORMAL)
    # Ask handlers to fill lazily-built submenus, like Explorer does on hover.
    try:
        cm3 = cm.QueryInterface(shell.IID_IContextMenu3)
    except Exception:
        cm3 = None
    return cm, cm3, hmenu


def _items(hmenu, cm3=None):
    out = []
    for pos in range(win32gui.GetMenuItemCount(hmenu)):
        buf, _ = win32gui_struct.EmptyMENUITEMINFO(
            win32con.MIIM_STRING | win32con.MIIM_SUBMENU | win32con.MIIM_ID | win32con.MIIM_FTYPE)
        win32gui.GetMenuItemInfo(hmenu, pos, True, buf)
        info = win32gui_struct.UnpackMENUITEMINFO(buf)
        if info.fType & win32con.MFT_SEPARATOR:
            out.append(("---", None, None))
            continue
        sub = info.hSubMenu or None
        if sub and cm3 is not None:
            try:
                cm3.HandleMenuMsg2(win32con.WM_INITMENUPOPUP, sub, pos)
            except Exception:
                pass
        out.append(((info.text or "").replace("&", ""), info.wID, sub))
    return out


def tree(hmenu, cm3=None, depth=0, lines=None):
    lines = [] if lines is None else lines
    for text, wid, sub in _items(hmenu, cm3):
        lines.append("  " * depth + (text + (" >" if sub else "")))
        if sub:
            tree(sub, cm3, depth + 1, lines)
    return lines


def find(hmenu, path: list[str], cm3=None):
    for text, wid, sub in _items(hmenu, cm3):
        if text.strip() == path[0]:
            if len(path) == 1:
                return wid, sub
            if sub:
                return find(sub, path[1:], cm3)
    return None, None


def fw_menu(paths):
    cm, cm3, hmenu = _context_menu(paths)
    _, sub = find(hmenu, ["FileWizard"], cm3)
    return cm, cm3, sub


def dump(paths) -> list[str]:
    cm, cm3, sub = fw_menu(paths)
    if not sub:
        cm_, cm3_, hmenu = _context_menu(paths)
        raise SystemExit("FileWizard not in context menu. Top level was:\n" + "\n".join(tree(hmenu, cm3_)))
    return tree(sub, cm3)


def click(label_path: str, paths) -> None:
    cm, cm3, sub = fw_menu(paths)
    if not sub:
        raise SystemExit("FileWizard not in context menu")
    wid, _ = find(sub, label_path.split("/"), cm3)
    if wid is None:
        raise SystemExit(f"'{label_path}' not found. Menu:\n" + "\n".join(tree(sub, cm3)))
    # CMINVOKECOMMANDINFO: fMask, hwnd, verb (offset), params, dir, nShow, hotkey, hicon
    cm.InvokeCommand((0, 0, wid - ID_FIRST, None, None, win32con.SW_SHOWNORMAL, 0, 0))


if __name__ == "__main__":
    pythoncom.CoInitialize()
    cmd, *rest = sys.argv[1:]
    if cmd == "dump":
        print("\n".join(dump(rest)))
    elif cmd == "click":
        click(rest[0], rest[1:])
    else:
        raise SystemExit(__doc__)
