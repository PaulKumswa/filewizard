import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ["FILEWIZARD_NONINTERACTIVE"] = "1"

import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

from filewizard import collector, office, operations, shell_menu  # noqa: E402
from filewizard.cli import main  # noqa: E402
from filewizard.util import UserError, parse_page_ranges, unique_path  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def make_pdf(path: Path, pages: int = 3, rotate: int = 0) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        p = doc.new_page()
        p.insert_text((72, 72), f"Page {i + 1} hello", fontsize=24)
        if rotate:
            p.set_rotation(rotate)
    doc.save(path)
    return path


def make_img(path: Path, size=(400, 300), mode="RGB", exif_rotate=False) -> Path:
    img = Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30))
    kw = {}
    if exif_rotate:
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90 CW to display"
        exif[0x8825] = {2: (1.0, 2.0, 3.0)}  # fake GPS
        kw["exif"] = exif.tobytes()
    img.save(path, **kw)
    return path


def run(*args) -> int:
    return main(["run", *map(str, args)])


def pages(path: Path) -> int:
    with pymupdf.open(path) as d:
        return d.page_count


# ---------- utils ----------

def test_page_ranges():
    assert parse_page_ranges("1-3,5", 10) == [0, 1, 2, 4]
    assert parse_page_ranges("8-", 10) == [7, 8, 9]
    assert parse_page_ranges("-2", 10) == [0, 1]
    assert parse_page_ranges("last", 4) == [3]
    assert parse_page_ranges("3 1", 4) == [2, 0]
    for bad in ("0", "11", "5-2", "abc", ""):
        with pytest.raises(UserError):
            parse_page_ranges(bad, 10)


def test_unique_path(tmp_path):
    a = tmp_path / "x.pdf"
    assert unique_path(a) == a
    a.touch()
    assert unique_path(a).name == "x (2).pdf"


# ---------- PDF ----------

def test_merge_and_order(tmp_path):
    a = make_pdf(tmp_path / "a10.pdf", 1)
    b = make_pdf(tmp_path / "a2.pdf", 2)
    assert run("pdf-merge", a, b) == 0
    out = tmp_path / "a2 (merged).pdf"  # natural sort: a2 before a10
    assert pages(out) == 3
    with pymupdf.open(out) as d:
        assert "Page 1" in d[0].get_text() and "Page 1" in d[2].get_text()


def test_merge_needs_two(tmp_path, capsys):
    assert run("pdf-merge", make_pdf(tmp_path / "a.pdf")) == 1
    assert "at least two" in capsys.readouterr().err


def test_split_extract_delete_reverse(tmp_path):
    src = make_pdf(tmp_path / "doc.pdf", 5)
    assert run("pdf-split", src) == 0
    assert len(list((tmp_path / "doc (pages)").glob("*.pdf"))) == 5
    assert run("pdf-extract", src, "-p", "pages=2-3") == 0
    assert pages(tmp_path / "doc (pages 2-3).pdf") == 2
    assert run("pdf-delete", src, "-p", "pages=1,5") == 0
    with pymupdf.open(tmp_path / "doc (edited).pdf") as d:
        assert d.page_count == 3 and "Page 2" in d[0].get_text()
    assert run("pdf-reverse", src) == 0
    with pymupdf.open(tmp_path / "doc (reversed).pdf") as d:
        assert "Page 5" in d[0].get_text()


def test_missing_param_noninteractive(tmp_path, capsys):
    assert run("pdf-extract", make_pdf(tmp_path / "d.pdf")) == 1
    assert "-p pages=" in capsys.readouterr().err


def test_rotate(tmp_path):
    src = make_pdf(tmp_path / "r.pdf", 2)
    assert run("pdf-rotate-right", src) == 0
    with pymupdf.open(tmp_path / "r (rotated right).pdf") as d:
        assert [p.rotation for p in d] == [90, 90]


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_page_numbers_land_at_visible_bottom(tmp_path, rot):
    src = make_pdf(tmp_path / f"n{rot}.pdf", 2, rotate=rot)
    assert run("pdf-number", src) == 0
    with pymupdf.open(tmp_path / f"n{rot} (numbered).pdf") as d:
        page = d[1]
        hits = page.search_for("2 / 2")
        assert hits, "page number text not found"
        # search_for returns unrotated coordinates
        r = hits[0] * page.rotation_matrix  # unrotated -> what the reader sees
        assert r.y0 > page.rect.height * 0.85, (rot, r, page.rect)
        assert abs((r.x0 + r.x1) / 2 - page.rect.width / 2) < 30


def test_protect_and_unlock(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", 1)
    assert run("pdf-protect", src, "-p", "password=hunter2") == 0
    locked = tmp_path / "s (protected).pdf"
    with pymupdf.open(locked) as d:
        assert d.needs_pass and not d.authenticate("wrong") and d.authenticate("hunter2")
    # operating on a locked file gives a friendly error
    assert run("pdf-rotate-right", locked) == 1
    assert run("pdf-unlock", locked, "-p", "password=wrong") == 1
    assert run("pdf-unlock", locked, "-p", "password=hunter2") == 0
    with pymupdf.open(tmp_path / "s (protected) (unlocked).pdf") as d:
        assert not d.needs_pass and "Page 1" in d[0].get_text()


def test_unlock_owner_only_protection(tmp_path):
    """'Can open but can't print/copy' PDFs: no password needed to remove restrictions."""
    src = make_pdf(tmp_path / "o.pdf", 1)
    with pymupdf.open(src) as d:
        d.save(tmp_path / "owner.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="secret",
               user_pw="", permissions=pymupdf.PDF_PERM_ACCESSIBILITY)
    assert run("pdf-unlock", tmp_path / "owner.pdf", "-p", "password=") == 0
    with pymupdf.open(tmp_path / "owner (unlocked).pdf") as d:
        assert not d.is_encrypted and "Page 1" in d[0].get_text()


def test_unlock_plain_pdf_errors(tmp_path, capsys):
    assert run("pdf-unlock", make_pdf(tmp_path / "p.pdf"), "-p", "password=") == 1
    assert "isn't password-protected" in capsys.readouterr().err


def test_compress(tmp_path):
    # a PDF with a big noisy image compresses; a tiny text PDF reports "already small"
    noisy = tmp_path / "noise.png"
    Image.effect_noise((3000, 3000), 60).convert("RGB").save(noisy)
    big = tmp_path / "big.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, filename=str(noisy))
    doc.save(big)
    assert run("pdf-compress", big) == 0
    out = tmp_path / "big (compressed).pdf"
    assert out.stat().st_size < big.stat().st_size * 0.7
    small = make_pdf(tmp_path / "small.pdf")
    with pymupdf.open(small) as d:  # already optimized: nothing left to squeeze
        d.save(tmp_path / "tight.pdf", garbage=4, deflate=True)
    assert run("pdf-compress", tmp_path / "tight.pdf") == 1
    assert not (tmp_path / "tight (compressed).pdf").exists()


def test_pdf_to_images_and_text(tmp_path):
    one = make_pdf(tmp_path / "one.pdf", 1)
    many = make_pdf(tmp_path / "many.pdf", 3)
    assert run("pdf-to-png", one) == 0
    assert Image.open(tmp_path / "one.png").size[0] > 1000  # 200 dpi letter
    assert run("pdf-to-jpg", many) == 0
    assert len(list((tmp_path / "many (jpg)").glob("*.jpg"))) == 3
    assert run("pdf-to-txt", many) == 0
    assert "Page 3 hello" in (tmp_path / "many.txt").read_text()


def test_pdf_to_docx(tmp_path):
    pytest.importorskip("pdf2docx")
    docx = pytest.importorskip("docx")
    assert run("pdf-to-docx", make_pdf(tmp_path / "w.pdf", 2)) == 0
    text = "\n".join(p.text for p in docx.Document(tmp_path / "w.docx").paragraphs)
    assert "Page 2 hello" in text


def test_extract_images(tmp_path):
    img = make_img(tmp_path / "pic.png", (200, 200))
    pdfp = tmp_path / "withimg.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(0, 0, 200, 200), filename=str(img))
    doc.save(pdfp)
    assert run("pdf-extract-images", pdfp) == 0
    assert len(list((tmp_path / "withimg (images)").iterdir())) == 1
    assert run("pdf-extract-images", make_pdf(tmp_path / "noimg.pdf")) == 1


def test_bad_pdf(tmp_path, capsys):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert run("pdf-split", bad) == 1
    assert "valid PDF" in capsys.readouterr().err


# ---------- images ----------

@pytest.mark.parametrize("target", ["png", "jpg", "webp", "ico", "bmp", "tiff"])
def test_image_conversions(tmp_path, target):
    src = make_img(tmp_path / "t.png", mode="RGBA") if target != "png" else make_img(tmp_path / "t.jpg")
    assert run(f"img-to-{target}", src) == 0
    out = tmp_path / f"t.{target}"
    Image.open(out).verify()


def test_heic(tmp_path):
    heif = pytest.importorskip("pillow_heif")
    heif.register_heif_opener()
    src = tmp_path / "phone.heic"
    Image.new("RGB", (64, 48), (0, 128, 0)).save(src)
    assert run("img-to-jpg", src) == 0
    assert Image.open(tmp_path / "phone.jpg").size == (64, 48)


def test_exif_orientation_applied_and_metadata_stripped(tmp_path):
    src = make_img(tmp_path / "cam.jpg", (400, 300), exif_rotate=True)
    assert run("img-to-png", src) == 0
    assert Image.open(tmp_path / "cam.png").size == (300, 400)  # rotated upright
    assert run("img-strip", src) == 0
    out = Image.open(tmp_path / "cam (no metadata).jpg")
    assert len(out.getexif()) == 0 and out.size == (300, 400)


def test_resize_variants(tmp_path):
    src = make_img(tmp_path / "big.png", (1000, 500))
    assert run("img-resize", src, "-p", "size=50%") == 0
    assert Image.open(tmp_path / "big (500x250).png").size == (500, 250)
    assert run("img-resize", src, "-p", "size=200") == 0
    assert Image.open(tmp_path / "big (200x100).png").size == (200, 100)
    assert run("img-resize", src, "-p", "size=100x100") == 0
    assert Image.open(tmp_path / "big (100x50).png").size == (100, 50)
    assert run("img-resize", src, "-p", "size=huge") == 1


def test_image_rotate_and_compress(tmp_path):
    src = tmp_path / "n.png"
    Image.effect_noise((800, 600), 40).convert("RGB").save(src)
    assert run("img-rotate-right", src) == 0
    assert Image.open(tmp_path / "n (rotated right).png").size == (600, 800)
    assert run("img-compress", src) == 0
    assert (tmp_path / "n (compressed).jpg").stat().st_size < src.stat().st_size


def test_images_to_pdf(tmp_path):
    a = make_img(tmp_path / "1.jpg", (3000, 4000))
    b = make_img(tmp_path / "2.png", (4000, 3000))
    assert run("img-to-pdf", a) == 0
    with pymupdf.open(tmp_path / "1.pdf") as d:
        assert d[0].rect.height <= 792.5  # fits US Letter
    assert run("img-combine-pdf", b, a) == 0
    with pymupdf.open(tmp_path / "1 (combined).pdf") as d:
        assert d.page_count == 2
        assert d[0].rect.height > d[0].rect.width  # 1.jpg (portrait) first
        assert d[1].rect.width > d[1].rect.height


def test_one_bad_file_doesnt_stop_batch(tmp_path, capsys):
    good = make_img(tmp_path / "good.png")
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"nope")
    assert run("img-to-webp", good, bad) == 1
    assert (tmp_path / "good.webp").exists()
    assert "bad.jpg" in capsys.readouterr().err


# ---------- office ----------

@pytest.mark.skipif(not office.find_soffice(), reason="LibreOffice not installed")
def test_office_to_pdf_libreoffice(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Hello from Word")
    src = tmp_path / "memo.docx"
    d.save(src)
    assert run("office-to-pdf", src) == 0
    with pymupdf.open(tmp_path / "memo.pdf") as p:
        assert "Hello from Word" in p[0].get_text()


# ---------- shell integration ----------

def test_registry_entries():
    entries = shell_menu.build_entries('"C:\\py\\pythonw.exe" "C:\\fw\\launch.pyw"', "C:\\fw\\i.ico")
    keys = dict(entries)
    base = r"Software\Classes\SystemFileAssociations\.pdf\shell\FileWizard"
    assert keys[base]["SubCommands"] == ("REG_SZ", "")
    pdf_verbs = [k for k in keys if k.startswith(base + r"\shell\\"[:-1]) and not k.endswith("command")]
    assert any(k.endswith("pdf-merge") for k in pdf_verbs)
    assert not any("img-to-png" in k for k in pdf_verbs)
    cmd = keys[next(k for k in keys if k.endswith(r"pdf-merge\command"))][""][1]
    assert cmd == '"C:\\py\\pythonw.exe" "C:\\fw\\launch.pyw" pdf-merge "%1"'
    # PNG files don't offer "Convert to PNG"
    png = [k for k in keys if r"\.png\shell" in k]
    assert not any(k.endswith("img-to-png") for k in png)
    assert any(k.endswith("img-to-jpg") for k in png)
    # submenu verbs sort in menu order
    names = [k.rsplit("\\", 1)[1] for k in pdf_verbs if k.count("\\") == base.count("\\") + 2]
    assert names == sorted(names)
    reg = shell_menu.to_reg_file(entries)
    assert reg.startswith("Windows Registry Editor Version 5.00")
    assert '"CommandFlags"=dword:00000020' in reg


def test_launcher_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    launcher = shell_menu.write_launcher()
    src = make_pdf(tmp_path / "x.pdf", 2)
    # run from the file's folder with a hostile module there, like Explorer would
    (tmp_path / "pymupdf.py").write_text("raise SystemExit('hijacked')")
    env = {**os.environ, "FILEWIZARD_NONINTERACTIVE": "1", "TMPDIR": str(tmp_path / "q")}
    (tmp_path / "q").mkdir()
    r = subprocess.run([sys.executable, str(launcher), "pdf-reverse", str(src)],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "x (reversed).pdf").exists()


def test_collector_merges_parallel_invocations(tmp_path):
    """Simulate Explorer launching one process per selected file."""
    files = [make_pdf(tmp_path / f"f{i}.pdf", 1) for i in range(6)]
    env = {**os.environ, "FILEWIZARD_NONINTERACTIVE": "1", "TMPDIR": str(tmp_path / "q"),
           "XDG_STATE_HOME": str(tmp_path / "state"), "PYTHONPATH": str(REPO)}
    (tmp_path / "q").mkdir()
    procs = [subprocess.Popen([sys.executable, "-m", "filewizard", "run", "--shell", "pdf-merge", str(f)],
                              env=env, cwd=REPO, stderr=subprocess.PIPE, text=True) for f in files]
    errs = [p.communicate(timeout=60)[1] for p in procs]
    assert all(p.returncode == 0 for p in procs), errs
    merged = list(tmp_path.glob("*merged*.pdf"))
    assert len(merged) == 1, merged
    assert pages(merged[0]) == 6


def test_collector_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(collector.tempfile, "gettempdir", lambda: str(tmp_path))
    assert collector.collect("x", "a", settle=0.2) == ["a"]
    q = collector._queue_dir("x")
    assert not list(q.glob("*.item")) and not (q / "leader.lock").exists()
    (q / "leader.lock").mkdir()
    assert collector.collect("x", "b", settle=0.2) is None  # someone else is leading


def test_every_op_has_a_menu_home():
    for op in operations.OPERATIONS:
        assert any(op.applies_to(e) for e in operations.ALL_EXTS), op.id
        assert (op.per_file is None) != (op.batch is None), op.id
