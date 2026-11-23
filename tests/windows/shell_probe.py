"""Drive the real Windows shell context menu, the way Explorer does.

Builds the context menu for a selection of files through IShellFolder/IContextMenu
(the same COM objects Explorer uses), "hovers" submenus by sending WM_INITMENUPOPUP
through IContextMenu3 (cascades are filled lazily, exactly like on screen), dumps the
FileWizard submenu, and "clicks" items via InvokeCommand. Pure ctypes: no pywin32 needed.

    python tests/windows/shell_probe.py dump  FILE [FILE...]
    python tests/windows/shell_probe.py click "Convert to/PNG" FILE [FILE...]
"""

from __future__ import annotations

import ctypes
import os
import sys
import uuid
from ctypes import POINTER, byref, c_int, c_uint, c_void_p, c_wchar_p, wintypes

ole32 = ctypes.windll.ole32
shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32

LRESULT = ctypes.c_ssize_t
WPARAM, LPARAM = ctypes.c_size_t, ctypes.c_ssize_t
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
user32.GetSubMenu.argtypes = [wintypes.HMENU, c_int]
user32.GetSubMenu.restype = wintypes.HMENU

ID_FIRST, ID_LAST = 1, 0x7FFF
CMF_NORMAL = 0
WM_INITMENUPOPUP = 0x0117
MIIM_ID, MIIM_SUBMENU, MIIM_STRING, MIIM_FTYPE = 0x2, 0x4, 0x40, 0x100
MFT_SEPARATOR = 0x800
CMIC_MASK_FLAG_NO_UI, CMIC_MASK_NOASYNC = 0x400, 0x100
SW_SHOWNORMAL = 1


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, s: str) -> "GUID":
        u = uuid.UUID(s)
        g = cls(u.fields[0], u.fields[1], u.fields[2])
        ctypes.memmove(g.Data4, u.bytes[8:], 8)
        return g


IID_IShellFolder = GUID.of("000214E6-0000-0000-C000-000000000046")
IID_IContextMenu = GUID.of("000214E4-0000-0000-C000-000000000046")
IID_IContextMenu3 = GUID.of("BCFCE0A0-EC17-11D0-8D10-00A0C90F2719")
IID_IContextMenu2 = GUID.of("000214F4-0000-0000-C000-000000000046")


class MENUITEMINFOW(ctypes.Structure):
    _fields_ = [("cbSize", c_uint), ("fMask", c_uint), ("fType", c_uint), ("fState", c_uint),
                ("wID", c_uint), ("hSubMenu", wintypes.HMENU), ("hbmpChecked", wintypes.HBITMAP),
                ("hbmpUnchecked", wintypes.HBITMAP), ("dwItemData", ctypes.c_size_t),
                ("dwTypeData", c_wchar_p), ("cch", c_uint), ("hbmpItem", wintypes.HBITMAP)]


class CMINVOKECOMMANDINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.DWORD), ("hwnd", wintypes.HWND),
                ("lpVerb", c_void_p), ("lpParameters", ctypes.c_char_p), ("lpDirectory", ctypes.c_char_p),
                ("nShow", c_int), ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE)]


def _method(obj: c_void_p, index: int, restype, *argtypes):
    """Fetch method `index` from a COM object's vtable."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return lambda *args: proto(vtbl[index])(obj, *args)


def _check(hr: int, what: str):
    if hr < 0:
        raise OSError(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


class ContextMenu:
    def __init__(self, paths: list[str], background: bool = False):
        ole32.CoInitialize(None)
        if background:  # menu for empty space inside the folder paths[0]
            self._init_background(os.path.abspath(paths[0]))
            return
        paths = [os.path.abspath(p) for p in paths]
        shell32.SHParseDisplayName.argtypes = [c_wchar_p, c_void_p, POINTER(c_void_p), c_uint, POINTER(c_uint)]
        shell32.SHBindToParent.argtypes = [c_void_p, POINTER(GUID), POINTER(c_void_p), POINTER(c_void_p)]
        abs_pidls = []
        for p in paths:
            pidl = c_void_p()
            _check(shell32.SHParseDisplayName(p, None, byref(pidl), 0, None), f"SHParseDisplayName({p})")
            abs_pidls.append(pidl)
        folder = c_void_p()
        last = c_void_p()
        _check(shell32.SHBindToParent(abs_pidls[0], byref(IID_IShellFolder), byref(folder), byref(last)),
               "SHBindToParent")
        children = (c_void_p * len(paths))()
        shell32.ILFindLastID.argtypes = [c_void_p]
        shell32.ILFindLastID.restype = c_void_p
        for i, pidl in enumerate(abs_pidls):
            children[i] = shell32.ILFindLastID(pidl)
        self.cm = c_void_p()
        get_ui = _method(folder, 10, ctypes.c_long,
                         wintypes.HWND, c_uint, c_void_p, POINTER(GUID), c_void_p, POINTER(c_void_p))
        _check(get_ui(None, len(paths), children, byref(IID_IContextMenu), None, byref(self.cm)), "GetUIObjectOf")
        self.hmenu = user32.CreatePopupMenu()
        qcm = _method(self.cm, 3, ctypes.c_long, wintypes.HMENU, c_uint, c_uint, c_uint, c_uint)
        _check(qcm(self.hmenu, 0, ID_FIRST, ID_LAST, CMF_NORMAL), "QueryContextMenu")
        self.cm3 = c_void_p()
        qi = _method(self.cm, 0, ctypes.c_long, POINTER(GUID), POINTER(c_void_p))
        if qi(byref(IID_IContextMenu3), byref(self.cm3)) < 0:
            self.cm3 = None
        self._keep = (abs_pidls, folder, children)

    def _init_background(self, folder_path: str):
        shell32.SHParseDisplayName.argtypes = [c_wchar_p, c_void_p, POINTER(c_void_p), c_uint, POINTER(c_uint)]
        shell32.SHGetDesktopFolder.argtypes = [POINTER(c_void_p)]
        pidl = c_void_p()
        _check(shell32.SHParseDisplayName(folder_path, None, byref(pidl), 0, None), "SHParseDisplayName")
        desktop = c_void_p()
        _check(shell32.SHGetDesktopFolder(byref(desktop)), "SHGetDesktopFolder")
        bind = _method(desktop, 5, ctypes.c_long, c_void_p, c_void_p, POINTER(GUID), POINTER(c_void_p))
        folder = c_void_p()
        _check(bind(pidl, None, byref(IID_IShellFolder), byref(folder)), "BindToObject")
        create_view = _method(folder, 8, ctypes.c_long, wintypes.HWND, POINTER(GUID), POINTER(c_void_p))
        self.cm = c_void_p()
        _check(create_view(None, byref(IID_IContextMenu), byref(self.cm)), "CreateViewObject(IContextMenu)")
        self.hmenu = user32.CreatePopupMenu()
        qcm = _method(self.cm, 3, ctypes.c_long, wintypes.HMENU, c_uint, c_uint, c_uint, c_uint)
        _check(qcm(self.hmenu, 0, ID_FIRST, ID_LAST, CMF_NORMAL), "QueryContextMenu")
        self.cm3 = c_void_p()
        qi = _method(self.cm, 0, ctypes.c_long, POINTER(GUID), POINTER(c_void_p))
        if qi(byref(IID_IContextMenu3), byref(self.cm3)) < 0:
            self.cm3 = None
        self._keep = (pidl, desktop, folder)

    def _hover(self, hsub, pos: int):
        """What Explorer does when the mouse rests on a cascade: lets the handler fill it.
        Uses IContextMenu3::HandleMenuMsg2, or IContextMenu2::HandleMenuMsg when that's all there is."""
        if self.cm3:
            handle2 = _method(self.cm3, 7, ctypes.c_long, c_uint, WPARAM, LPARAM, POINTER(LRESULT))
            res = LRESULT()
            handle2(WM_INITMENUPOPUP, hsub, pos & 0xFFFF, byref(res))
            return
        if getattr(self, "cm2", None) is None:
            self.cm2 = c_void_p()
            qi = _method(self.cm, 0, ctypes.c_long, POINTER(GUID), POINTER(c_void_p))
            if qi(byref(IID_IContextMenu2), byref(self.cm2)) < 0:
                self.cm2 = False
        if self.cm2:
            handle = _method(self.cm2, 6, ctypes.c_long, c_uint, WPARAM, LPARAM)
            handle(WM_INITMENUPOPUP, hsub, pos & 0xFFFF)

    def items(self, hmenu=None):
        hmenu = hmenu or self.hmenu
        out = []
        for pos in range(user32.GetMenuItemCount(hmenu)):
            buf = ctypes.create_unicode_buffer(512)
            mii = MENUITEMINFOW(cbSize=ctypes.sizeof(MENUITEMINFOW),
                                fMask=MIIM_ID | MIIM_SUBMENU | MIIM_STRING | MIIM_FTYPE,
                                dwTypeData=ctypes.cast(buf, c_wchar_p), cch=511)
            if not user32.GetMenuItemInfoW(hmenu, pos, True, byref(mii)):
                continue
            if mii.fType & MFT_SEPARATOR:
                out.append(("---", None, None))
                continue
            sub = mii.hSubMenu or None
            if sub:
                self._hover(sub, pos)
            # display text: '&&' is a literal ampersand, a single '&' marks the shortcut key
            shown = buf.value.replace("&&", "\0").replace("&", "").replace("\0", "&")
            out.append((shown, mii.wID, sub))
        return out

    def tree(self, hmenu=None, depth=0, lines=None):
        lines = [] if lines is None else lines
        for text, wid, sub in self.items(hmenu):
            lines.append("  " * depth + text + (" >" if sub else ""))
            if sub:
                self.tree(sub, depth + 1, lines)
        return lines

    def find(self, path: list[str], hmenu=None):
        def norm(t: str) -> str:  # tolerate "…" vs "..." vs nothing (command lines mangle "…")
            return t.strip().rstrip(".…").strip()

        for text, wid, sub in self.items(hmenu):
            if norm(text) == norm(path[0]):
                if len(path) == 1:
                    return wid, sub
                if sub:
                    return self.find(path[1:], sub)
        return None, None

    def invoke(self, wid: int):
        info = CMINVOKECOMMANDINFO(cbSize=ctypes.sizeof(CMINVOKECOMMANDINFO),
                                   fMask=CMIC_MASK_FLAG_NO_UI | CMIC_MASK_NOASYNC,
                                   lpVerb=wid - ID_FIRST, nShow=SW_SHOWNORMAL)
        inv = _method(self.cm, 4, ctypes.c_long, POINTER(CMINVOKECOMMANDINFO))
        _check(inv(byref(info)), "InvokeCommand")


# ---------- the API the tests use ----------

def top_level(paths) -> list[str]:
    return [t for t, _, _ in ContextMenu(paths).items()]


def dump(paths) -> list[str]:
    cm = ContextMenu(paths)
    _, sub = cm.find(["FileWizard"])
    if not sub:
        raise SystemExit("FileWizard not in context menu. Top level was:\n" + "\n".join(cm.tree()))
    return cm.tree(sub)


def click(label_path: str, paths) -> None:
    cm = ContextMenu(paths)
    wid, _ = cm.find(["FileWizard", *label_path.split("/")])
    if wid is None:
        _, sub = cm.find(["FileWizard"])
        raise SystemExit(f"'{label_path}' not found. Menu:\n" + "\n".join(cm.tree(sub) if sub else cm.tree()))
    cm.invoke(wid)


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd == "dump":
        print("\n".join(dump(rest)))
    elif cmd == "click":
        click(rest[0], rest[1:])
    else:
        raise SystemExit(__doc__)
