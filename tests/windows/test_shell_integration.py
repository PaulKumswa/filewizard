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

if sys.platform == "win32":
    import pythoncom

    sys.path.insert(0, str(Path(__file__).parent))
    import shell_probe


@pytest.fixture(scope="module", autouse=True)
def installed():
    os.environ["FILEWIZARD_NONINTERACTIVE"] = "1"  # inherited by processes the shell launches
    os.environ["FILEWIZARD_NO_NOTIFY"] = "1"
    from filewizard import shell_menu

    pythoncom.CoInitialize()
    shell_menu.install()
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
