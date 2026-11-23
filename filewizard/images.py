"""Image conversions and tools (Pillow + pillow-heif + rawpy + PyMuPDF for SVG)."""

from __future__ import annotations

import io
from pathlib import Path

from .util import UserError, sibling

RAW_EXTS = {".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf", ".rw2", ".raf",
            ".pef", ".srw", ".x3f", ".3fr", ".erf", ".kdc", ".mrw", ".raw", ".rwl", ".iiq"}
VECTOR_EXTS = {".svg"}
RASTER_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic", ".heif",
               ".avif", ".ico", ".psd", ".tga", ".jp2", ".j2k", ".dds", ".pcx", ".ppm", ".pgm", ".pbm",
               ".qoi", ".xbm"}
IMAGE_EXTS = RASTER_EXTS | RAW_EXTS | VECTOR_EXTS

_heif_registered = False


def _register_heif():
    global _heif_registered
    if not _heif_registered:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
            _heif_registered = True
        except ImportError as e:
            raise UserError("HEIC support needs the 'pillow-heif' package (pip install pillow-heif).") from e


def _open_raw(path: Path):
    try:
        import rawpy
    except ImportError as e:
        raise UserError("Camera RAW support needs the 'rawpy' package (pip install rawpy).") from e
    from PIL import Image

    try:
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
    except Exception as e:
        raise UserError(f"Couldn't read the RAW photo {path.name}: {e}") from e
    return Image.fromarray(rgb)


def _open_svg(path: Path, target_px: int = 2048):
    import pymupdf
    from PIL import Image

    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise UserError(f"Couldn't read the SVG {path.name}: {e}") from e
    page = doc[0]
    zoom = max(1.0, target_px / max(page.rect.width, page.rect.height, 1))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
    img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def open_image(path: Path):
    """Open any supported image, upright (EXIF rotation applied), fully loaded."""
    from PIL import Image, ImageOps

    ext = path.suffix.lower()
    if ext in RAW_EXTS:
        return _open_raw(path)
    if ext in VECTOR_EXTS:
        return _open_svg(path)
    if ext in (".heic", ".heif", ".avif"):
        _register_heif()
    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        raise UserError(f"Couldn't open {path.name} as an image ({type(e).__name__}: {e}).") from e
    # Phone photos store rotation in EXIF; apply it so the output isn't sideways.
    return ImageOps.exif_transpose(img) or img


_open = open_image  # backwards-compatible name


def is_animated(path: Path) -> bool:
    if path.suffix.lower() not in (".gif", ".webp", ".png", ".apng"):
        return False
    from PIL import Image

    try:
        with Image.open(path) as im:
            return getattr(im, "is_animated", False) and im.n_frames > 1
    except Exception:
        return False


def _flatten(img, background=(255, 255, 255)):
    """Formats without transparency (JPG, BMP) need a solid background."""
    from PIL import Image

    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    if img.mode in ("I;16", "I;16B", "I", "F"):  # 16-bit/float scans
        from PIL import ImageOps

        return ImageOps.autocontrast(img.convert("L")).convert("RGB") if img.mode != "I;16" else \
            img.point(lambda v: v / 256).convert("RGB")
    return img.convert("RGB")


ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

FORMATS = {
    # ext: (Pillow format, needs flatten, save kwargs)
    ".png": ("PNG", False, {"optimize": True}),
    ".jpg": ("JPEG", True, {"quality": 92, "optimize": True, "subsampling": "4:2:0"}),
    ".webp": ("WEBP", False, {"quality": 90, "method": 5}),
    ".heic": ("HEIF", False, {"quality": 85}),
    ".avif": ("AVIF", False, {"quality": 80}),
    ".bmp": ("BMP", True, {}),
    ".tiff": ("TIFF", False, {"compression": "tiff_lzw"}),
    ".gif": ("GIF", False, {}),
    ".ico": ("ICO", False, {"sizes": ICO_SIZES}),
}


def save_as(img, out: Path, ext: str, keep_exif: bytes | None = None, **overrides):
    from PIL import Image

    fmt, flatten, kwargs = FORMATS[ext]
    kwargs = {**kwargs, **overrides}
    if fmt == "HEIF":
        _register_heif()
    if flatten:
        img = _flatten(img)
    elif img.mode not in ("RGB", "RGBA", "L", "LA") or (fmt in ("HEIF", "AVIF") and img.mode in ("L", "LA")):
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        img = img.convert("RGBA" if has_alpha else "RGB")
    if fmt == "ICO":
        side = max(img.size)  # icons are square; pad instead of stretching
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = square
        kwargs["sizes"] = [s for s in kwargs["sizes"] if s[0] <= side] or [(side, side)]
    if keep_exif and fmt in ("JPEG", "WEBP", "PNG", "HEIF", "AVIF", "TIFF"):
        kwargs["exif"] = keep_exif
    img.save(out, fmt, **kwargs)
    return out


def _exif_without_orientation(src_img) -> bytes | None:
    """Keep date/camera info when converting, but drop orientation (we already applied it)."""
    try:
        exif = src_img.getexif()
        if not exif:
            return None
        exif.pop(0x0112, None)
        return exif.tobytes()
    except Exception:
        return None


def convert(src: Path, ext: str) -> list[Path]:
    from PIL import Image

    out = sibling(src, None, ext)
    if ext in (".gif", ".webp") and is_animated(src):  # keep animation
        with Image.open(src) as im:
            frames, durations = [], []
            for i in range(im.n_frames):
                im.seek(i)
                frames.append(im.convert("RGBA"))
                durations.append(im.info.get("duration", 100))
            frames[0].save(out, FORMATS[ext][0], save_all=True, append_images=frames[1:],
                           duration=durations, loop=0, disposal=2)
        return [out]
    img = open_image(src)
    return [save_as(img, out, ext, keep_exif=_exif_without_orientation(img))]


def resize(src: Path, spec: str) -> list[Path]:
    """spec: '50%' or '1920' (longest side in px) or '800x600' (fit inside box)."""
    from PIL import Image

    img = open_image(src)
    w, h = img.size
    s = spec.strip().lower().replace(" ", "").replace("px", "")
    try:
        if s.endswith("%"):
            f = float(s[:-1]) / 100
        elif "x" in s:
            bw, bh = (int(v) for v in s.split("x", 1))
            f = min(bw / w, bh / h)
        else:
            f = int(s) / max(w, h)
    except (ValueError, ZeroDivisionError) as e:
        raise UserError(f"Couldn't understand size '{spec}'. Try 50%, 1920, or 800x600.") from e
    new = (round(w * f), round(h * f))
    if min(new) < 1:
        raise UserError("That would make the image smaller than 1 pixel.")
    if max(new) > 30000:
        raise UserError("That would be enormous (over 30,000 pixels). Pick a smaller size.")
    ext = out_ext(src)
    out = sibling(src, f"{new[0]}x{new[1]}", ext)
    return [save_as(img.resize(new, Image.LANCZOS), out, ext, keep_exif=_exif_without_orientation(img))]


def compress(src: Path) -> list[Path]:
    """Re-encode smaller: JPG q72 for photos, palette PNG for graphics with transparency."""
    img = open_image(src)
    has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    if has_alpha:
        out = sibling(src, "compressed", ".png")
        save_as(img.convert("RGBA").quantize(256, method=2), out, ".png")
    else:
        out = sibling(src, "compressed", ".jpg")
        save_as(img, out, ".jpg", quality=72, progressive=True)
    if out.stat().st_size >= src.stat().st_size * 0.95:
        out.unlink()
        raise UserError(f"{src.name} is already well compressed; the result wasn't smaller, so nothing was saved.")
    return [out]


def strip_metadata(src: Path) -> list[Path]:
    """Remove EXIF/GPS etc. by copying only the pixels."""
    from PIL import Image

    img = open_image(src)
    clean = Image.frombytes(img.mode, img.size, img.tobytes())
    if img.mode == "P" and img.getpalette():
        clean.putpalette(img.getpalette())
        if "transparency" in img.info:
            clean.info["transparency"] = img.info["transparency"]
    ext = out_ext(src)
    return [save_as(clean, sibling(src, "no metadata", ext), ext)]


def transform(src: Path, how: str) -> list[Path]:
    from PIL import Image, ImageOps

    img = open_image(src)
    ops = {
        "right": (lambda i: i.transpose(Image.Transpose.ROTATE_270), "rotated right"),
        "left": (lambda i: i.transpose(Image.Transpose.ROTATE_90), "rotated left"),
        "180": (lambda i: i.transpose(Image.Transpose.ROTATE_180), "rotated 180"),
        "hflip": (lambda i: i.transpose(Image.Transpose.FLIP_LEFT_RIGHT), "mirrored"),
        "vflip": (lambda i: i.transpose(Image.Transpose.FLIP_TOP_BOTTOM), "flipped"),
        "gray": (lambda i: _gray_keep_alpha(i) if i.mode in ("RGBA", "LA", "PA")
                 else ImageOps.grayscale(i), "grayscale"),
        "square": (_pad_square, "square"),
        "trim": (_trim_borders, "cropped"),
    }
    fn, label = ops[how]
    ext = out_ext(src)
    return [save_as(fn(img), sibling(src, label, ext), ext, keep_exif=_exif_without_orientation(img))]


def rotate(src: Path, degrees_clockwise: int) -> list[Path]:
    return transform(src, {90: "right", 270: "left", 180: "180"}[degrees_clockwise])


def _gray_keep_alpha(img):
    from PIL import ImageOps

    rgba = img.convert("RGBA")
    g = ImageOps.grayscale(rgba).convert("LA")
    g.putalpha(rgba.getchannel("A"))
    return g


def _pad_square(img):
    """Pad to a square (for profile pictures/Instagram) using white, or transparent if it has alpha."""
    from PIL import Image

    side = max(img.size)
    has_alpha = img.mode in ("RGBA", "LA", "PA")
    canvas = Image.new("RGBA" if has_alpha else "RGB", (side, side), (0, 0, 0, 0) if has_alpha else (255, 255, 255))
    canvas.paste(img.convert(canvas.mode), ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


def _trim_borders(img):
    """Crop away uniform borders (whitespace around scans, screenshots, logos)."""
    from PIL import Image, ImageChops

    if img.mode in ("RGBA", "LA"):
        bbox = img.getchannel("A").getbbox()
    else:
        rgb = img.convert("RGB")
        bg = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        diff = ImageChops.difference(rgb, bg).convert("L").point(lambda v: 255 if v > 18 else 0)
        bbox = diff.getbbox()
    if not bbox or bbox == (0, 0, *img.size):
        raise UserError("No plain border found to crop.")
    return img.crop(bbox)


def remove_background(src: Path) -> list[Path]:
    try:
        from rembg import remove  # optional, ~170 MB AI model downloaded on first use
    except ImportError as e:
        raise UserError("Background removal needs the optional AI add-on:\n\n    pip install rembg\n\n"
                        "(it downloads a ~170 MB model the first time).") from e
    img = open_image(src)
    out = sibling(src, "no background", ".png")
    save_as(remove(img), out, ".png")
    return [out]


def out_ext(src: Path) -> str:
    """Keep the source format where we can write it; HEIC/RAW/etc. fall back sensibly."""
    e = src.suffix.lower()
    e = {".jpeg": ".jpg", ".jfif": ".jpg", ".tif": ".tiff", ".heif": ".heic"}.get(e, e)
    if e in FORMATS:
        return e
    return ".png" if e in VECTOR_EXTS | {".psd", ".tga", ".dds", ".qoi"} else ".jpg"


def to_pdf_bytes(src: Path, max_w_pt: float | None = None, max_h_pt: float | None = None) -> tuple[bytes, float, float]:
    """Encode an image for embedding in a PDF and pick a page size.

    The page fits the image inside Letter or A4 (Settings), rotated to match the image's orientation,
    so phone photos print sensibly instead of becoming 50-inch pages.
    """
    from .util import paper

    if max_w_pt is None or max_h_pt is None:
        max_w_pt, max_h_pt = (595, 842) if paper() == "a4" else (612, 792)
    img = open_image(src)
    w, h = img.size
    if w > h:
        max_w_pt, max_h_pt = max(max_w_pt, max_h_pt), min(max_w_pt, max_h_pt)
    scale = min(max_w_pt / w, max_h_pt / h)
    buf = io.BytesIO()
    if src.suffix.lower() in (".jpg", ".jpeg", ".jfif", ".heic", ".heif", ".avif") or src.suffix.lower() in RAW_EXTS:
        _flatten(img).save(buf, "JPEG", quality=92)  # photos: stay lossy, stay small
    else:
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        img.convert("RGBA" if has_alpha else "RGB").save(buf, "PNG")
    return buf.getvalue(), w * scale, h * scale


def contact_sheet(images: list[Path], cols: int = 4, thumb: int = 400) -> list[Path]:
    """Grid of thumbnails with filenames: one image showing many."""
    from PIL import Image, ImageDraw

    thumbs = []
    for p in images:
        im = open_image(p)
        im.thumbnail((thumb, thumb))
        thumbs.append((p.name, _flatten(im)))
    cols = min(cols, len(thumbs))
    rows = -(-len(thumbs) // cols)
    pad, label_h = 16, 28
    W = cols * (thumb + pad) + pad
    H = rows * (thumb + label_h + pad) + pad
    sheet = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    for i, (name, im) in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = pad + c * (thumb + pad)
        y = pad + r * (thumb + label_h + pad)
        sheet.paste(im, (x + (thumb - im.width) // 2, y + (thumb - im.height) // 2))
        d.text((x, y + thumb + 6), name[:48], fill=(40, 40, 40))
    out = sibling(images[0], "contact sheet", ".jpg")
    sheet.save(out, "JPEG", quality=88)
    return [out]


def compress_to(src: Path, max_bytes: int) -> list[Path]:
    """Smallest visible change that gets under a size limit (upload forms: 'max 500 KB').

    Binary-searches JPEG quality; only if quality 40 is still too big does it scale down."""
    img = _flatten(open_image(src))
    if src.stat().st_size <= max_bytes and src.suffix.lower() in (".jpg", ".jpeg"):
        raise UserError(f"{src.name} is already under {_kb(max_bytes)}.")

    def encode(im, q):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        return buf.getvalue()

    best = None
    scale = 1.0
    from PIL import Image

    for _ in range(12):
        im = img if scale == 1.0 else img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                                                  Image.LANCZOS)
        lo, hi = 40, 92
        while lo <= hi:
            q = (lo + hi) // 2
            data = encode(im, q)
            if len(data) <= max_bytes:
                best, lo = data, q + 1
            else:
                hi = q - 1
        if best is not None:
            break
        scale *= 0.8
    if best is None:
        raise UserError(f"Couldn't get {src.name} under {_kb(max_bytes)}.")
    out = sibling(src, f"under {_kb(max_bytes)}", ".jpg")
    out.write_bytes(best)
    return [out]


def _kb(n: int) -> str:
    return f"{n / 1024 / 1024:g} MB" if n >= 1024 * 1024 else f"{n // 1024} KB"


def describe(path: Path) -> str:
    """Human-readable details for the 'Show details' action."""
    img = None
    lines = []
    try:
        from PIL import Image

        if path.suffix.lower() in RAW_EXTS | VECTOR_EXTS:
            img = open_image(path)
            fmt = path.suffix[1:].upper()
        else:
            if path.suffix.lower() in (".heic", ".heif", ".avif"):
                _register_heif()
            img = Image.open(path)
            fmt = img.format
        lines.append(f"Image: {img.width} × {img.height} px, {fmt}, {img.mode}")
        if getattr(img, "n_frames", 1) > 1:
            lines.append(f"Frames: {img.n_frames} (animated)")
        dpi = img.info.get("dpi")
        if dpi:
            lines.append(f"DPI: {round(dpi[0])}")
        exif = img.getexif() if hasattr(img, "getexif") else {}
        tags = {0x010F: "Camera make", 0x0110: "Camera model", 0x0132: "Date", 0x0131: "Software"}
        for tag, name in tags.items():
            if exif.get(tag):
                lines.append(f"{name}: {exif[tag]}")
        sub = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
        if sub.get(0x9003):
            lines.append(f"Taken: {sub[0x9003]}")
        gps = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else {}
        if gps:
            try:
                def deg(v, ref):
                    d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
                    return -d if ref in ("S", "W") else d

                lat, lon = deg(gps[2], gps[1]), deg(gps[4], gps[3])
                lines.append(f"⚠ GPS location: {lat:.5f}, {lon:.5f}  (use 'Remove location/camera info' before sharing)")
            except Exception:
                lines.append("⚠ Contains GPS location data")
    except Exception as e:
        lines.append(f"(couldn't read image details: {e})")
    return "\n".join(lines)


def watermark(src: Path, text: str) -> list[Path]:
    """Repeating faint diagonal text across the image (protects photos you send out)."""
    from PIL import Image, ImageDraw, ImageFont

    text = text.strip()
    if not text:
        raise UserError("Watermark text can't be empty.")
    img = open_image(src).convert("RGBA")
    size = max(14, min(img.size) // 14)
    font = None
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default(size=size)
    diag = int((img.width ** 2 + img.height ** 2) ** 0.5)
    layer = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tw = d.textlength(text, font=font)
    step_x, step_y = int(tw + size * 2), size * 4
    for row, y in enumerate(range(0, diag, step_y)):
        for x in range(-step_x + (row % 2) * step_x // 2, diag, step_x):
            d.text((x, y), text, font=font, fill=(255, 255, 255, 70), stroke_width=max(1, size // 18),
                   stroke_fill=(0, 0, 0, 50))
    layer = layer.rotate(30, resample=Image.BICUBIC)
    left, top = (diag - img.width) // 2, (diag - img.height) // 2
    img.alpha_composite(layer.crop((left, top, left + img.width, top + img.height)))
    ext = out_ext(src)
    return [save_as(img, sibling(src, "watermarked", ext), ext)]


def icon_pack(src: Path) -> list[Path]:
    """Everything a website or app needs: favicon.ico + PNG icons in standard sizes."""
    from PIL import Image

    from .util import unique_dir

    img = open_image(src).convert("RGBA")
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    folder = unique_dir(src.with_name(f"{src.stem} (icons)"))
    outs = []
    for px, name in ((16, "favicon-16.png"), (32, "favicon-32.png"), (180, "apple-touch-icon.png"),
                     (192, "icon-192.png"), (512, "icon-512.png")):
        p = folder / name
        square.resize((px, px), Image.LANCZOS).save(p, "PNG", optimize=True)
        outs.append(p)
    ico = folder / "favicon.ico"
    square.save(ico, "ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    outs.append(ico)
    (folder / "how-to-use.html").write_text(
        '<link rel="icon" href="/favicon.ico" sizes="any">\n'
        '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">\n'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">\n', encoding="utf-8")
    return [folder]


def stitch(files: list[Path], vertical: bool = True, gap: int = 0) -> list[Path]:
    """Join images into one: stacked top-to-bottom (screenshots) or side by side."""
    from PIL import Image

    if len(files) < 2:
        raise UserError("Select at least two images to stitch.")
    imgs = [open_image(f).convert("RGBA") for f in files]
    if vertical:  # match widths
        w = max(i.width for i in imgs)
        imgs = [i if i.width == w else i.resize((w, round(i.height * w / i.width)), Image.LANCZOS) for i in imgs]
        canvas = Image.new("RGBA", (w, sum(i.height for i in imgs) + gap * (len(imgs) - 1)), (255, 255, 255, 255))
        y = 0
        for i in imgs:
            canvas.alpha_composite(i, (0, y))
            y += i.height + gap
    else:  # match heights
        h = max(i.height for i in imgs)
        imgs = [i if i.height == h else i.resize((round(i.width * h / i.height), h), Image.LANCZOS) for i in imgs]
        canvas = Image.new("RGBA", (sum(i.width for i in imgs) + gap * (len(imgs) - 1), h), (255, 255, 255, 255))
        x = 0
        for i in imgs:
            canvas.alpha_composite(i, (x, 0))
            x += i.width + gap
    if max(canvas.size) > 30000:
        raise UserError("The stitched image would be over 30,000 pixels long. Select fewer images.")
    photo = all(f.suffix.lower() in (".jpg", ".jpeg", ".heic", ".heif") for f in files)
    ext = ".jpg" if photo else ".png"
    out = sibling(files[0], "stitched", ext)
    return [save_as(canvas, out, ext)]


def _uniform_frames(files: list[Path], max_side: int):
    """Open images and fit them all onto one canvas size (letterboxed), for GIFs/slideshows."""
    from PIL import Image

    imgs = [_flatten(open_image(f)) for f in files]
    w = max(i.width for i in imgs)
    h = max(i.height for i in imgs)
    scale = min(1.0, max_side / max(w, h))
    W, H = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
    frames = []
    for im in imgs:
        im = im.copy()
        im.thumbnail((W, H), Image.LANCZOS)
        canvas = Image.new("RGB", (W, H), (0, 0, 0))
        canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
        frames.append(canvas)
    return frames


def make_gif(files: list[Path], seconds_per_frame: float = 0.8) -> list[Path]:
    if len(files) < 2:
        raise UserError("Select at least two images to make an animation.")
    frames = _uniform_frames(files, 800)
    out = sibling(files[0], "animation", ".gif")
    frames[0].save(out, "GIF", save_all=True, append_images=frames[1:], duration=int(seconds_per_frame * 1000),
                   loop=0, optimize=True)
    return [out]


def make_slideshow(files: list[Path], seconds_per_image: float = 3.0) -> list[Path]:
    """Photos -> 1080p MP4 slideshow with a short crossfade between pictures."""
    import tempfile

    from . import media

    if len(files) < 2:
        raise UserError("Select at least two images to make a slideshow.")
    frames = _uniform_frames(files, 1920)
    with tempfile.TemporaryDirectory(prefix="fw-slides-") as td:
        paths = []
        for i, fr in enumerate(frames):
            p = Path(td) / f"{i:04d}.jpg"
            fr.save(p, "JPEG", quality=92)
            paths.append(p)
        fade = 0.6
        args, parts = [], []
        for p in paths:
            args += ["-loop", "1", "-t", f"{seconds_per_image + fade:.2f}", "-i", str(p)]
        for i in range(len(paths)):
            parts.append(f"[{i}:v]setsar=1,format=yuv420p,fps=30[v{i}]")
        chain, last, offset = [], "v0", 0.0
        for i in range(1, len(paths)):
            offset += seconds_per_image
            chain.append(f"[{last}][v{i}]xfade=transition=fade:duration={fade}:offset={offset - fade * (i - 1):.2f}[x{i}]")
            last = f"x{i}"
        graph = ";".join(parts + chain)
        out = sibling(files[0], "slideshow", ".mp4")
        total = seconds_per_image * len(paths)
        media.run([*args, "-filter_complex", graph, "-map", f"[{last}]", "-c:v", "libx264", "-crf", "20",
                   "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)], total, "Making slideshow")
    return [out]


def white_to_transparent(src: Path, tolerance: int = 28) -> list[Path]:
    """Make a plain light background transparent by flood-filling from the edges (white inside
    the subject, like the inside of a letter "O", stays)."""
    from PIL import ImageDraw

    img = open_image(src).convert("RGBA")
    w, h = img.size
    marker = (255, 0, 255, 0)
    seeds = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]
    filled = False
    for x, y in seeds:
        r, g, b, a = img.getpixel((x, y))
        if a and min(r, g, b) >= 255 - tolerance * 2 and img.getpixel((x, y)) != marker:
            ImageDraw.floodfill(img, (x, y), marker, thresh=tolerance)
            filled = True
    if not filled:
        raise UserError(f"{src.name} doesn't have a plain light background at its edges.")
    data = [(0, 0, 0, 0) if p == marker else p for p in img.getdata()]
    img.putdata(data)
    out = sibling(src, "transparent", ".png")
    img.save(out, "PNG", optimize=True)
    return [out]


def font_sample(src: Path) -> list[Path]:
    """Preview a font file without installing it: alphabet, digits and sample sizes."""
    from PIL import Image, ImageDraw, ImageFont

    try:
        ImageFont.truetype(str(src), 24)
    except OSError as e:
        if "pixel size" in str(e):  # colour-emoji / bitmap fonts only come in fixed sizes
            raise UserError(f"{src.name} is a bitmap or colour-emoji font that only works at fixed sizes, "
                            "so a sample sheet can't be drawn.") from e
        raise UserError(f"Couldn't read {src.name} as a font ({e}).") from e
    W, pad = 1600, 40
    lines = [(28, src.stem), (40, "ABCDEFGHIJKLMNOPQRSTUVWXYZ"), (40, "abcdefghijklmnopqrstuvwxyz"),
             (40, "0123456789 !?&@#%()[]{}.,;:"), (72, "The quick brown fox"), (54, "jumps over the lazy dog."),
             (36, "Pack my box with five dozen liquor jugs."), (20, "Small text: sphinx of black quartz, judge my vow.")]
    H = pad * 2 + sum(int(size * 1.45) for size, _ in lines) + 40
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    y = pad
    for i, (size, text) in enumerate(lines):
        font = ImageFont.truetype(str(src), size)
        d.text((pad, y), text, font=font, fill=(110, 110, 110) if i == 0 else (20, 20, 20))
        y += int(size * 1.45)
        if i == 0:
            d.line((pad, y - 6, W - pad, y - 6), fill=(220, 220, 220), width=2)
    out = sibling(src, "sample", ".png")
    img.save(out, "PNG", optimize=True)
    return [out]
