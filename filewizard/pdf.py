"""PDF tools (PyMuPDF)."""

from __future__ import annotations

from pathlib import Path

from .util import UserError, human_size, parse_page_ranges, sibling, unique_dir


def _pymupdf():
    import pymupdf

    return pymupdf


def _open(path: Path, password: str | None = None):
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise UserError(f"{path.name} doesn't look like a valid PDF ({e}).") from e
    if doc.needs_pass:
        if password is None or not doc.authenticate(password):
            doc.close()
            raise UserError(
                f"{path.name} is password-protected. Use 'Unlock PDF' first."
                if password is None else f"Wrong password for {path.name}."
            )
    return doc


def _save(doc, out: Path, **kw):
    doc.save(out, garbage=3, deflate=True, **kw)
    doc.close()
    return out


# ---------- conversions ----------

def to_images(src: Path, ext: str, dpi: int = 200) -> list[Path]:
    """One image per page. Multi-page PDFs get their own folder."""
    doc = _open(src)
    fmt = {".png": "png", ".jpg": "jpeg"}[ext]
    outs: list[Path] = []
    if doc.page_count == 1:
        targets = [sibling(src, None, ext)]
    else:
        folder = unique_dir(src.with_name(f"{src.stem} ({ext[1:]})"))
        width = len(str(doc.page_count))
        targets = [folder / f"{src.stem} - page {i + 1:0{width}d}{ext}" for i in range(doc.page_count)]
    for page, out in zip(doc, targets):
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        if fmt == "jpeg":
            pix.save(out, output="jpeg", jpg_quality=90)
        else:
            pix.save(out)
        outs.append(out)
    doc.close()
    return outs


def to_text(src: Path) -> list[Path]:
    doc = _open(src)
    text = "\n\n".join(f"--- Page {i + 1} ---\n{p.get_text()}" for i, p in enumerate(doc))
    doc.close()
    if not text.replace("--- Page", "").strip(" -\n0123456789"):
        raise UserError(f"{src.name} has no selectable text (it's probably a scan). OCR isn't supported yet.")
    out = sibling(src, None, ".txt")
    out.write_text(text, encoding="utf-8")
    return [out]


def to_docx(src: Path) -> list[Path]:
    try:
        from pdf2docx import Converter
    except ImportError as e:
        raise UserError("PDF → Word needs the 'pdf2docx' package (pip install pdf2docx).") from e
    _open(src).close()  # friendly error for encrypted/broken files
    out = sibling(src, None, ".docx")
    cv = Converter(str(src))
    try:
        cv.convert(str(out))
    finally:
        cv.close()
    return [out]


def extract_images(src: Path) -> list[Path]:
    doc = _open(src)
    pymupdf = _pymupdf()
    seen: set[int] = set()
    folder = None
    outs: list[Path] = []
    for pno, page in enumerate(doc, 1):
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            info = doc.extract_image(xref)
            if not info or info["width"] < 16 or info["height"] < 16:  # skip specks/spacers
                continue
            if folder is None:
                folder = unique_dir(src.with_name(f"{src.stem} (images)"))
            ext = info["ext"]
            if ext not in ("png", "jpeg", "jpg"):
                # odd encodings (jpx, jbig2, ...) -> PNG for compatibility
                pix = pymupdf.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                out = folder / f"page {pno} - image {len(outs) + 1}.png"
                pix.save(out)
            else:
                out = folder / f"page {pno} - image {len(outs) + 1}.{ext}"
                out.write_bytes(info["image"])
            outs.append(out)
    doc.close()
    if not outs:
        raise UserError(f"No images found in {src.name}.")
    return outs


def images_to_pdf(images: list[Path], out: Path | None = None) -> list[Path]:
    from .images import to_pdf_bytes

    pymupdf = _pymupdf()
    doc = pymupdf.open()
    for img in images:
        data, w, h = to_pdf_bytes(img)
        page = doc.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=data)
    if out is None:
        out = sibling(images[0], "combined" if len(images) > 1 else None, ".pdf")
    return [_save(doc, out)]


# ---------- page tools ----------

def merge(files: list[Path]) -> list[Path]:
    if len(files) < 2:
        raise UserError("Select at least two PDFs to merge (select them all, then right-click one).")
    pymupdf = _pymupdf()
    out_doc = pymupdf.open()
    for f in files:
        d = _open(f)
        out_doc.insert_pdf(d)
        d.close()
    return [_save(out_doc, sibling(files[0], "merged", ".pdf"))]


def split(src: Path) -> list[Path]:
    pymupdf = _pymupdf()
    doc = _open(src)
    if doc.page_count < 2:
        raise UserError(f"{src.name} has only one page.")
    folder = unique_dir(src.with_name(f"{src.stem} (pages)"))
    width = len(str(doc.page_count))
    outs = []
    for i in range(doc.page_count):
        single = pymupdf.open()
        single.insert_pdf(doc, from_page=i, to_page=i)
        outs.append(_save(single, folder / f"{src.stem} - page {i + 1:0{width}d}.pdf"))
    doc.close()
    return outs


def extract_pages(src: Path, pages: str) -> list[Path]:
    doc = _open(src)
    idx = parse_page_ranges(pages, doc.page_count)
    doc.select(idx)
    label = "pages " + pages.replace(" ", "")
    return [_save(doc, sibling(src, label[:40], ".pdf"))]


def delete_pages(src: Path, pages: str) -> list[Path]:
    doc = _open(src)
    drop = set(parse_page_ranges(pages, doc.page_count))
    keep = [i for i in range(doc.page_count) if i not in drop]
    if not keep:
        raise UserError("That would delete every page.")
    doc.select(keep)
    return [_save(doc, sibling(src, "edited", ".pdf"))]


def reverse(src: Path) -> list[Path]:
    doc = _open(src)
    doc.select(list(range(doc.page_count - 1, -1, -1)))
    return [_save(doc, sibling(src, "reversed", ".pdf"))]


def rotate(src: Path, degrees_clockwise: int) -> list[Path]:
    doc = _open(src)
    for page in doc:
        page.set_rotation((page.rotation + degrees_clockwise) % 360)
    label = {90: "rotated right", 270: "rotated left", 180: "rotated 180"}[degrees_clockwise]
    return [_save(doc, sibling(src, label, ".pdf"))]


def add_page_numbers(src: Path) -> list[Path]:
    pymupdf = _pymupdf()
    doc = _open(src)
    n = doc.page_count
    for i, page in enumerate(doc, 1):
        r = page.rect  # visible (rotated) page rectangle
        text = f"{i} / {n}"
        size = max(8, min(11, r.width / 55))
        width = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
        visible_point = pymupdf.Point((r.width - width) / 2, r.height - 0.45 * 72 + size / 2)
        # insert_text works in unrotated coordinates; map the point back and rotate the text.
        page.insert_text(
            visible_point * page.derotation_matrix, text,
            fontsize=size, fontname="helv", color=(0.25, 0.25, 0.25), rotate=page.rotation,
        )
    return [_save(doc, sibling(src, "numbered", ".pdf"))]


def compress(src: Path) -> list[Path]:
    doc = _open(src)
    before = src.stat().st_size
    if hasattr(doc, "rewrite_images"):
        # Downsample images above 150 dpi and re-encode as JPEG q75: where most PDF bloat lives.
        doc.rewrite_images(dpi_threshold=180, dpi_target=150, quality=75, lossy=True, lossless=True)
    out = sibling(src, "compressed", ".pdf")
    doc.save(out, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
    doc.close()
    after = out.stat().st_size
    if after > before * 0.95 or before - after < 5 * 1024:  # not worth a second copy
        out.unlink()
        raise UserError(f"{src.name} is already about as small as it gets ({human_size(before)}); nothing saved.")
    return [out]


def protect(src: Path, password: str) -> list[Path]:
    pymupdf = _pymupdf()
    doc = _open(src)
    perms = (pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY | pymupdf.PDF_PERM_ANNOTATE
             | pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_PRINT_HQ)
    out = sibling(src, "protected", ".pdf")
    doc.save(out, garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw=password, owner_pw=password, permissions=perms)
    doc.close()
    return [out]


def is_locked(src: Path) -> bool:
    doc = _pymupdf().open(src)
    try:
        return bool(doc.needs_pass)
    finally:
        doc.close()


def unlock(src: Path, password: str | None) -> list[Path]:
    pymupdf = _pymupdf()
    try:
        doc = pymupdf.open(src)
    except Exception as e:
        raise UserError(f"{src.name} doesn't look like a valid PDF ({e}).") from e
    # Check status BEFORE authenticating: in PyMuPDF 2.x, reading `needs_pass` after
    # authenticate() makes the decrypted save write corrupt (blank) pages.
    locked = bool(doc.needs_pass)
    encrypted = locked or bool(doc.is_encrypted) or bool(doc.metadata.get("encryption"))
    if not encrypted:
        doc.close()
        raise UserError(f"{src.name} isn't password-protected.")
    if locked and not doc.authenticate(password or ""):
        doc.close()
        raise UserError(f"Wrong password for {src.name}.")
    out = sibling(src, "unlocked", ".pdf")
    doc.save(out, garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)
    doc.close()
    return [out]


# ---------- more tools ----------

def watermark(src: Path, text: str) -> list[Path]:
    """Big faint diagonal text across every page (e.g. DRAFT, CONFIDENTIAL, your name)."""
    pymupdf = _pymupdf()
    text = text.strip()
    if not text:
        raise UserError("Watermark text can't be empty.")
    doc = _open(src)
    for page in doc:
        r = page.rect
        diag = (r.width ** 2 + r.height ** 2) ** 0.5
        size = min(120, diag * 0.75 / max(len(text), 1) / 0.55)
        tw = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
        import math

        angle = math.degrees(math.atan2(r.height, r.width))
        center = pymupdf.Point(r.width / 2, r.height / 2)
        start = pymupdf.Point(center.x - tw / 2, center.y + size * 0.35)
        mat = pymupdf.Matrix(-angle)  # negative = rising left-to-right on screen
        page.insert_text(start * page.derotation_matrix, text, fontsize=size, fontname="helv",
                         color=(0.5, 0.5, 0.5), fill_opacity=0.18, stroke_opacity=0.18,
                         morph=(center * page.derotation_matrix, mat * pymupdf.Matrix(page.rotation)),
                         overlay=True)
    return [_save(doc, sibling(src, "watermarked", ".pdf"))]


def _is_blank(page) -> bool:
    if page.get_text("text").strip():
        return False
    pix = page.get_pixmap(dpi=30, colorspace=_pymupdf().csGRAY, alpha=False)
    samples = pix.samples
    dark = sum(1 for b in samples if b < 230)
    return dark <= len(samples) * 0.002  # scanner specks allowed


def remove_blank_pages(src: Path) -> list[Path]:
    doc = _open(src)
    keep = [i for i, p in enumerate(doc) if not _is_blank(p)]
    removed = doc.page_count - len(keep)
    if removed == 0:
        doc.close()
        raise UserError(f"No blank pages found in {src.name}.")
    if not keep:
        doc.close()
        raise UserError(f"Every page in {src.name} looks blank; nothing saved.")
    doc.select(keep)
    return [_save(doc, sibling(src, f"{removed} blank removed", ".pdf"))]


def flatten(src: Path) -> list[Path]:
    """Burn filled-in form fields and annotations into the page so they can't be edited/lost."""
    doc = _open(src)
    if not hasattr(doc, "bake"):
        raise UserError("Flattening needs a newer PyMuPDF (pip install -U pymupdf).")
    doc.bake(annots=True, widgets=True)
    return [_save(doc, sibling(src, "flattened", ".pdf"))]


def grayscale(src: Path, dpi: int = 200) -> list[Path]:
    """Black & white copy (pages are re-rendered as images: good for printing/sending scans)."""
    pymupdf = _pymupdf()
    doc = _open(src)
    out_doc = pymupdf.open()
    from .util import report

    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
        new = out_doc.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("jpeg", jpg_quality=85))
        report((i + 1) / doc.page_count, f"Page {i + 1} of {doc.page_count}")
    doc.close()
    return [_save(out_doc, sibling(src, "grayscale", ".pdf"))]


# ---------- OCR (needs Tesseract) ----------

def tessdata() -> str | None:
    import os

    env = os.environ.get("TESSDATA_PREFIX")
    if env and Path(env).exists():
        return env
    for base in (r"C:\Program Files\Tesseract-OCR\tessdata", r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
                 str(Path.home() / "AppData/Local/Programs/Tesseract-OCR/tessdata"),
                 "/usr/share/tesseract-ocr/5/tessdata", "/usr/share/tesseract-ocr/4.00/tessdata",
                 "/usr/share/tessdata", "/opt/homebrew/share/tessdata"):
        if Path(base, "eng.traineddata").exists():
            return base
    return None


def _need_tesseract() -> str:
    td = tessdata()
    if not td:
        raise UserError("Text recognition (OCR) needs the free Tesseract engine.\n\n"
                        "Install it from https://github.com/UB-Mannheim/tesseract/wiki "
                        "(keep the default folder), then try again.")
    return td


def ocr(src: Path, dpi: int = 300) -> list[Path]:
    """Make a scanned PDF searchable/selectable: adds an invisible text layer."""
    pymupdf = _pymupdf()
    td = _need_tesseract()
    from .util import report

    doc = _open(src)
    out_doc = pymupdf.open()
    for i, page in enumerate(doc):
        report(i / doc.page_count, f"Reading page {i + 1} of {doc.page_count}")
        if page.get_text().strip():  # already has text: keep the page as-is
            out_doc.insert_pdf(doc, from_page=i, to_page=i)
            continue
        pix = page.get_pixmap(dpi=dpi)
        ocr_page = pymupdf.open("pdf", pix.pdfocr_tobytes(language="eng", tessdata=td))
        out_doc.insert_pdf(ocr_page)
    doc.close()
    return [_save(out_doc, sibling(src, "searchable", ".pdf"))]


def image_to_text(src: Path) -> list[Path]:
    """OCR a screenshot/photo of text into a .txt file."""
    pymupdf = _pymupdf()
    td = _need_tesseract()
    from .images import open_image
    import io

    img = open_image(src).convert("RGB")
    if max(img.size) < 1500:  # upscale small screenshots: Tesseract likes ~300 dpi text
        f = 1500 / max(img.size)
        img = img.resize((round(img.width * f), round(img.height * f)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    pix = pymupdf.Pixmap(buf.getvalue())
    d = pymupdf.open("pdf", pix.pdfocr_tobytes(language="eng", tessdata=td))
    text = d[0].get_text()
    if not text.strip():
        raise UserError(f"No text found in {src.name}.")
    out = sibling(src, None, ".txt")
    out.write_text(text, encoding="utf-8")
    return [out]


def any_to_pdf(src: Path, out: Path) -> Path:
    """Convert one file of any supported kind to a PDF at `out` (used by 'Combine into one PDF')."""
    from . import docs, office
    from .images import IMAGE_EXTS

    ext = src.suffix.lower()
    if ext == ".pdf":
        import shutil

        shutil.copyfile(src, out)
    elif ext in IMAGE_EXTS:
        images_to_pdf([src], out)
    elif ext in docs.EBOOK_EXTS:
        tmp = docs.ebook_to_pdf(src)[0]
        tmp.replace(out)
    elif ext in (".md", ".markdown", ".html", ".htm"):
        tmp = docs.markup_to_pdf(src)[0]
        tmp.replace(out)
    elif ext in office.WORD_EXTS | office.SLIDE_EXTS | office.SHEET_EXTS:
        office.convert(src, ".pdf", out)
    else:
        raise UserError(f"Can't add {src.name} to a PDF.")
    return out


def combine_any(files: list[Path]) -> list[Path]:
    """Merge PDFs, images, Office docs, ebooks... into a single PDF, in the given order."""
    import tempfile

    from .util import report

    if len(files) < 2 and files and files[0].suffix.lower() == ".pdf":
        raise UserError("Select at least two files to merge (select them all, then right-click one).")
    pymupdf = _pymupdf()
    out_doc = pymupdf.open()
    toc = []
    with tempfile.TemporaryDirectory(prefix="filewizard-") as td:
        for i, f in enumerate(files):
            report(i / len(files), f"Adding {f.name}")
            if f.suffix.lower() == ".pdf":
                d = _open(f)
            else:
                d = pymupdf.open(any_to_pdf(f, Path(td) / f"{i}.pdf"))
            toc.append([1, f.stem, out_doc.page_count + 1])  # bookmark per source file
            out_doc.insert_pdf(d)
            d.close()
    if len(files) > 1:
        out_doc.set_toc(toc)
    label = "merged" if all(f.suffix.lower() == ".pdf" for f in files) else "combined"
    return [_save(out_doc, sibling(files[0], label, ".pdf"))]


def compress_to(src: Path, max_bytes: int) -> list[Path]:
    """Fit a PDF under an upload limit, trying the gentlest settings first."""
    import shutil
    import tempfile

    from .util import report

    pymupdf = _pymupdf()
    before = src.stat().st_size
    label = f"under {max_bytes / 1024 / 1024:g} MB" if max_bytes >= 1024 * 1024 else f"under {max_bytes // 1024} KB"
    if before <= max_bytes:
        raise UserError(f"{src.name} is already {human_size(before)}, under the limit.")
    steps = [(200, 80), (150, 70), (120, 60), (96, 50), (72, 40), (50, 30)]
    with tempfile.TemporaryDirectory(prefix="fw-pdfsize-") as td:
        for i, (dpi, quality) in enumerate(steps):
            report(i / len(steps), f"Trying {dpi} dpi images at quality {quality}")
            doc = _open(src)
            if hasattr(doc, "rewrite_images"):
                doc.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=quality, lossy=True,
                                   lossless=True, bitonal=False, color=True, gray=True)
            trial = Path(td) / f"t{i}.pdf"
            doc.save(trial, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
            doc.close()
            if trial.stat().st_size <= max_bytes:
                out = sibling(src, label, ".pdf")
                shutil.move(str(trial), out)
                return [out]
    raise UserError(f"Couldn't get {src.name} under {label[6:]} (best effort was still too big). "
                    "It's probably mostly text/vector content, which images settings can't shrink.")
