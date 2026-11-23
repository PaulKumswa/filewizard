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
    assert run("combine-pdf", a, b) == 0
    out = tmp_path / "a2 (merged).pdf"  # natural sort: a2 before a10
    assert pages(out) == 3
    with pymupdf.open(out) as d:
        assert "Page 1" in d[0].get_text() and "Page 1" in d[2].get_text()


def test_merge_needs_two(tmp_path, capsys):
    assert run("combine-pdf", make_pdf(tmp_path / "a.pdf")) == 1
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
    assert run("combine-pdf", b, a) == 0
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
    pdf_keys = [k for k in keys if k.startswith(base + "\\")]
    assert any(k.endswith("combine-pdf") for k in pdf_keys)
    assert not any("img-to-png" in k for k in pdf_keys)
    cmd = keys[next(k for k in pdf_keys if k.endswith("combine-pdf\\command"))][""][1]
    assert cmd == '"C:\\py\\pythonw.exe" "C:\\fw\\launch.pyw" combine-pdf "%1"'
    # nested: Convert to > PNG on a jpg, and no "Convert to PNG" on a png
    jpg = [k for k in keys if "\\.jpg\\shell" in k]
    assert any(k.endswith("_convert-to\\shell\\" + k.rsplit("\\", 1)[1]) and k.endswith("img-to-png") for k in jpg)
    assert not any(k.endswith("img-to-png") for k in keys if "\\.png\\shell" in k)
    # per-extension label: "Merge" on PDFs, "Combine" elsewhere
    assert keys[next(k for k in pdf_keys if k.endswith("combine-pdf"))]["MUIVerb"][1].startswith("Merge")
    assert keys[next(k for k in jpg if k.endswith("combine-pdf"))]["MUIVerb"][1].startswith("Combine")
    # folders get a menu too
    assert any(k.startswith(r"Software\Classes\Directory\shell\FileWizard") for k in keys)
    reg = shell_menu.to_reg_file(entries)
    assert reg.startswith("Windows Registry Editor Version 5.00")
    assert '"CommandFlags"=dword:00000020' in reg


def test_verb_keys_identical_across_types():
    """Mixed selections (jpg+png, pdf+docx) only work if each verb has one key name everywhere."""
    entries = shell_menu.build_entries("x", None)
    by_op: dict[str, set[str]] = {}
    for key, vals in entries:
        if key.endswith("\\command"):
            op = vals[""][1].split()[1]
            tail = key.split("\\shell\\FileWizard", 1)[1]
            by_op.setdefault(op, set()).add(tail)
    for op, tails in by_op.items():
        assert len(tails) == 1, (op, tails)


def test_registry_key_lengths():
    for key, _ in shell_menu.build_entries("x", None):
        assert len(key) < 255, key


def test_menu_preview():
    text = shell_menu.render_menu(".jpg")
    assert "Convert to  ▸" in text and "HEIC" in text
    assert "(audio only)" in shell_menu.render_menu("mp4")
    assert "Merge selected PDFs" in shell_menu.render_menu(".pdf")


def test_launcher_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    launcher = shell_menu.write_launcher()
    src = make_pdf(tmp_path / "x.pdf", 2)
    # run from the file's folder with a hostile module there, like Explorer would
    (tmp_path / "pymupdf.py").write_text("raise SystemExit('hijacked')")
    env = {**os.environ, "FILEWIZARD_NONINTERACTIVE": "1", "TMPDIR": str(tmp_path / "q")}
    (tmp_path / "q").mkdir()
    r = subprocess.run([sys.executable, str(launcher), "run", "--shell", "pdf-reverse", str(src)],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "x (reversed).pdf").exists()


def test_collector_merges_parallel_invocations(tmp_path):
    """Simulate Explorer launching one process per selected file."""
    files = [make_pdf(tmp_path / f"f{i}.pdf", 1) for i in range(6)]
    env = {**os.environ, "FILEWIZARD_NONINTERACTIVE": "1", "TMPDIR": str(tmp_path / "q"),
           "XDG_STATE_HOME": str(tmp_path / "state"), "PYTHONPATH": str(REPO)}
    (tmp_path / "q").mkdir()
    procs = [subprocess.Popen([sys.executable, "-m", "filewizard", "run", "--shell", "combine-pdf", str(f)],
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
        assert any(op.applies_to(e) for e in [*operations.ALL_EXTS, operations.DIR]), op.id
        assert (op.per_file is None) != (op.batch is None), op.id
        assert not (op.param_names and op.ask is None), op.id


def test_classic_menus_fit_windows_16_entry_limit():
    """Measured in CI: Windows shows at most 16 entries in a registry cascade. Never exceed it,
    and always end with "All actions…" when something had to be left out."""
    from filewizard.operations import ALL_EXTS, DIR, for_ext

    entries = dict(shell_menu.build_entries("x", None))
    for ext in [*ALL_EXTS, DIR]:
        base = shell_menu._menu_root(ext)
        n = sum(1 for k in entries if k.startswith(base + "\\") and not k.endswith("\\command"))
        assert n <= 16, (ext, n)
        full = shell_menu._cost([op for op in for_ext(ext) if op.id != "palette"])
        if full > 15:
            assert any(k.endswith("_palette") for k in entries if k.startswith(base + "\\")), ext


def test_png_menu_still_has_webp_for_mixed_selection_test():
    assert "WebP" in shell_menu.render_menu(".png") and "WebP" in shell_menu.render_menu(".jpg")


def _parse_manifest(lines):
    """Python mirror of the C++ parser in native/fwshell.cpp."""
    types, top, stack, names = {}, [], [], []
    for ln in lines:
        f = ln.split("\t")
        target = stack[-1]["children"] if stack else top
        if f[0] == "type":
            names, top, stack = f[1:], [], []
        elif f[0] == "menu":
            node = {"menu": f[1], "children": []}
            target.append(node)
            stack.append(node)
        elif f[0] == "end":
            assert stack, "unbalanced end"
            stack.pop()
        elif f[0] == "sep":
            target.append("sep")
        elif f[0] == "item":
            target.append({"id": f[1], "label": f[2], "multi": "m" in f[3]})
        elif f[0] == "endtype":
            assert not stack, "menu left open at endtype"
            for n in names:
                types[n] = top
    return types


def _ids(nodes):
    for n in nodes:
        if isinstance(n, dict) and "id" in n:
            yield n
        elif isinstance(n, dict):
            yield from _ids(n["children"])


def test_native_manifest():
    lines = shell_menu.manifest_lines('"fw.exe" run --native', "i.ico")
    assert lines[0] == 'command\t"fw.exe" run --native'
    types = _parse_manifest(lines)
    pdf = list(_ids(types[".pdf"]))
    merge = next(n for n in pdf if n["id"] == "combine-pdf")
    assert merge["multi"] and merge["label"].startswith("Merge")
    assert any(n["id"] == "pdf-flatten" for n in pdf), "native menu must have everything (no 16 cap)"
    assert "*" in types and "<folder>" in types
    # Convert to ▸ is a submenu on PDFs
    assert any(isinstance(n, dict) and n.get("menu") == "Convert to" for n in types[".pdf"])
    # identical menus share blocks: far fewer blocks than extensions
    assert sum(1 for ln in lines if ln.startswith("type\t")) < len(types) / 2
    assert len("\n".join(lines).encode()) < 200_000


def test_run_accepts_list_file(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    for p in (a, b):
        d = pymupdf.open()
        d.new_page()
        d.save(p)
    lst = tmp_path / "sel.txt"
    lst.write_text(f"{a}\n{b}\n", encoding="utf-8")
    assert main(["run", "--native", "combine-pdf", "--list", str(lst)]) == 0
    assert (tmp_path / "a (merged).pdf").exists() and not lst.exists()


def test_cli_survives_cp1252_console(tmp_path):
    """Regression: printing '→' or non-Latin filenames crashed on Windows cp1252 consoles."""
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "FILEWIZARD_NONINTERACTIVE": "1"}
    r = subprocess.run([sys.executable, "-m", "filewizard", "list"], cwd=REPO, env=env,
                       capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    r = subprocess.run([sys.executable, "-m", "filewizard", "menu", ".pdf"], cwd=REPO, env=env, capture_output=True)
    assert r.returncode == 0 and b"Merge" in r.stdout


def test_pages_to_spec():
    from filewizard.ui import pages_to_spec

    assert pages_to_spec([0, 1, 2, 4, 7, 8]) == "1-3,5,8-9"
    assert pages_to_spec([3]) == "4"
    assert pages_to_spec([]) == ""
    assert parse_page_ranges(pages_to_spec([0, 2, 3, 9]), 10) == [0, 2, 3, 9]


def test_recycle_removes_only_given_outputs(tmp_path):
    from filewizard.ui import recycle

    keep = tmp_path / "original.pdf"
    keep.write_text("x")
    made = tmp_path / "original (merged).pdf"
    made.write_text("y")
    folder = tmp_path / "original (pages)"
    folder.mkdir()
    (folder / "p1.pdf").write_text("z")
    recycle([made, folder, tmp_path / "missing.pdf"])
    assert keep.exists() and not made.exists() and not folder.exists()
