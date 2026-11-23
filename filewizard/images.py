"""Image conversions and tools (Pillow)."""

from __future__ import annotations

from pathlib import Path

from .util import UserError, sibling

_heif_registered = False


def _open(path: Path):
    global _heif_registered
    from PIL import Image, ImageOps

    if path.suffix.lower() in (".heic", ".heif") and not _heif_registered:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
            _heif_registered = True
        except ImportError as e:
            raise UserError("HEIC support needs the 'pillow-heif' package (pip install pillow-heif).") from e
    img = Image.open(path)
    img.load()
    # Phone photos store rotation in EXIF; apply it so the output isn't sideways.
    return ImageOps.exif_transpose(img) or img


def _flatten(img, background=(255, 255, 255)):
    """Formats without transparency (JPG, BMP) need a solid background."""
    from PIL import Image

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


FORMATS = {
    # ext: (Pillow format, needs flatten, save kwargs)
    ".png": ("PNG", False, {"optimize": True}),
    ".jpg": ("JPEG", True, {"quality": 92, "optimize": True}),
    ".webp": ("WEBP", False, {"quality": 90}),
    ".bmp": ("BMP", True, {}),
    ".tiff": ("TIFF", False, {"compression": "tiff_lzw"}),
    ".gif": ("GIF", False, {}),
    ".ico": ("ICO", False, {"sizes": [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]}),
}


def save_as(img, out: Path, ext: str, **overrides):
    fmt, flatten, kwargs = FORMATS[ext]
    kwargs = {**kwargs, **overrides}
    if flatten:
        img = _flatten(img)
    elif fmt in ("PNG", "WEBP", "TIFF", "ICO") and img.mode not in ("RGB", "RGBA", "L", "LA"):
        img = img.convert("RGBA")
    if fmt == "ICO":
        side = max(img.size)  # icons are square; pad instead of stretching
        from PIL import Image

        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
        img = square
        kwargs["sizes"] = [s for s in kwargs["sizes"] if s[0] <= side] or [(side, side)]
    img.save(out, fmt, **kwargs)
    return out


def convert(src: Path, ext: str) -> list[Path]:
    img = _open(src)
    out = sibling(src, None, ext)
    return [save_as(img, out, ext)]


def resize(src: Path, spec: str) -> list[Path]:
    """spec: '50%' or '1920' (longest side in px) or '800x600' (fit inside box)."""
    from PIL import Image

    img = _open(src)
    w, h = img.size
    s = spec.strip().lower().replace(" ", "")
    try:
        if s.endswith("%"):
            f = float(s[:-1]) / 100
            new = (round(w * f), round(h * f))
        elif "x" in s:
            bw, bh = (int(v) for v in s.split("x", 1))
            f = min(bw / w, bh / h)
            new = (round(w * f), round(h * f))
        else:
            f = int(s) / max(w, h)
            new = (round(w * f), round(h * f))
    except (ValueError, ZeroDivisionError) as e:
        raise UserError(f"Couldn't understand size '{spec}'. Try 50%, 1920, or 800x600.") from e
    if min(new) < 1:
        raise UserError("That would make the image smaller than 1 pixel.")
    ext = _out_ext(src)
    out = sibling(src, f"{new[0]}x{new[1]}", ext)
    return [save_as(img.resize(new, Image.LANCZOS), out, ext)]


def compress(src: Path) -> list[Path]:
    """Re-encode as a reasonably-sized JPG (or optimized PNG if it has transparency)."""
    img = _open(src)
    has_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
    if has_alpha:
        out = sibling(src, "compressed", ".png")
        save_as(img.quantize(256) if img.mode != "P" else img, out, ".png")
    else:
        out = sibling(src, "compressed", ".jpg")
        save_as(img, out, ".jpg", quality=72)
    if out.stat().st_size >= src.stat().st_size:
        out.unlink()
        raise UserError(f"{src.name} is already well compressed; the result wasn't smaller, so nothing was saved.")
    return [out]


def strip_metadata(src: Path) -> list[Path]:
    """Remove EXIF/GPS etc. by copying only the pixels."""
    from PIL import Image

    img = _open(src)
    clean = Image.frombytes(img.mode, img.size, img.tobytes())
    if img.mode == "P" and img.getpalette():
        clean.putpalette(img.getpalette())
    ext = _out_ext(src)
    return [save_as(clean, sibling(src, "no metadata", ext), ext)]


def rotate(src: Path, degrees_clockwise: int) -> list[Path]:
    img = _open(src)
    ext = _out_ext(src)
    label = {90: "rotated right", 270: "rotated left", 180: "rotated 180"}[degrees_clockwise]
    return [save_as(img.rotate(-degrees_clockwise, expand=True), sibling(src, label, ext), ext)]


def _out_ext(src: Path) -> str:
    """Keep the source format where we can write it; HEIC etc. fall back to JPG."""
    e = src.suffix.lower()
    e = {".jpeg": ".jpg", ".tif": ".tiff"}.get(e, e)
    return e if e in FORMATS else ".jpg"


def to_pdf_bytes(src: Path, max_w_pt: float = 612, max_h_pt: float = 792) -> tuple[bytes, float, float]:
    """Encode an image for embedding in a PDF and pick a page size.

    The page fits the image inside US Letter (rotated to match the image's orientation),
    so phone photos print sensibly instead of becoming 50-inch pages.
    """
    import io

    img = _open(src)
    w, h = img.size
    if w > h:
        max_w_pt, max_h_pt = max(max_w_pt, max_h_pt), min(max_w_pt, max_h_pt)
    scale = min(max_w_pt / w, max_h_pt / h)
    buf = io.BytesIO()
    if src.suffix.lower() in (".jpg", ".jpeg", ".heic", ".heif"):
        _flatten(img).save(buf, "JPEG", quality=92)  # photos: stay lossy, stay small
    else:
        img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(buf, "PNG")
    return buf.getvalue(), w * scale, h * scale
