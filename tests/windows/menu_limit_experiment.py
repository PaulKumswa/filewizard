"""Experiment: how many registry-defined cascade items does Windows show, per layout?

Registers throwaway menus on a fake extension, reads them back through the shell, prints
counts, cleans up. Run in CI (never fails the build): python tests/windows/menu_limit_experiment.py
"""

import os
import sys
import tempfile
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import shell_probe  # noqa: E402

HK = winreg.HKEY_CURRENT_USER
CLS = r"Software\Classes"


def setkey(path, values):
    with winreg.CreateKeyEx(HK, path, 0, winreg.KEY_WRITE) as k:
        for name, val in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)


def verb(parent, i, label):
    setkey(rf"{parent}\shell\{i:03d}", {"MUIVerb": label})
    setkey(rf"{parent}\shell\{i:03d}\command", {"": "cmd /c exit"})


def delete_tree(path):
    try:
        with winreg.OpenKey(HK, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    child = winreg.EnumKey(k, 0)
                except OSError:
                    break
                delete_tree(rf"{path}\{child}")
        winreg.DeleteKey(HK, path)
    except FileNotFoundError:
        pass


def count(ext: str, label: str):
    d = tempfile.mkdtemp()
    f = os.path.join(d, "x" + ext)
    open(f, "w").close()
    cm = shell_probe.ContextMenu([f])
    _, sub = cm.find([label])
    if not sub:
        return "NOT SHOWN"
    lines = cm.tree(sub)
    leaves = [ln for ln in lines if not ln.rstrip().endswith(">") and ln.strip() != "---"]
    return f"{len(leaves)} leaves shown ({len(lines)} lines)"


def main():
    results = {}
    # A: flat 30 items under SubCommands=""
    base = rf"{CLS}\SystemFileAssociations\.fwa\shell\TestA"
    setkey(base, {"MUIVerb": "TestA", "SubCommands": ""})
    for i in range(30):
        verb(base, i, f"A{i}")
    results["A flat 30 (SubCommands)"] = count(".fwa", "TestA")

    # B: 3 submenus x 10
    base = rf"{CLS}\SystemFileAssociations\.fwb\shell\TestB"
    setkey(base, {"MUIVerb": "TestB", "SubCommands": ""})
    for m in range(3):
        sm = rf"{base}\shell\{m:03d}"
        setkey(sm, {"MUIVerb": f"Menu{m}", "SubCommands": ""})
        for i in range(10):
            verb(sm, i, f"B{m}.{i}")
    results["B nested 3x10 (SubCommands)"] = count(".fwb", "TestB")

    # C: ExtendedSubCommandsKey with 30 items
    store = rf"{CLS}\FWTest.C"
    for i in range(30):
        verb(store, i, f"C{i}")
    setkey(rf"{CLS}\SystemFileAssociations\.fwc\shell\TestC", {"MUIVerb": "TestC", "ExtendedSubCommandsKey": "FWTest.C"})
    results["C flat 30 (ExtendedSubCommandsKey)"] = count(".fwc", "TestC")

    # D: ExtendedSubCommandsKey, each submenu its own ExtendedSubCommandsKey (3 x 10)
    for m in range(3):
        sub = rf"{CLS}\FWTest.D{m}"
        for i in range(10):
            verb(sub, i, f"D{m}.{i}")
        setkey(rf"{CLS}\FWTest.D\shell\{m:03d}", {"MUIVerb": f"Menu{m}", "ExtendedSubCommandsKey": f"FWTest.D{m}"})
    setkey(rf"{CLS}\SystemFileAssociations\.fwd\shell\TestD", {"MUIVerb": "TestD", "ExtendedSubCommandsKey": "FWTest.D"})
    results["D nested 3x10 (ExtendedSubCommandsKey each)"] = count(".fwd", "TestD")

    print("\n=== cascade limit experiment ===")
    for k, v in results.items():
        print(f"{k:<48} {v}")

    for ext, name in ((".fwa", "TestA"), (".fwb", "TestB"), (".fwc", "TestC"), (".fwd", "TestD")):
        delete_tree(rf"{CLS}\SystemFileAssociations\{ext}")
    for k in ("FWTest.C", "FWTest.D", "FWTest.D0", "FWTest.D1", "FWTest.D2"):
        delete_tree(rf"{CLS}\{k}")


if __name__ == "__main__":
    main()
