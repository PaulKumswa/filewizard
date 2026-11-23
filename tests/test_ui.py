"""Open every dialog for real (needs a display: CI uses Xvfb), auto-close it, assert no errors.

Runs each dialog in light and dark themes. Skipped where Tk or a display isn't available.
"""

import importlib
import os
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

try:
    _r = tk.Tk()
    _r.destroy()
except Exception:  # no display
    pytest.skip("no display for Tk", allow_module_level=True)


@pytest.fixture(params=["light", "dark"])
def ui(request, monkeypatch, tmp_path):
    monkeypatch.setenv("FILEWIZARD_THEME", request.param)
    monkeypatch.delenv("FILEWIZARD_NONINTERACTIVE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    import filewizard.ui as module

    module = importlib.reload(module)  # re-read the theme
    errors = []
    orig_init = tk.Tk.__init__

    def auto_close(self, *a, **k):
        orig_init(self, *a, **k)
        self.report_callback_exception = lambda *exc: errors.append(exc)
        self.after(400, self.destroy)

    monkeypatch.setattr(tk.Tk, "__init__", auto_close)
    yield module
    assert not errors, errors


def _pdf(path: Path, pages=3) -> Path:
    import pymupdf

    d = pymupdf.open()
    for i in range(pages):
        d.new_page().insert_text((72, 72), f"Page {i + 1}")
    d.save(path)
    return path


def test_palette(ui, tmp_path):
    from filewizard import operations

    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    assert ui.palette([f], operations.for_selection([f])) is None  # closed without choosing


def test_order_dialog(ui, tmp_path):
    files = [tmp_path / f"{i}.pdf" for i in range(3)]
    assert ui.order_files(files, "Merge", add_types={".pdf"}) is None


def test_ask_string(ui):
    assert ui.ask_string("Pages?") is None


def test_page_picker(ui, tmp_path):
    assert ui.pick_pages(_pdf(tmp_path / "p.pdf", 6), "delete") is None


def test_signature_placement(ui, tmp_path):
    from PIL import Image

    from filewizard.pdf import signature_png

    sig = tmp_path / "s.png"
    Image.new("RGB", (200, 80), "white").save(sig)
    assert ui.place_signature(_pdf(tmp_path / "c.pdf"), signature_png(sig)) is None


def test_text_window(ui):
    ui.show_text("Checksum", "a.txt\n  SHA-256: 00ff")


def test_toast(ui, tmp_path):
    out = tmp_path / "done.png"
    out.write_bytes(b"x")
    ui.notify_done([out])


def test_progress_window(ui):
    from filewizard.util import PROGRESS

    def work():
        for i in range(12):
            PROGRESS.report(i / 12, "working")
            time.sleep(0.1)
        return 42

    try:
        assert ui.run_with_progress("Testing", work) == 42
    finally:
        PROGRESS.fraction, PROGRESS.text, PROGRESS.cancelled = None, "", False


def test_settings_window(ui, tmp_path):
    ui.settings_window(lambda: "ok", lambda: None, lambda: None, tmp_path / "x.log")


def test_message_box_noninteractive(monkeypatch, capsys):
    monkeypatch.setenv("FILEWIZARD_NONINTERACTIVE", "1")
    import filewizard.ui as module

    module.message("hello", error=True)
    assert "hello" in capsys.readouterr().err


@pytest.fixture(autouse=True, scope="module")
def _restore_theme():
    yield
    os.environ.pop("FILEWIZARD_THEME", None)
    import filewizard.ui as module

    importlib.reload(module)
