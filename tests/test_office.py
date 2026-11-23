"""Office documents -> PDF. Needs Microsoft Office or LibreOffice; skipped otherwise
(CI installs LibreOffice on Windows and sets FILEWIZARD_REQUIRE_OFFICE=1 so it can't skip)."""

import os

import pytest

os.environ["FILEWIZARD_NONINTERACTIVE"] = "1"

import pymupdf  # noqa: E402

from filewizard import office  # noqa: E402
from filewizard.cli import main  # noqa: E402

if not office.find_soffice() and not office._com_available():
    if os.environ.get("FILEWIZARD_REQUIRE_OFFICE") == "1":
        raise RuntimeError("Office/LibreOffice required but not found:\n" + office.backend_report())
    pytestmark = pytest.mark.skip(reason="no Office or LibreOffice")


def run(*args) -> int:
    return main(["run", *map(str, args)])


def _docx(path, text):
    import docx

    d = docx.Document()
    d.add_heading(text, 1)
    d.add_paragraph("Body paragraph with ünïcödé.")
    d.save(path)
    return path


def _xlsx(path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["Item", "Cost"])
    wb.active.append(["Coffee", 3.5])
    wb.save(path)
    return path


def test_word_to_pdf(tmp_path):
    src = _docx(tmp_path / "Memo.docx", "Quarterly memo")
    assert run("office-to-pdf", src) == 0
    with pymupdf.open(tmp_path / "Memo.pdf") as d:
        assert "Quarterly memo" in d[0].get_text()


def test_excel_to_pdf(tmp_path):
    assert run("office-to-pdf", _xlsx(tmp_path / "Budget.xlsx")) == 0
    with pymupdf.open(tmp_path / "Budget.pdf") as d:
        assert "Coffee" in d[0].get_text()


def test_combine_word_and_pdf(tmp_path):
    a = _docx(tmp_path / "a.docx", "First doc")
    b = tmp_path / "b.pdf"
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), "Second doc")
    d.save(b)
    assert run("combine-pdf", a, b) == 0
    with pymupdf.open(tmp_path / "a (combined).pdf") as out:
        assert "First doc" in out[0].get_text() and "Second doc" in out[-1].get_text()


def test_backend_report():
    assert "LibreOffice" in office.backend_report()


def test_slides_to_png(tmp_path):
    pptx = pytest.importorskip("pptx")
    prs = pptx.Presentation()
    for title in ("One", "Two"):
        s = prs.slides.add_slide(prs.slide_layouts[0])
        s.shapes.title.text = title
    src = tmp_path / "Deck.pptx"
    prs.save(src)
    assert run("office-to-png", src) == 0
    assert len(list((tmp_path / "Deck (png)").glob("*.png"))) == 2
    assert not list(tmp_path.glob("*.pdf"))  # no stray temp PDF
