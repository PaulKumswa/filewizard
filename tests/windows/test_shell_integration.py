"""End-to-end on real Windows: install the menu, read it back through the shell, click items.

Runs only on Windows with FILEWIZARD_SHELL_TEST=1 (CI sets it), because it writes to the
current user's registry.
"""

import os
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("FILEWIZARD_SHELL_TEST") != "1",
    reason="Windows shell integration test (set FILEWIZARD_SHELL_TEST=1)",
)

MODE = os.environ.get("FILEWIZARD_MENU_MODE", "classic")
native_only = pytest.mark.skipif(MODE != "native", reason="native menu only")

if sys.platform == "win32":
    sys.path.insert(0, str(Path(__file__).parent))
    import shell_probe


@pytest.fixture(scope="module", autouse=True)
def installed():
    os.environ["FILEWIZARD_NONINTERACTIVE"] = "1"  # inherited by processes the shell launches
    os.environ["FILEWIZARD_NO_NOTIFY"] = "1"
    if os.environ.get("FILEWIZARD_ALREADY_INSTALLED") == "1":  # testing the real installer build
        yield
        return
    from filewizard import shell_menu

    if MODE == "native":  # turn on the DLL's diagnostics log for CI
        from filewizard.util import app_dir

        (app_dir() / "shell-debug").write_text("1")
    assert shell_menu.install(MODE) == MODE
    yield
    if MODE == "native":
        from filewizard.util import app_dir

        log = app_dir() / "shell.log"
        print("\n--- fwshell.dll log ---\n" + (log.read_text(encoding="utf-8", errors="replace")[-4000:]
                                               if log.exists() else "(no log: DLL never loaded)"))
    shell_menu.uninstall()


def _wait_for(pattern_dir: Path, glob: str, count: int = 1, timeout: float = 90):
    end = time.time() + timeout
    while time.time() < end:
        hits = list(pattern_dir.glob(glob))
        if len(hits) >= count:
            time.sleep(1.0)  # let writers finish
            return hits
        time.sleep(0.5)
    log = Path(os.environ["LOCALAPPDATA"], "FileWizard", "filewizard.log")
    tail = log.read_text(encoding="utf-8")[-3000:] if log.exists() else "(no log)"
    raise AssertionError(f"timed out waiting for {glob} in {pattern_dir}\nLOG:\n{tail}")


def _pdf(path: Path, pages=1):
    import pymupdf

    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"{path.stem} {i}")
    d.save(path)
    return str(path)


def _img(path: Path):
    from PIL import Image

    Image.new("RGB", (64, 48), (10, 120, 200)).save(path)
    return str(path)


def test_menu_shows_for_pdf(tmp_path):
    lines = shell_probe.dump([_pdf(tmp_path / "a.pdf")])
    print("\n".join(lines))
    text = "\n".join(lines)
    assert "Compress" in text
    if MODE == "classic":  # classic verbs can't see the selection; native hides Merge for one file
        assert "Merge" in text


def test_menu_shows_for_image(tmp_path):
    lines = shell_probe.dump([_img(tmp_path / "a.jpg")])
    print("\n".join(lines))
    assert any("PNG" in line for line in lines)


def test_click_single(tmp_path):
    a = _pdf(tmp_path / "a.pdf", 2)
    shell_probe.click(os.environ.get("FW_ROTATE_LABEL", "Rotate/Right (clockwise)"), [a])
    _wait_for(tmp_path, "a (rotated right).pdf")


def test_click_merge_multi(tmp_path):
    files = [_pdf(tmp_path / f"m{i}.pdf") for i in range(4)]
    shell_probe.click("Merge selected PDFs…", files)
    [out] = _wait_for(tmp_path, "*merged*.pdf")
    time.sleep(3)  # make sure no second, partial merge appears
    assert len(list(tmp_path.glob("*merged*.pdf"))) == 1
    import pymupdf

    assert pymupdf.open(out).page_count == 4


def test_click_mixed_image_types(tmp_path):
    files = [_img(tmp_path / "x.jpg"), _img(tmp_path / "y.png")]
    shell_probe.click("Convert to/WebP", files)
    _wait_for(tmp_path, "*.webp", count=2)


def test_unknown_type_gets_more_menu(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"\x00" * 10)
    lines = shell_probe.dump([str(f)])
    print("\n".join(lines))
    assert any("Checksum" in line for line in lines)


def test_known_type_has_exactly_one_filewizard(tmp_path):
    top = shell_probe.top_level([_pdf(tmp_path / "one.pdf")])
    print(top)
    assert top.count("FileWizard") == 1


def test_folder_menu(tmp_path):
    d = tmp_path / "Folder A"
    d.mkdir()
    (d / "x.txt").write_text("x")
    lines = shell_probe.dump([str(d)])
    print("\n".join(lines))
    assert any("ZIP" in line for line in lines)


# ---------- native handler: full menu, selection-aware ----------

@native_only
def test_native_full_menu_beyond_16(tmp_path):
    lines = shell_probe.dump([_pdf(tmp_path / "a.pdf")])
    print("\n".join(lines))
    assert len([ln for ln in lines if ln.strip() != "---"]) > 16
    text = "\n".join(lines)
    assert "Flatten forms" in text and "Add watermark" in text
    assert "Merge" not in text, "merge needs 2+ files, hide it for a single PDF"


@native_only
def test_native_merge_appears_for_multiple(tmp_path):
    files = [_pdf(tmp_path / f"p{i}.pdf") for i in range(2)]
    assert any("Merge selected PDFs" in ln for ln in shell_probe.dump(files))


@native_only
def test_native_mixed_selection_shows_shared_actions_only(tmp_path):
    files = [_pdf(tmp_path / "doc.pdf"), _img(tmp_path / "pic.jpg")]
    lines = shell_probe.dump(files)
    print("\n".join(lines))
    text = "\n".join(lines)
    assert "Combine selected into one PDF" in text or "Merge selected PDFs" in text
    assert "Split into single pages" not in text and "HEIC" not in text


@native_only
def test_native_click_mixed_combine(tmp_path):
    files = [_pdf(tmp_path / "doc.pdf", 2), _img(tmp_path / "pic.jpg")]
    lines = shell_probe.dump(files)
    label = next(ln.strip() for ln in lines if "selected" in ln and "PDF" in ln)
    shell_probe.click(label, files)
    [out] = _wait_for(tmp_path, "*combined*.pdf")
    import pymupdf

    assert pymupdf.open(out).page_count == 3


@native_only
def test_native_com_object_creates():
    """Create the handler directly through COM: pinpoints registration/loading problems."""
    import ctypes
    import winreg

    from filewizard.shell_menu import CLSID

    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{CLSID}\InprocServer32") as k:
        dll = winreg.QueryValue(k, "")
    print("HKCR InprocServer32 ->", dll, "exists:", os.path.exists(dll))
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"*\shellex\ContextMenuHandlers\FileWizard") as k:
        print("HKCR * handler ->", winreg.QueryValue(k, ""))
    try:
        h = ctypes.WinDLL(dll)
        print("LoadLibrary OK; DllGetClassObject:", bool(h.DllGetClassObject))
    except OSError as e:
        raise AssertionError(f"LoadLibrary failed: {e}")
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    clsid = shell_probe.GUID.of(CLSID.strip("{}"))
    ptr = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(shell_probe.IID_IContextMenu),
                                ctypes.byref(ptr))
    print(f"CoCreateInstance hr=0x{hr & 0xFFFFFFFF:08X}")
    assert hr == 0


@native_only
def test_native_ampersand_and_mixed_label(tmp_path):
    single = shell_probe.ContextMenu([_pdf(tmp_path / "one.pdf")])
    edit = single.find(["FileWizard", "Edit"])[1]
    labels = [t for t, _, _ in single.items(edit)]
    print(labels)
    assert "Black & white" in labels, labels
    lines = shell_probe.dump([_pdf(tmp_path / "doc.pdf"), _img(tmp_path / "pic.jpg")])
    assert any("Combine selected into one PDF" in ln for ln in lines), lines


@native_only
def test_native_menu_icons_rendered():
    from filewizard.shell_menu import find_native_dll

    folder = find_native_dll().parent if os.environ.get("FILEWIZARD_REGISTER_MACHINE") == "1" else None
    from filewizard.util import app_dir

    icons = list(((folder or app_dir()) / "icons").glob("*.ico"))
    print(len(icons), "menu icons")
    assert len(icons) >= 10
    manifest = ((folder or app_dir()) / "menu.tsv").read_text(encoding="utf-8")
    assert "\tConvert to\t" in manifest and ".ico" in manifest


@native_only
def test_native_soundtrack_only_for_video_plus_audio(tmp_path):
    import subprocess

    import imageio_ffmpeg

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    v1, v2, a = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "m.mp3"
    for v in (v1, v2):
        subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1", str(v)], check=True)
    subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1", str(a)], check=True)
    two_videos = "\n".join(shell_probe.dump([str(v1), str(v2)]))
    pair = "\n".join(shell_probe.dump([str(v1), str(a)]))
    assert "soundtrack" not in two_videos and "Join selected" in two_videos
    assert "soundtrack" in pair


@native_only
def test_native_recent_actions_on_top(tmp_path):
    from filewizard.util import app_dir

    (app_dir() / "recent.txt").write_text("img-to-jpg\npdf-rotate-right\n", encoding="utf-8")
    try:
        lines = shell_probe.dump([_pdf(tmp_path / "r.pdf")])
        print("\n".join(lines[:4]))
        assert lines[0].startswith("Rotate") and "Right" in lines[0], lines[:3]  # recent, with its path
        assert lines[1] == "---"
    finally:
        (app_dir() / "recent.txt").unlink()
