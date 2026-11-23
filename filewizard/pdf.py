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
