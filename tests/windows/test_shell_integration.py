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

    assert shell_menu.install(MODE) == MODE
    yield
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
    assert "Merge" in text and "Compress" in text


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
