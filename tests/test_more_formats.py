"""Tests for media, extra image formats, documents, data, archives and file tools."""

import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

os.environ["FILEWIZARD_NONINTERACTIVE"] = "1"

import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

from filewizard import media, pdf  # noqa: E402
from filewizard.cli import main  # noqa: E402
from filewizard.util import UserError  # noqa: E402


def run(*args) -> int:
    return main(["run", *map(str, args)])


@pytest.fixture(scope="session")
def clip(tmp_path_factory) -> Path:
    """A 3-second 320x240 test video with a 440 Hz tone."""
    d = tmp_path_factory.mktemp("media")
    out = d / "clip.mp4"
    subprocess.run([media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=3:size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


@pytest.fixture
def video(clip, tmp_path) -> Path:
    p = tmp_path / "clip.mp4"
    shutil.copy(clip, p)
    return p


@pytest.fixture
def song(tmp_path) -> Path:
    out = tmp_path / "song.wav"
    subprocess.run([media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=4", str(out)], check=True)
    return out


# ---------- media ----------

def test_probe(video):
    info = media.probe(video)
    assert info.vcodec == "h264" and info.acodec == "aac"
    assert (info.width, info.height) == (320, 240)
    assert 2.9 < info.duration < 3.2


def test_probe_rejects_non_media(tmp_path):
    bad = tmp_path / "x.mp4"
    bad.write_text("hello")
    with pytest.raises(UserError):
        media.probe(bad)


@pytest.mark.parametrize("target", ["mkv", "mov"])
def test_video_remux_is_lossless_copy(video, target):
    assert run(f"vid-to-{target}", video) == 0
    out = video.with_suffix(f".{target}")
    assert media.probe(out).vcodec == "h264"


@pytest.mark.parametrize("target", ["webm", "avi"])
def test_video_reencode(video, target):
    assert run(f"vid-to-{target}", video) == 0
    assert media.probe(video.with_suffix(f".{target}")).duration > 2.5


def test_video_to_gif_and_back(video):
    assert run("vid-to-gif", video) == 0
    gif = video.with_suffix(".gif")
    with Image.open(gif) as im:
        assert im.n_frames > 10
    assert run("gif-to-mp4", gif) == 0
    assert (video.parent / "clip (2).mp4").exists()  # never overwrites the original


@pytest.mark.parametrize("target", ["mp3", "m4a", "wav", "flac", "ogg", "opus", "aiff"])
def test_audio_conversions(song, target):
    assert run(f"aud-to-{target}", song) == 0 if target != "wav" else True
    if target != "wav":
        assert media.probe(song.with_suffix(f".{target}")).acodec


def test_extract_audio_from_video(video):
    assert run("aud-to-mp3", video) == 0
    info = media.probe(video.with_suffix(".mp3"))
    assert info.acodec == "mp3" and info.vcodec is None


def test_trim(video, song):
    assert run("media-trim", video, "-p", "span=0:01-0:02") == 0
    assert 0.8 < media.probe(video.parent / "clip (trimmed).mp4").duration < 1.3
    assert run("media-trim", song, "-p", "span=-2") == 0
    assert 1.8 < media.probe(song.parent / "song (trimmed).wav").duration < 2.2
    assert run("media-trim", song, "-p", "span=3-1") == 1


def test_parse_span():
    assert media.parse_span("1:00-", 90) == (60, 90)
    assert media.parse_span("-0:30", 90) == (0, 30)
    assert media.parse_span("0:10 to 0:20", 90) == (10, 20)
    with pytest.raises(UserError):
        media.parse_span("abc", 90)


def test_compress_to_target_size(tmp_path):
    big = tmp_path / "big.mp4"
    subprocess.run([media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=duration=6:size=1280x720:rate=30", "-c:v", "libx264", "-crf", "4", str(big)],
                   check=True)
    assert big.stat().st_size > 2 * 1024 * 1024
    assert run("vid-under-custom", big, "-p", "mb=1") == 0
    out = tmp_path / "big (under 1 MB).mp4"
    assert out.stat().st_size <= 1024 * 1024


def test_resize_rotate_mute_speed(video):
    assert run("vid-rotate-right", video) == 0
    info = media.probe(video.parent / "clip (rotated right).mp4")
    assert (info.width, info.height) == (240, 320)
    assert run("vid-mute", video) == 0
    assert media.probe(video.parent / "clip (muted).mp4").acodec is None
    assert run("media-speed-2_0", video) == 0
    assert 1.3 < media.probe(video.parent / "clip (2x speed).mp4").duration < 1.8
    assert run("vid-480p", video) == 1  # already smaller than 480p: friendly refusal


def test_join_videos_and_audio(video, song, tmp_path):
    other = tmp_path / "other.mp4"  # different size, no audio
    subprocess.run([media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=2:size=640x360:rate=30", "-c:v", "libx264", str(other)], check=True)
    assert run("media-join", video, other) == 0
    joined = next(tmp_path.glob("*joined*.mp4"))
    assert 4.5 < media.probe(joined).duration < 5.5
    s2 = tmp_path / "s2.wav"
    shutil.copy(song, s2)
    assert run("media-join", song, s2) == 0
    assert 7.5 < media.probe(next(tmp_path.glob("*joined*.wav"))).duration < 8.5


def test_snapshot_frames_normalize(video, song):
    assert run("vid-snapshot", video) == 0
    assert Image.open(video.parent / "clip (frame).jpg").size == (320, 240)
    assert run("vid-frames", video) == 0
    assert len(list((video.parent / "clip (frames)").glob("*.jpg"))) >= 3
    assert run("media-normalize", song) == 0


def test_cancel_stops_ffmpeg(video, monkeypatch):
    from filewizard import util

    util.PROGRESS.cancelled = True
    try:
        with pytest.raises(util.Cancelled):
            media.convert_video(video, ".webm")
    finally:
        util.PROGRESS.cancelled = False
    assert not list(video.parent.glob("*.partial*")), "partial output left behind"


# ---------- images: new formats ----------

@pytest.mark.parametrize("target", ["heic", "avif"])
def test_modern_image_formats(tmp_path, target):
    src = tmp_path / "p.jpg"
    Image.new("RGB", (120, 80), (30, 150, 60)).save(src)
    assert run(f"img-to-{target}", src) == 0
    from filewizard.images import open_image

    assert open_image(tmp_path / f"p.{target}").size == (120, 80)


def test_svg_to_png(tmp_path):
    svg = tmp_path / "logo.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
                   '<rect width="100" height="50" fill="red"/></svg>')
    assert run("img-to-png", svg) == 0
    im = Image.open(tmp_path / "logo.png")
    assert im.size[0] >= 1000 and im.size[0] / im.size[1] == pytest.approx(2, rel=0.02)


def test_psd_and_tga_input(tmp_path):
    tga = tmp_path / "t.tga"
    Image.new("RGB", (40, 30), "blue").save(tga)
    assert run("img-to-png", tga) == 0


def test_animated_gif_stays_animated(tmp_path):
    frames = [Image.new("RGB", (40, 40), c) for c in ("red", "green", "blue")]
    gif = tmp_path / "a.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert run("img-to-webp", gif) == 0
    with Image.open(tmp_path / "a.webp") as im:
        assert im.n_frames == 3


def test_image_edits(tmp_path):
    src = tmp_path / "logo.png"
    im = Image.new("RGB", (300, 200), "white")
    im.paste(Image.new("RGB", (100, 50), "red"), (100, 75))
    im.save(src)
    assert run("img-trim", src) == 0
    assert Image.open(tmp_path / "logo (cropped).png").size == (100, 50)
    assert run("img-square", src) == 0
    assert Image.open(tmp_path / "logo (square).png").size == (300, 300)
    assert run("img-gray", src) == 0
    assert Image.open(tmp_path / "logo (grayscale).png").mode == "L"
    assert run("img-rotate-hflip", src) == 0
    assert run("img-resize-50pct", src) == 0
    assert Image.open(tmp_path / "logo (150x100).png").size == (150, 100)


def test_conversion_keeps_photo_date_but_not_orientation(tmp_path):
    src = tmp_path / "cam.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x0132] = "2024:07:04 12:00:00"
    Image.new("RGB", (40, 20)).save(src, exif=exif.tobytes())
    assert run("img-to-webp", src) == 0
    out = Image.open(tmp_path / "cam.webp")
    assert out.size == (20, 40)
    assert out.getexif().get(0x0132) == "2024:07:04 12:00:00"
    assert 0x0112 not in out.getexif()


def test_contact_sheet(tmp_path):
    files = []
    for i in range(5):
        p = tmp_path / f"{i}.png"
        Image.new("RGB", (200, 100), (i * 40, 0, 0)).save(p)
        files.append(p)
    assert run("img-contact-sheet", *files) == 0
    assert (tmp_path / "0 (contact sheet).jpg").exists()


@pytest.mark.skipif(not pdf.tessdata(), reason="Tesseract not installed")
def test_ocr_image_and_pdf(tmp_path):
    from PIL import ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 300), "white")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 72)
    except OSError:
        font = ImageFont.load_default(size=72)
    ImageDraw.Draw(img).text((40, 100), "Hello FileWizard 2026", fill="black", font=font)
    shot = tmp_path / "shot.png"
    img.save(shot)
    assert run("img-ocr", shot) == 0
    assert "FileWizard" in (tmp_path / "shot.txt").read_text()
    # scanned PDF (image only) -> searchable
    assert run("img-to-pdf", shot) == 0
    scan = tmp_path / "shot.pdf"
    with pymupdf.open(scan) as d:
        assert not d[0].get_text().strip()
    assert run("pdf-ocr", scan) == 0
    with pymupdf.open(tmp_path / "shot (searchable).pdf") as d:
        assert "FileWizard" in d[0].get_text()


# ---------- PDF extras ----------

def _pdf(path, pages=2, blank_at=()):
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        if i not in blank_at:
            p.insert_text((72, 72), f"Page {i + 1}")
    d.save(path)
    return path


def test_watermark_blank_gray_flatten(tmp_path):
    src = _pdf(tmp_path / "w.pdf", 4, blank_at=(1, 3))
    assert run("pdf-watermark", src, "-p", "text=DRAFT") == 0
    with pymupdf.open(tmp_path / "w (watermarked).pdf") as d:
        assert "DRAFT" in d[0].get_text()
    assert run("pdf-remove-blank", src) == 0
    with pymupdf.open(tmp_path / "w (2 blank removed).pdf") as d:
        assert d.page_count == 2
    assert run("pdf-remove-blank", tmp_path / "w (2 blank removed).pdf") == 1
    assert run("pdf-gray", src) == 0
    assert run("pdf-flatten", src) == 0


def test_combine_mixed_types_into_pdf(tmp_path):
    a = _pdf(tmp_path / "a.pdf", 2)
    img = tmp_path / "b.png"
    Image.new("RGB", (300, 200), "orange").save(img)
    md = tmp_path / "c.md"
    md.write_text("# Notes\n\nSome **bold** text.\n")
    assert run("combine-pdf", a, img, md) == 0
    out = tmp_path / "a (combined).pdf"
    with pymupdf.open(out) as d:
        assert d.page_count == 4
        assert [t[1] for t in d.get_toc()] == ["a", "b", "c"]  # a bookmark per file
        assert "Notes" in d[3].get_text()


# ---------- documents & data ----------

def test_markdown_to_pdf_and_html(tmp_path):
    md = tmp_path / "readme.md"
    md.write_text("# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n" + "para\n\n" * 200)
    assert run("doc-to-pdf", md) == 0
    with pymupdf.open(tmp_path / "readme.pdf") as d:
        assert d.page_count > 1 and "Title" in d[0].get_text()
    assert run("md-to-html", md) == 0
    assert "<table>" in (tmp_path / "readme.html").read_text()


def test_text_to_pdf_handles_windows_encoding(tmp_path):
    t = tmp_path / "notes.log"
    t.write_bytes("café — naïve\r\n".encode("cp1252"))
    assert run("doc-to-pdf", t) == 0
    with pymupdf.open(tmp_path / "notes.pdf") as d:
        assert "café" in d[0].get_text()


def test_epub_to_pdf(tmp_path):
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="c.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("c.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
                   'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title>'
                   '<dc:identifier id="id">x</dc:identifier></metadata><manifest><item id="c1" href="c1.xhtml" '
                   'media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("c1.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Chapter One</h1>'
                   + "<p>Once upon a time.</p>" * 50 + "</body></html>")
    assert run("doc-to-pdf", epub) == 0
    with pymupdf.open(tmp_path / "book.pdf") as d:
        assert "Chapter One" in d[0].get_text()


def test_csv_xlsx_json_roundtrip(tmp_path):
    import openpyxl

    src = tmp_path / "people.csv"
    src.write_text("name;zip;age\nAda;02139;36\nLin;90210;41\n", encoding="utf-8")  # semicolons (EU Excel)
    assert run("data-to-xlsx", src) == 0
    ws = openpyxl.load_workbook(tmp_path / "people.xlsx").active
    assert [c.value for c in ws[2]] == ["Ada", "02139", 36]  # leading zero kept, number typed
    assert run("data-to-json", src) == 0
    data = json.loads((tmp_path / "people.json").read_text())
    assert data[0]["zip"] == "02139" and data[1] == {"name": "Lin", "zip": 90210, "age": 41}
    j = tmp_path / "people.json"
    (tmp_path / "people.csv").unlink()
    assert run("data-to-csv", j) == 0
    assert (tmp_path / "people.csv").read_text(encoding="utf-8-sig").splitlines()[0] == "name,zip,age"


def test_multi_sheet_xlsx_to_csv(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "A"
    wb.active.append([1, 2])
    wb.create_sheet("B").append([3, 4])
    wb.save(tmp_path / "book.xlsx")
    assert run("data-to-csv", tmp_path / "book.xlsx") == 0
    assert sorted(p.name for p in (tmp_path / "book (csv)").iterdir()) == ["A.csv", "B.csv"]


# ---------- archives ----------

def test_zip_folder_and_extract(tmp_path):
    folder = tmp_path / "proj"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.txt").write_text("A")
    (folder / "sub" / "b.txt").write_text("B")
    assert run("zip", folder) == 0
    z = tmp_path / "proj.zip"
    assert sorted(zipfile.ZipFile(z).namelist()) == ["proj/a.txt", "proj/sub/b.txt"]
    shutil.rmtree(folder)
    assert run("arc-extract", z) == 0  # single top folder -> lands right next to the zip
    assert (tmp_path / "proj" / "sub" / "b.txt").read_text() == "B"


def test_7z_roundtrip(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hello" * 1000)
    assert run("7z", f) == 0
    f.unlink()
    assert run("arc-extract-folder", tmp_path / "x.7z") == 0
    assert (tmp_path / "x" / "x.txt").read_text().startswith("hello")


def test_tar_gz(tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f.txt").write_text("t")
    tgz = tmp_path / "d.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(tmp_path / "d", arcname="d")
    shutil.rmtree(tmp_path / "d")
    assert run("arc-extract-folder", tgz) == 0
    assert (tmp_path / "d" / "f.txt").exists()


def test_zip_slip_blocked(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../escaped.txt", "pwned")
    assert run("arc-extract-folder", evil) == 1
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "evil").exists()  # cleaned up


# ---------- any-file tools ----------

def test_checksum(tmp_path, capsys):
    f = tmp_path / "f.bin"
    f.write_bytes(b"abc")
    assert run("checksum", f) == 0
    assert "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" in capsys.readouterr().out


def test_batch_rename(tmp_path):
    for n in ("img10.jpg", "img2.jpg", "img1.jpg"):
        (tmp_path / n).write_text(n)
    files = list(tmp_path.iterdir())
    assert run("rename", *files, "-p", "pattern=Trip {n}") == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Trip 01.jpg", "Trip 02.jpg", "Trip 03.jpg"]
    assert (tmp_path / "Trip 03.jpg").read_text() == "img10.jpg"  # natural order kept
    assert run("rename", *tmp_path.iterdir(), "-p", "pattern=a/b") == 1


def test_folder_combine(tmp_path):
    folder = tmp_path / "scans"
    folder.mkdir()
    for i in range(3):
        Image.new("RGB", (100, 140), "white").save(folder / f"page{i}.jpg")
    assert run("folder-combine-pdf", folder) == 0
    with pymupdf.open(folder / "page0 (combined).pdf") as d:
        assert d.page_count == 3


def test_image_under_size(tmp_path):
    src = tmp_path / "big.png"
    Image.effect_noise((2400, 1600), 50).convert("RGB").save(src)
    assert run("img-under-200kb", src) == 0
    out = tmp_path / "big (under 200 KB).jpg"
    assert out.stat().st_size <= 200 * 1024
    assert run("img-under-custom", src, "-p", "size=1 MB") == 0
    assert (tmp_path / "big (under 1 MB).jpg").stat().st_size <= 1024 * 1024


def test_show_details(tmp_path, video, capsys):
    img = tmp_path / "gps.jpg"
    exif = Image.Exif()
    exif[0x010F] = "Canon"
    gps = {1: "N", 2: (42.0, 55.0, 0.0), 3: "W", 4: (85.0, 35.0, 0.0)}
    exif[0x8825] = gps
    Image.new("RGB", (64, 32)).save(img, exif=exif.tobytes())
    assert run("details", img, video) == 0
    out = capsys.readouterr().out
    assert "64 × 32" in out and "Canon" in out and "GPS location" in out
    assert "320 × 240" in out and "h264" in out


def test_image_watermark_and_icons(tmp_path):
    src = tmp_path / "photo.jpg"
    Image.new("RGB", (800, 600), (20, 90, 160)).save(src)
    assert run("img-watermark", src, "-p", "text=© Paul") == 0
    wm = Image.open(tmp_path / "photo (watermarked).jpg")
    assert wm.size == (800, 600)
    assert wm.convert("L").getextrema()[1] > 120  # white text drawn on a dark image
    assert run("img-icons", src) == 0
    names = {p.name for p in (tmp_path / "photo (icons)").iterdir()}
    assert {"favicon.ico", "apple-touch-icon.png", "icon-512.png"} <= names
    assert Image.open(tmp_path / "photo (icons)" / "icon-512.png").size == (512, 512)


def test_burn_and_extract_subtitles(video):
    assert run("vid-subs", video) == 1  # no .srt yet: friendly message
    video.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHello there\n", encoding="utf-8")
    assert run("vid-subs", video) == 0
    assert media.probe(video.parent / "clip (subtitled).mp4").duration > 2.5
    assert run("vid-extract-subs", video) == 1  # mp4 without a subtitle track


def test_output_subfolder_setting(tmp_path, monkeypatch):
    from filewizard import settings

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    settings.save({"output": "subfolder"})
    try:
        src = tmp_path / "a.png"
        Image.new("RGB", (10, 10)).save(src)
        assert run("img-to-jpg", src) == 0
        assert (tmp_path / "FileWizard" / "a.jpg").exists()
    finally:
        settings.save({"output": "beside"})


def test_pdf_under_size(tmp_path):
    noisy = tmp_path / "scan.png"
    Image.effect_noise((2500, 3300), 40).convert("RGB").save(noisy)
    big = tmp_path / "scan.pdf"
    d = pymupdf.open()
    for _ in range(3):
        d.new_page().insert_image(pymupdf.Rect(0, 0, 612, 792), filename=str(noisy))
    d.save(big)
    assert big.stat().st_size > 3 * 1024 * 1024
    assert run("pdf-under-1mb", big) == 0
    out = tmp_path / "scan (under 1 MB).pdf"
    assert out.stat().st_size <= 1024 * 1024
    with pymupdf.open(out) as o:
        assert o.page_count == 3
    assert run("pdf-under-custom", out, "-p", "size=5 MB") == 1  # already under: friendly message


def test_pdf_sign(tmp_path):
    from PIL import ImageDraw

    sig = tmp_path / "sig.jpg"  # photo of ink on white paper
    im = Image.new("RGB", (400, 150), "white")
    ImageDraw.Draw(im).line([(20, 120), (120, 30), (220, 110), (380, 40)], fill=(10, 20, 120), width=8)
    im.save(sig)
    doc = _pdf(tmp_path / "contract.pdf", 2)
    assert run("pdf-sign", doc, "-p", f"sig={sig}", "-p", "x=400", "-p", "y=700", "-p", "page=0",
               "-p", "width=160") == 0
    with pymupdf.open(tmp_path / "contract (signed).pdf") as d:
        imgs = d[-1].get_images()
        assert len(imgs) == 1 and not d[0].get_images()
        r = d[-1].get_image_rects(imgs[0][0])[0]
        assert abs((r.x0 + r.x1) / 2 - 400) < 2 and abs(r.width - 160) < 2
    from filewizard.pdf import signature_png
    import io

    png = Image.open(io.BytesIO(signature_png(sig)))
    assert png.mode == "RGBA" and png.getpixel((0, 0))[3] == 0  # white became transparent


def test_rename_by_photo_date(tmp_path):
    exif = Image.Exif()
    exif[0x0132] = "2024:07:04 09:30:00"
    Image.new("RGB", (8, 8)).save(tmp_path / "IMG_0001.jpg", exif=exif.tobytes())
    Image.new("RGB", (8, 8)).save(tmp_path / "IMG_0002.jpg")  # no EXIF: falls back to file date
    assert run("rename", *sorted(tmp_path.iterdir()), "-p", "pattern={taken} {n}") == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names[0] == "2024-07-04 01.jpg" and len(names) == 2


def test_rotate_some_pages(tmp_path):
    src = _pdf(tmp_path / "scan.pdf", 4)
    assert run("pdf-rotate-some", src, "-p", "pages=2,4") == 0
    with pymupdf.open(tmp_path / "scan (rotated).pdf") as d:
        assert [p.rotation for p in d] == [0, 90, 0, 90]


def test_stitch(tmp_path):
    a, b = tmp_path / "s1.png", tmp_path / "s2.png"
    Image.new("RGB", (400, 200), "red").save(a)
    Image.new("RGB", (200, 100), "blue").save(b)
    assert run("img-stitch-v", a, b) == 0
    assert Image.open(tmp_path / "s1 (stitched).png").size == (400, 400)  # second scaled to width 400
    assert run("img-stitch-h", a, b) == 0
    assert Image.open(tmp_path / "s1 (stitched) (2).png").size == (800, 200)


def test_ringtone(song):
    assert run("media-ringtone", song) == 0
    out = song.parent / "song (ringtone).m4r"
    assert out.exists() and media.probe(out).acodec == "aac"


def test_csv_to_pdf_table(tmp_path):
    src = tmp_path / "grades.csv"
    src.write_text("name,score\n" + "".join(f"Student {i},{i}\n" for i in range(300)), encoding="utf-8")
    assert run("doc-to-pdf", src) == 0
    with pymupdf.open(tmp_path / "grades.pdf") as d:
        assert d.page_count > 1 and "Student 299" in "".join(p.get_text() for p in d)


def test_video_compatible(tmp_path):
    hevc = tmp_path / "iphone.mov"
    subprocess.run([media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=1:size=320x240:rate=25", "-c:v", "libx265", "-tag:v", "hvc1", str(hevc)],
                   check=True)
    assert media.probe(hevc).vcodec == "hevc"
    assert run("vid-compatible", hevc) == 0
    assert media.probe(tmp_path / "iphone (compatible).mp4").vcodec == "h264"


def test_parallel_batch_convert(tmp_path):
    files = []
    for i in range(12):
        p = tmp_path / f"p{i}.png"
        Image.effect_noise((600, 400), 30).convert("RGB").save(p)
        files.append(p)
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"nope")
    assert run("img-to-jpg", *files, bad) == 1  # one bad file is reported, the rest still convert
    assert len(list(tmp_path.glob("*.jpg"))) == 12


def test_gif_and_slideshow_from_images(tmp_path):
    files = []
    for i, c in enumerate(("red", "green", "blue")):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (640, 480) if i != 1 else (480, 640), c).save(p)
        files.append(p)
    assert run("img-make-gif", *files) == 0
    with Image.open(tmp_path / "0 (animation).gif") as g:
        assert g.n_frames == 3
    assert run("img-slideshow", *files) == 0
    info = media.probe(tmp_path / "0 (slideshow).mp4")
    assert info.vcodec == "h264" and 8.0 < info.duration < 10.5


def test_soundtrack(video, song):
    assert run("media-soundtrack", video, song) == 0
    out = video.parent / "clip (new audio).mp4"
    info = media.probe(out)
    assert info.vcodec == "h264" and info.acodec == "aac" and abs(info.duration - 3.0) < 0.3
    assert run("media-soundtrack", video) == 1  # needs a pair


def test_pdf_tables_to_excel(tmp_path):
    import openpyxl

    d = pymupdf.open()
    page = d.new_page()
    rows = [["Date", "Item", "Amount"], ["2026-01-02", "Coffee", "3.50"], ["2026-01-03", "Books", "1,250.00"],
            ["2026-01-04", "Refund", "(20.00)"]]
    x0, y0, cw, rh = 72, 100, 150, 24
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y1 - 7), val, fontsize=10)
    src = tmp_path / "statement.pdf"
    d.save(src)
    assert run("pdf-to-xlsx", src) == 0
    ws = openpyxl.load_workbook(tmp_path / "statement.xlsx").active
    values = [[c.value for c in r] for r in ws.iter_rows()]
    assert values[0] == ["Date", "Item", "Amount"]
    assert values[2][2] == 1250.0 and values[3][2] == -20.0
    assert run("pdf-to-xlsx", _pdf(tmp_path / "plain.pdf", 1)) == 1


def test_n_up(tmp_path):
    src = _pdf(tmp_path / "slides.pdf", 5)
    assert run("pdf-4up", src) == 0
    with pymupdf.open(tmp_path / "slides (4 per page).pdf") as d:
        assert d.page_count == 2 and "Page 4" in d[0].get_text() and "Page 5" in d[1].get_text()
    assert run("pdf-2up", src) == 0
    with pymupdf.open(tmp_path / "slides (2 per page).pdf") as d:
        assert d.page_count == 3 and d[0].rect.width > d[0].rect.height


def test_trim_margins(tmp_path):
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((250, 380), "small centred text", fontsize=12)
    src = tmp_path / "paper.pdf"
    d.save(src)
    assert run("pdf-trim", src) == 0
    with pymupdf.open(tmp_path / "paper (trimmed).pdf") as o:
        r = o[0].rect
        assert r.width < 250 and r.height < 100 and "small centred text" in o[0].get_text()
    assert run("pdf-trim", tmp_path / "paper (trimmed).pdf") == 1


def test_merge_spreadsheets(tmp_path):
    import openpyxl

    (tmp_path / "jan.csv").write_text("name,amount\nAda,10\nLin,20\n", encoding="utf-8")
    (tmp_path / "feb.csv").write_text("amount,name,note\n5,Kai,late\n", encoding="utf-8")
    assert run("data-merge", tmp_path / "jan.csv", tmp_path / "feb.csv") == 0
    ws = openpyxl.load_workbook(next(tmp_path.glob("*combined*.xlsx"))).active
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    assert rows[0] == ["Source file", "amount", "name", "note"] or rows[0] == ["Source file", "name", "amount", "note"]
    by_name = {r[rows[0].index("name")]: r for r in rows[1:]}
    assert by_name["Kai"][rows[0].index("amount")] == 5 and by_name["Kai"][0] == "feb.csv"
    assert by_name["Ada"][rows[0].index("note")] in ("", None)


def test_password_7z(tmp_path):
    import py7zr

    f = tmp_path / "secret.txt"
    f.write_text("hidden")
    arc = tmp_path / "s.7z"
    with py7zr.SevenZipFile(arc, "w", password="pw") as z:
        z.write(f, "secret.txt")
    f.unlink()
    assert run("arc-extract-folder", arc) == 1  # non-interactive, no password -> friendly error
    assert run("arc-extract-folder", arc, "-p", "password=nope") == 1
    assert run("arc-extract-folder", arc, "-p", "password=pw") == 0
    assert (tmp_path / "s" / "secret.txt").read_text() == "hidden"


def test_audio_extract_is_lossless_copy_when_possible(video):
    assert run("aud-to-m4a", video) == 0  # clip has AAC audio
    out = video.with_suffix(".m4a")
    assert media.probe(out).acodec == "aac"


def test_video_crop_vertical(video):
    assert run("vid-crop-9x16", video) == 0
    info = media.probe(video.parent / "clip (vertical).mp4")
    assert abs(info.width / info.height - 9 / 16) < 0.02 and info.height == 240


def test_white_background_transparent(tmp_path):
    from PIL import ImageDraw

    src = tmp_path / "logo.jpg"
    im = Image.new("RGB", (200, 120), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((40, 30, 160, 90), fill=(20, 60, 200))
    d.rectangle((90, 50, 110, 70), fill="white")  # white enclosed by the logo must stay
    im.save(src, quality=95)
    assert run("img-white-bg", src) == 0
    out = Image.open(tmp_path / "logo (transparent).png")
    assert out.getpixel((5, 5))[3] == 0 and out.getpixel((100, 60))[3] == 255 and out.getpixel((50, 40))[3] == 255


def test_pdf_to_pptx(tmp_path):
    pptx = pytest.importorskip("pptx")
    src = _pdf(tmp_path / "lecture.pdf", 3)
    assert run("pdf-to-pptx", src) == 0
    prs = pptx.Presentation(tmp_path / "lecture.pptx")
    assert len(prs.slides) == 3


def test_pdf_compare(tmp_path):
    def make(path, words):
        d = pymupdf.open()
        p = d.new_page()
        for i, w in enumerate(words):
            p.insert_text((72, 72 + 20 * i), w)
        d.save(path)
        return path

    a = make(tmp_path / "v1.pdf", ["The rent is $900.", "Pets allowed."])
    b = make(tmp_path / "v2.pdf", ["The rent is $950.", "Pets allowed."])
    assert run("pdf-compare", a, b) == 0
    html = (tmp_path / "v1 (comparison).html").read_text(encoding="utf-8")
    assert "rent" in html and "diff_chg" in html and "1 changed passage" in html  # $9[0→5]0 highlighted
    assert run("pdf-compare", a, a) == 1  # identical: friendly message


def test_rename_rolls_back_on_failure(tmp_path, monkeypatch):
    files = []
    for n in ("a.txt", "b.txt", "c.txt"):
        p = tmp_path / n
        p.write_text(n)
        files.append(p)
    real = Path.rename
    calls = {"n": 0}

    def flaky(self, target):
        calls["n"] += 1
        if calls["n"] == 5:  # fail during the second phase
            raise PermissionError(13, "in use")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    assert run("rename", *files, "-p", "pattern=New {n}") == 1
    monkeypatch.setattr(Path, "rename", real)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "b.txt", "c.txt"]
    assert (tmp_path / "b.txt").read_text() == "b.txt"


def test_read_only_folder_falls_back_to_downloads(tmp_path, monkeypatch):
    from filewizard import util

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    real = util._writable
    monkeypatch.setattr(util, "_writable", lambda folder: False if folder.name == "ro" else real(folder))
    ro = tmp_path / "ro"
    ro.mkdir()
    src = ro / "pic.png"
    Image.new("RGB", (10, 10)).save(src)
    assert run("img-to-jpg", src) == 0
    assert (tmp_path / "home" / "Downloads" / "FileWizard" / "pic.jpg").exists()
    assert not (ro / "pic.jpg").exists()


def test_unicode_and_emoji_filenames(tmp_path, clip):
    name = "Café résumé 📷 – 日本"
    img = tmp_path / f"{name}.png"
    Image.new("RGB", (20, 20), "teal").save(img)
    assert run("img-to-jpg", img) == 0 and (tmp_path / f"{name}.jpg").exists()
    pdf_a = _pdf(tmp_path / f"{name} A.pdf", 1)
    pdf_b = _pdf(tmp_path / f"{name} B.pdf", 1)
    assert run("combine-pdf", pdf_a, pdf_b) == 0
    vid = tmp_path / f"{name}.mp4"
    shutil.copy(clip, vid)
    assert run("aud-to-mp3", vid) == 0 and (tmp_path / f"{name}.mp3").exists()


@pytest.mark.skipif(not pdf.tessdata(), reason="Tesseract data not installed")
def test_photos_to_searchable_pdf(tmp_path):
    from PIL import ImageDraw, ImageFont

    files = []
    for i, word in enumerate(("Receipt", "Invoice")):
        im = Image.new("RGB", (1200, 400), "white")
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 80)
        except OSError:
            font = ImageFont.load_default(size=80)
        ImageDraw.Draw(im).text((60, 150), f"{word} number {i + 1}", fill="black", font=font)
        p = tmp_path / f"photo{i}.jpg"
        im.save(p)
        files.append(p)
    assert run("img-scan-pdf", *files) == 0
    with pymupdf.open(tmp_path / "photo0 (searchable).pdf") as d:
        assert d.page_count == 2 and "Invoice" in d[1].get_text()
    assert not list(tmp_path.glob("*scan tmp*"))


def test_font_sample(tmp_path):
    import glob

    fonts = glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) + glob.glob("C:/Windows/Fonts/arial.ttf")
    if not fonts:
        pytest.skip("no TTF font on this machine")
    src = tmp_path / "MyFont.ttf"
    shutil.copy(fonts[0], src)
    assert run("font-sample", src) == 0
    assert Image.open(tmp_path / "MyFont (sample).png").size[0] == 1600


def test_more_containers_registered():
    from filewizard.operations import for_ext

    for ext in (".rmvb", ".divx", ".caf", ".pages", ".numbers", ".key", ".vsdx", ".otf"):
        assert for_ext(ext), ext


def test_tar_symlink_escape_blocked(tmp_path):
    import io

    evil = tmp_path / "evil.tar"
    with tarfile.open(evil, "w") as t:
        info = tarfile.TarInfo("innocent")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../../etc/passwd"
        t.addfile(info)
        data = b"x"
        f = tarfile.TarInfo("ok.txt")
        f.size = len(data)
        t.addfile(f, io.BytesIO(data))
    assert run("arc-extract-folder", evil) == 1
    assert not (tmp_path / "evil").exists()


def test_thumbnail_grid(video):
    assert run("vid-grid", video) == 0
    im = Image.open(video.parent / "clip (thumbnails).jpg")
    assert im.width > 4 * 480 and im.height > 4 * 300
