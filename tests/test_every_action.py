"""Run EVERY action on a sample of every file kind it accepts. An action may refuse politely
("already small", "no tables found"), but it must never hit the generic crash path."""

import glob
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

os.environ["FILEWIZARD_NONINTERACTIVE"] = "1"

import pymupdf  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from filewizard import media, operations  # noqa: E402
from filewizard.cli import main  # noqa: E402

SKIP = {"settings", "palette", "img-nobg"}  # opens windows / needs the optional AI add-on
LIBREOFFICE_ONLY = {".doc", ".docx", ".docm", ".rtf", ".odt", ".pages", ".wpd", ".wps", ".dot", ".dotx",
                    ".ppt", ".pptx", ".pptm", ".odp", ".key", ".pps", ".ppsx", ".xls", ".xlsb", ".ods",
                    ".numbers", ".pub", ".vsd", ".vsdx", ".odg"}


def _samples(d: Path) -> dict[str, Path]:
    s: dict[str, Path] = {}
    img = Image.new("RGB", (320, 200), "white")
    ImageDraw.Draw(img).rectangle((60, 40, 260, 160), fill=(30, 90, 200))
    for ext in (".jpg", ".png", ".bmp", ".tiff", ".webp", ".gif"):
        p = d / f"sample{ext}"
        img.save(p)
        s[ext] = p
    from pillow_heif import register_heif_opener

    register_heif_opener()
    img.save(d / "sample.heic")
    s[".heic"] = d / "sample.heic"
    (d / "sample.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
                                  '<rect width="80" height="40" fill="green"/></svg>')
    s[".svg"] = d / "sample.svg"
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} sample text")
    doc.save(d / "sample.pdf")
    s[".pdf"] = d / "sample.pdf"
    ff = media.ffmpeg()
    subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
                    "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-pix_fmt", "yuv420p", str(d / "sample.mp4")],
                   check=True)
    s[".mp4"] = d / "sample.mp4"
    subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=2", str(d / "sample.wav")], check=True)
    s[".wav"] = d / "sample.wav"
    shutil.copy(d / "sample.wav", d / "sample2.wav")
    (d / "sample.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    s[".csv"] = d / "sample.csv"
    (d / "sample.json").write_text('[{"a": 1, "b": 2}]', encoding="utf-8")
    s[".json"] = d / "sample.json"
    (d / "sample.md").write_text("# Hello\n\nworld\n", encoding="utf-8")
    s[".md"] = d / "sample.md"
    (d / "sample.txt").write_text("plain text\n", encoding="utf-8")
    s[".txt"] = d / "sample.txt"
    with zipfile.ZipFile(d / "sample.zip", "w") as z:
        z.writestr("inside.txt", "hi")
    s[".zip"] = d / "sample.zip"
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["x", "y"])
    wb.save(d / "sample.xlsx")
    s[".xlsx"] = d / "sample.xlsx"
    fonts = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) + glob.glob("C:/Windows/Fonts/arial.ttf")
    if fonts:
        shutil.copy(fonts[0], d / "sample.ttf")
        s[".ttf"] = d / "sample.ttf"
    sig = d / "sig.png"
    Image.new("RGB", (200, 80), "white").save(sig)
    s["<sig>"] = sig
    s["<folder>"] = d / "folder"
    (d / "folder").mkdir()
    shutil.copy(d / "sample.pdf", d / "folder" / "a.pdf")
    return s


PARAMS = {"pages": "1", "size": "50%", "password": "pw", "text": "DRAFT", "span": "0:00-0:01", "mb": "1",
          "pattern": "Renamed {n}"}


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    return _samples(tmp_path_factory.mktemp("samples"))


def _cases():
    for op in operations.OPERATIONS:
        if op.id in SKIP:
            continue
        yield op.id


@pytest.mark.parametrize("op_id", list(_cases()))
def test_action_never_crashes(op_id, samples, tmp_path, capsys):
    op = operations.BY_ID[op_id]
    from filewizard import office

    has_office = bool(office.find_soffice())
    picks = [e for e in samples if e.startswith(".") and op.applies_to(e)
             and (has_office or e not in LIBREOFFICE_ONLY)]
    if op.exts & {operations.DIR, operations.BACKGROUND} and not picks:
        picks = ["<folder>"]
    if not picks:
        pytest.skip("no sample file of a kind this action accepts")
    args = []
    for n in op.param_names:
        value = "300 KB" if (n == "size" and "under" in op_id) else PARAMS.get(n, "1")
        args += ["-p", f"{n}={value}"]
    if op_id == "pdf-sign":
        args = ["-p", f"sig={samples['<sig>']}", "-p", "x=200", "-p", "y=300"]
    for ext in picks[:3]:  # a few kinds per action keeps the run fast
        src = samples[ext]
        work = tmp_path / ext.strip(".<>")
        work.mkdir()
        if src.is_dir():
            target = work / "folder"
            shutil.copytree(src, target)
        else:
            target = work / src.name
            shutil.copy(src, target)
        files = [target]
        if op.multi or op.batch:
            extra = work / f"second{target.suffix}"
            if target.is_file():
                shutil.copy(src, extra)
                files.append(extra)
        if op.mixed:  # e.g. video + audio
            files = [work / "v.mp4", work / "a.wav"]
            shutil.copy(samples[".mp4"], files[0])
            shutil.copy(samples[".wav"], files[1])
        if op_id == "paste-image" or op_id == "paste-text":
            continue  # needs a real clipboard
        code = main(["run", op_id, *map(str, files), *args])
        err = capsys.readouterr().err
        assert "Something went wrong" not in err, f"{op_id} on {ext}: {err[-800:]}"
        assert code in (0, 1), (op_id, ext, code)
