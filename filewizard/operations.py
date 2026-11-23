"""Every action FileWizard offers, and how the right-click menu is laid out.

The catalog below is written in *menu order*: sections appear in the same order for every
file type (Convert to ▸ first, then combine/merge, compress, resize, ...). Items with a
`menu` path live in a submenu, e.g. menu=("Convert to",), label="PNG".

Each Operation knows which file extensions it appears on, how to ask the user for input
(once per batch, not once per file), and how to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import ui
from .util import UserError, natural_key

# ---------- file type families ----------
# (Kept here, import-light: the menu must build without loading PyMuPDF/Pillow.)

from .archives import ARCHIVE_EXTS  # noqa: E402
from .docs import DATA_EXTS, EBOOK_EXTS  # noqa: E402
from .images import IMAGE_EXTS, RAW_EXTS, VECTOR_EXTS  # noqa: E402
from .media import AUDIO_EXTS, VIDEO_EXTS  # noqa: E402
from .office import SHEET_EXTS, SLIDE_EXTS, WORD_EXTS  # noqa: E402

PDF_EXTS = {".pdf"}
OFFICE_EXTS = WORD_EXTS | SLIDE_EXTS | SHEET_EXTS
MARKDOWN_EXTS = {".md", ".markdown"}
HTML_EXTS = {".html", ".htm"}
DIR = "<folder>"  # pseudo-extension: right-click on a folder
ANY_FILE = "*"    # pseudo-extension: any file at all (registered for unknown types too)
DOC_LIKE = PDF_EXTS | OFFICE_EXTS | EBOOK_EXTS | MARKDOWN_EXTS | HTML_EXTS
EVERYTHING = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOC_LIKE | DATA_EXTS | ARCHIVE_EXTS

Params = dict[str, str]
Result = list[Path] | tuple[list[Path], str]


@dataclass(frozen=True)
class Operation:
    id: str
    label: str
    exts: frozenset[str]
    group: str                                   # top-level separator sections
    menu: tuple[str, ...] = ()                   # submenu path, () = directly in FileWizard ▸
    per_file: Callable[[Path, Params], Result] | None = None
    batch: Callable[[list[Path], Params], Result] | None = None
    ask: Callable[[list[Path]], Params | None] | None = None
    param_names: tuple[str, ...] = ()
    exclude_exts: frozenset[str] = field(default_factory=frozenset)
    labels: tuple[tuple[str, str], ...] = ()     # per-extension label overrides
    help: str = ""
    priority: int = 1                            # 1 = core; 2 = dropped first when space is short
    multi: bool = False                          # only meaningful with 2+ files selected

    def applies_to(self, ext: str) -> bool:
        ext = ext.lower()
        if ext in self.exclude_exts:
            return False
        return ext in self.exts or (ANY_FILE in self.exts and ext != DIR)

    def applies_to_path(self, p: Path) -> bool:
        return self.applies_to(DIR if p.is_dir() else p.suffix)

    def label_for(self, ext: str) -> str:
        return dict(self.labels).get(ext.lower(), self.label)

    @property
    def full_label(self) -> str:
        return " ▸ ".join((*self.menu, self.label))


# ---------- prompts ----------

def _ask_pages(verb: str):
    def ask(paths: list[Path]) -> Params | None:
        from .pdf import _open

        if len(paths) == 1:  # one PDF: show thumbnails to click
            try:
                v = ui.pick_pages(paths[0], verb)
                return None if v is None else {"pages": v}
            except (ImportError, RuntimeError):
                pass

        counts = []
        for p in paths[:3]:
            try:
                d = _open(p)
                counts.append(f"{p.name}: {d.page_count} pages")
                d.close()
            except UserError:
                pass
        hint = ("\n\n" + "\n".join(counts)) if counts else ""
        v = ui.ask_string(f"Pages to {verb}, e.g.  1-3, 5, 8-   (8- means 8 to the end){hint}")
        return None if v is None else {"pages": v}
    return ask


def _ask_text(key: str, prompt: str, initial: str = ""):
    def ask(paths):
        v = ui.ask_string(prompt, initial)
        return None if v is None else {key: v}
    return ask


def _ask_trim(paths):
    from .media import probe

    try:
        d = probe(paths[0]).duration
        length = f"\n\n{paths[0].name} is {int(d // 60)}:{d % 60:04.1f} long."
    except UserError:
        length = ""
    v = ui.ask_string("Keep from – to, as minutes:seconds.\n"
                      "Examples:  0:10-1:25   ·   1:00-  (to the end)   ·   -0:30  (first 30 s)" + length)
    return None if v is None else {"span": v}


def _ask_new_password(paths):
    pw = ui.ask_new_password()
    return None if pw is None else {"password": pw}


def _ask_unlock_password(paths):
    from .pdf import is_locked

    if not any(is_locked(p) for p in paths):
        return {}  # only "owner"/permissions protection: no password needed
    pw = ui.ask_string("Password to open the PDF:", secret=True)
    return None if pw is None else {"password": pw}


def _ask_order(title, add_types=None):
    def ask(paths):
        ordered = ui.order_files(sorted(paths, key=natural_key), title, add_types=add_types)
        return None if ordered is None else {"__order__": "\n".join(map(str, ordered))}
    return ask


def _ask_rename(paths):
    from .files import plan_rename

    while True:
        v = ui.ask_string("New name for the selected files. Tokens:\n"
                          "  {n} number   {name} old name   {date} modified   {taken} photo date   {ext}\n\n"
                          "e.g.  Vacation {n}  →  Vacation 01.jpg, Vacation 02.jpg …",
                          "{name}" if len(paths) == 1 else "")
        if v is None:
            return None
        try:
            plan = plan_rename(sorted(paths, key=natural_key), v)
        except UserError as e:
            ui.message(str(e), error=True)
            continue
        preview = "\n".join(f"{a.name}  →  {b.name}" for a, b in plan[:12])
        more = f"\n… and {len(plan) - 12} more" if len(plan) > 12 else ""
        if ui.confirm(f"Rename {len(plan)} file(s)?\n\n{preview}{more}"):
            return {"pattern": v}


def _ask_signature(paths):
    from tkinter import filedialog

    from .pdf import signature_png
    from .settings import load, save

    last = load().get("signature")
    sig = Path(last) if last and Path(last).exists() else None
    if sig is None or not ui.confirm(f"Use your saved signature?\n\n{sig.name}\n\n(Cancel to choose another image.)"):
        root = ui._root()
        picked = filedialog.askopenfilename(parent=root, title="Choose your signature image (PNG/JPG photo of it)",
                                            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.heic"), ("All", "*.*")])
        root.destroy()
        if not picked:
            return None
        sig = Path(picked)
        save({"signature": str(sig)})
    placed = ui.place_signature(paths[0], signature_png(sig))
    return None if placed is None else {**placed, "sig": str(sig)}


def _ordered(paths: list[Path], params: Params) -> list[Path]:
    if "__order__" in params:
        return [Path(p) for p in params["__order__"].split("\n")]
    return sorted(paths, key=natural_key)


def _kb_value(v: str) -> float:
    """'300 KB' / '2 MB' / '750' (KB) -> bytes."""
    t = v.strip().lower().replace(" ", "")
    try:
        if t.endswith("mb"):
            return float(t[:-2]) * 1024 * 1024
        return float(t.removesuffix("kb").removesuffix("k")) * 1024
    except ValueError as e:
        raise UserError(f"'{v}' isn't a size like 300 KB or 2 MB.") from e


def _mb(v: str) -> float:
    try:
        return float(v.lower().replace("mb", "").strip())
    except ValueError as e:
        raise UserError(f"'{v}' isn't a size in MB.") from e


# ---------- the catalog (in menu order) ----------

def _build() -> list[Operation]:
    from . import archives, docs, files, images, media, office, pdf

    fs = frozenset
    ops: list[Operation] = []

    def add(id, label, exts, group, **k):
        k.setdefault("menu", ())
        for key in ("exclude_exts",):
            if key in k:
                k[key] = fs(k[key])
        if "labels" in k:
            k["labels"] = tuple(k["labels"].items())
        ops.append(Operation(id, label, fs(exts), group, **k))

    CONVERT = ("Convert to",)
    IMG_IN = IMAGE_EXTS

    # ===== Convert to ▸ =====
    # documents
    add("office-to-pdf", "PDF", OFFICE_EXTS | {".txt"}, "convert", menu=CONVERT,
        per_file=lambda p, _: (docs.text_to_pdf(p) if p.suffix.lower() == ".txt" and not office.find_soffice()
                               and not office._com_available() else office.convert(p, ".pdf")))
    add("doc-to-pdf", "PDF", EBOOK_EXTS | MARKDOWN_EXTS | HTML_EXTS | {".log", ".json", ".xml", ".csv", ".tsv"},
        "convert", menu=CONVERT,
        per_file=lambda p, _: (docs.ebook_to_pdf(p) if p.suffix.lower() in EBOOK_EXTS
                               else docs.markup_to_pdf(p) if p.suffix.lower() in MARKDOWN_EXTS | HTML_EXTS
                               else docs.text_to_pdf(p)),
        exclude_exts={".csv"})
    add("img-to-pdf", "PDF", IMG_IN, "convert", menu=CONVERT,
        per_file=lambda p, _: pdf.images_to_pdf([p]))
    add("pdf-to-docx", "Word (.docx)", PDF_EXTS, "convert", menu=CONVERT,
        per_file=lambda p, _: pdf.to_docx(p))
    add("office-to-png", "PNG images", WORD_EXTS | SLIDE_EXTS | {".odp"}, "convert", menu=CONVERT,
        per_file=lambda p, _: office.to_images(p, ".png"), exclude_exts={".txt"})
    add("word-to-txt", "Text (.txt)", WORD_EXTS - {".txt"}, "convert", menu=CONVERT,
        per_file=lambda p, _: office.convert(p, ".txt"))
    add("word-to-docx", "Word (.docx)", {".doc", ".rtf", ".odt", ".docm", ".wps"}, "convert", menu=CONVERT,
        per_file=lambda p, _: office.convert(p, ".docx"))
    add("ppt-to-pptx", "PowerPoint (.pptx)", {".ppt", ".odp", ".pptm"}, "convert", menu=CONVERT,
        per_file=lambda p, _: office.convert(p, ".pptx"))
    add("data-to-xlsx", "Excel (.xlsx)", {".csv", ".tsv", ".json", ".xls", ".ods"}, "convert", menu=CONVERT,
        per_file=lambda p, _: (office.convert(p, ".xlsx") if p.suffix.lower() in (".xls", ".ods")
                               else docs.to_xlsx(p)))
    add("data-to-csv", "CSV", {".xlsx", ".xlsm", ".json", ".tsv"}, "convert", menu=CONVERT,
        per_file=lambda p, _: docs.to_csv(p))
    add("data-to-json", "JSON", {".csv", ".tsv", ".xlsx", ".xlsm"}, "convert", menu=CONVERT,
        per_file=lambda p, _: docs.to_json(p))
    add("md-to-html", "Web page (.html)", MARKDOWN_EXTS, "convert", menu=CONVERT,
        per_file=lambda p, _: docs.markdown_to_html_file(p))
    add("pdf-to-png", "PNG images", PDF_EXTS, "convert", menu=CONVERT,
        per_file=lambda p, _: pdf.to_images(p, ".png"))
    add("pdf-to-jpg", "JPG images", PDF_EXTS, "convert", menu=CONVERT,
        per_file=lambda p, _: pdf.to_images(p, ".jpg"))
    add("pdf-to-txt", "Text (.txt)", PDF_EXTS | EBOOK_EXTS, "convert", menu=CONVERT,
        per_file=lambda p, _: pdf.to_text(p) if p.suffix.lower() == ".pdf" else docs.ebook_to_text(p))

    # images
    for ext, name, same in ((".png", "PNG", {".png"}), (".jpg", "JPG", {".jpg", ".jpeg", ".jfif"}),
                            (".webp", "WebP", {".webp"}), (".heic", "HEIC (iPhone)", {".heic", ".heif"}),
                            (".avif", "AVIF", {".avif"}), (".gif", "GIF", {".gif"}),
                            (".tiff", "TIFF", {".tif", ".tiff"}), (".bmp", "BMP", {".bmp"}),
                            (".ico", "Icon (.ico)", {".ico"})):
        add(f"img-to-{ext[1:]}", name, IMG_IN, "convert", menu=CONVERT,
            per_file=lambda p, _, e=ext: images.convert(p, e), exclude_exts=same)
    add("gif-to-mp4", "MP4 video", {".gif"}, "convert", menu=CONVERT,
        per_file=lambda p, _: media.gif_to_mp4(p))

    # video
    for ext, name in ((".mp4", "MP4"), (".mkv", "MKV"), (".webm", "WebM"), (".mov", "MOV"),
                      (".avi", "AVI"), (".wmv", "WMV")):
        add(f"vid-to-{ext[1:]}", name, VIDEO_EXTS, "convert", menu=CONVERT,
            per_file=lambda p, _, e=ext: media.convert_video(p, e), exclude_exts={ext})
    add("vid-to-gif", "Animated GIF", VIDEO_EXTS, "convert", menu=CONVERT,
        per_file=lambda p, _: media.to_gif(p))

    # audio (also "extract the soundtrack" from videos)
    for ext, name, same in ((".mp3", "MP3", {".mp3"}), (".m4a", "M4A (AAC)", {".m4a", ".aac"}),
                            (".wav", "WAV", {".wav"}), (".flac", "FLAC (lossless)", {".flac"}),
                            (".ogg", "OGG", {".ogg", ".oga"}), (".opus", "Opus", {".opus"}),
                            (".aiff", "AIFF", {".aiff", ".aif"})):
        is_main = ext in (".mp3", ".m4a", ".wav")
        add(f"aud-to-{ext[1:]}", name, AUDIO_EXTS | (VIDEO_EXTS if is_main else set()), "convert",
            menu=CONVERT, per_file=lambda p, _, e=ext: media.convert_audio(p, e), exclude_exts=same,
            labels={v: f"{name.split(' (')[0]} (audio only)" for v in VIDEO_EXTS} if is_main else {})

    # ===== combine / merge (multi-select) =====
    add("combine-pdf", "Combine selected into one PDF…", DOC_LIKE | IMG_IN, "combine",
        batch=lambda ps, prm: pdf.combine_any(_ordered(ps, prm)),
        ask=_ask_order("Combine into one PDF", add_types=DOC_LIKE | IMG_IN),
        labels={".pdf": "Merge selected PDFs…"},
        help="Select several files (PDFs, images, Word, PowerPoint, …), right-click one, choose this.")
    add("media-join", "Join selected…", VIDEO_EXTS | AUDIO_EXTS, "combine",
        batch=lambda ps, prm: media.merge(_ordered(ps, prm)),
        ask=_ask_order("Join files", add_types=VIDEO_EXTS | AUDIO_EXTS))
    ST = ("Stitch into one image",)
    add("img-stitch-v", "Top to bottom", IMG_IN, "combine", menu=ST,
        batch=lambda ps, prm: images.stitch(_ordered(ps, prm), True), ask=_ask_order("Stitch images"))
    add("img-stitch-h", "Side by side", IMG_IN, "combine", menu=ST,
        batch=lambda ps, prm: images.stitch(_ordered(ps, prm), False), ask=_ask_order("Stitch images"))
    add("img-contact-sheet", "Make a contact sheet", IMG_IN, "combine",
        batch=lambda ps, _: images.contact_sheet(sorted(ps, key=natural_key)))

    # ===== compress =====
    PC = ("Compress",)
    add("pdf-compress", "Smaller file, same look", PDF_EXTS, "edit", menu=PC, per_file=lambda p, _: pdf.compress(p))
    for mb in (5, 2, 1):
        add(f"pdf-under-{mb}mb", f"Under {mb} MB (upload limits)" if mb == 2 else f"Under {mb} MB", PDF_EXTS, "edit",
            menu=PC, per_file=lambda p, _, m=mb: pdf.compress_to(p, m * 1024 * 1024))
    add("pdf-under-custom", "Under a size I choose…", PDF_EXTS, "edit", menu=PC,
        per_file=lambda p, prm: pdf.compress_to(p, int(_kb_value(prm["size"]))),
        ask=_ask_text("size", "Make the PDF smaller than (e.g. 2 MB or 800 KB):", "2 MB"), param_names=("size",))
    IC = ("Compress",)
    add("img-compress", "Smaller file, same look", IMG_IN, "edit", menu=IC, per_file=lambda p, _: images.compress(p))
    for kb, why in ((1024, "1 MB"), (500, "500 KB"), (200, "200 KB (upload forms)")):
        add(f"img-under-{kb}kb", f"Under {why}", IMG_IN, "edit", menu=IC,
            per_file=lambda p, _, k=kb: images.compress_to(p, k * 1024))
    add("img-under-custom", "Under a size I choose…", IMG_IN, "edit", menu=IC,
        per_file=lambda p, prm: images.compress_to(p, int(_kb_value(prm["size"]))),
        ask=_ask_text("size", "Make the image smaller than (e.g. 300 KB or 2 MB):", "500 KB"), param_names=("size",))
    add("office-compress", "Compress (shrink pictures)", {".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm"},
        "edit", per_file=lambda p, _: office.compress_ooxml(p))
    add("aud-compress", "Compress", AUDIO_EXTS, "edit", per_file=lambda p, _: media.compress_audio(p))
    VC = ("Compress",)
    add("vid-compress", "Smaller file, same look", VIDEO_EXTS, "edit", menu=VC,
        per_file=lambda p, _: media.compress(p))
    for mb, why in ((10, "Discord / WhatsApp"), (25, "email"), (100, "uploads")):
        add(f"vid-under-{mb}", f"Under {mb} MB ({why})", VIDEO_EXTS, "edit", menu=VC,
            per_file=lambda p, _, m=mb: media.compress(p, m))
    add("vid-under-custom", "Under a size I choose…", VIDEO_EXTS, "edit", menu=VC,
        per_file=lambda p, prm: media.compress(p, _mb(prm["mb"])),
        ask=_ask_text("mb", "Make the video smaller than how many MB?", "50"), param_names=("mb",))

    # ===== resize =====
    RZ = ("Resize",)
    for spec, name in (("25%", "25%"), ("50%", "50%"), ("75%", "75%"), ("3840", "4K (3840 px)"),
                       ("1920", "Full HD (1920 px)"), ("1280", "1280 px"), ("800", "800 px (web/email)")):
        add(f"img-resize-{spec.rstrip('%')}{'pct' if '%' in spec else 'px'}", name, IMG_IN, "edit", menu=RZ,
            per_file=lambda p, _, s=spec: images.resize(p, s))
    add("img-resize", "Custom…", IMG_IN, "edit", menu=RZ,
        per_file=lambda p, prm: images.resize(p, prm["size"]),
        ask=_ask_text("size", "New size: a percentage (50%), longest side in pixels (1920), or a box (800x600):",
                      "50%"), param_names=("size",))
    for h in (1080, 720, 480):
        add(f"vid-{h}p", f"{h}p", VIDEO_EXTS, "edit", menu=RZ, per_file=lambda p, _, h=h: media.resize(p, h))

    # ===== trim / pages =====
    add("media-trim", "Trim…", VIDEO_EXTS | AUDIO_EXTS, "edit",
        per_file=lambda p, prm: media.trim(p, prm["span"]), ask=_ask_trim, param_names=("span",))
    PG = ("Pages",)
    add("pdf-split", "Split into single pages", PDF_EXTS, "edit", menu=PG, per_file=lambda p, _: pdf.split(p))
    add("pdf-extract", "Extract pages…", PDF_EXTS, "edit", menu=PG,
        per_file=lambda p, prm: pdf.extract_pages(p, prm["pages"]), ask=_ask_pages("keep"), param_names=("pages",))
    add("pdf-delete", "Delete pages…", PDF_EXTS, "edit", menu=PG,
        per_file=lambda p, prm: pdf.delete_pages(p, prm["pages"]), ask=_ask_pages("delete"),
        param_names=("pages",))
    add("pdf-reverse", "Reverse order", PDF_EXTS, "edit", menu=PG, per_file=lambda p, _: pdf.reverse(p))
    add("pdf-remove-blank", "Remove blank pages", PDF_EXTS, "edit", menu=PG,
        per_file=lambda p, _: pdf.remove_blank_pages(p))

    # ===== rotate & flip =====
    RO = ("Rotate",)
    add("pdf-rotate-right", "Right (clockwise)", PDF_EXTS, "edit", menu=RO, per_file=lambda p, _: pdf.rotate(p, 90))
    add("pdf-rotate-left", "Left", PDF_EXTS, "edit", menu=RO, per_file=lambda p, _: pdf.rotate(p, 270))
    add("pdf-rotate-some", "Some pages (clockwise)…", PDF_EXTS, "edit", menu=RO,
        per_file=lambda p, prm: pdf.rotate_pages(p, prm["pages"]), ask=_ask_pages("rotate"), param_names=("pages",))
    add("pdf-rotate-180", "Upside down (180°)", PDF_EXTS, "edit", menu=RO, per_file=lambda p, _: pdf.rotate(p, 180))
    for how, name in (("right", "Right (clockwise)"), ("left", "Left"), ("180", "Upside down (180°)"),
                      ("hflip", "Mirror (flip horizontally)"), ("vflip", "Flip vertically")):
        add(f"img-rotate-{how}", name, IMG_IN, "edit", menu=RO, per_file=lambda p, _, h=how: images.transform(p, h))
        add(f"vid-rotate-{how}", name, VIDEO_EXTS, "edit", menu=RO, per_file=lambda p, _, h=how: media.rotate(p, h))

    # ===== speed / sound =====
    SP = ("Speed",)
    for f, name in ((0.5, "0.5× (slow motion)"), (1.25, "1.25×"), (1.5, "1.5×"), (2.0, "2×")):
        add(f"media-speed-{str(f).replace('.', '_')}", name, VIDEO_EXTS | AUDIO_EXTS, "edit", menu=SP,
            per_file=lambda p, _, f=f: media.speed(p, f))
    add("media-ringtone", "Make iPhone ringtone (.m4r)", VIDEO_EXTS | AUDIO_EXTS, "edit",
        per_file=lambda p, _: media.ringtone(p))
    add("media-normalize", "Fix volume (normalize loudness)", VIDEO_EXTS | AUDIO_EXTS, "edit",
        per_file=lambda p, _: media.normalize(p))
    add("vid-subs", "Burn in subtitles (.srt)", VIDEO_EXTS, "edit", per_file=lambda p, _: media.burn_subtitles(p),
        help="Needs a .srt file with the same name next to the video")
    add("vid-mute", "Remove sound", VIDEO_EXTS, "edit", per_file=lambda p, _: media.mute(p))

    # ===== edit / add =====
    ED = ("Edit",)
    add("img-gray", "Black & white", IMG_IN, "edit", menu=ED, per_file=lambda p, _: images.transform(p, "gray"))
    add("img-trim", "Crop away borders", IMG_IN, "edit", menu=ED, per_file=lambda p, _: images.transform(p, "trim"))
    add("img-square", "Pad to square", IMG_IN, "edit", menu=ED, per_file=lambda p, _: images.transform(p, "square"))
    add("img-nobg", "Remove background (AI)", IMG_IN - RAW_EXTS - VECTOR_EXTS, "edit", menu=ED,
        per_file=lambda p, _: images.remove_background(p))
    add("img-watermark", "Add watermark…", IMG_IN - VECTOR_EXTS, "edit", menu=ED,
        per_file=lambda p, prm: images.watermark(p, prm["text"]),
        ask=_ask_text("text", "Watermark text:", "© My Name"), param_names=("text",))
    add("img-icons", "Make website/app icons", IMG_IN - RAW_EXTS, "edit", menu=ED,
        per_file=lambda p, _: images.icon_pack(p))
    add("pdf-number", "Add page numbers", PDF_EXTS, "edit", menu=ED, per_file=lambda p, _: pdf.add_page_numbers(p))
    add("pdf-watermark", "Add watermark…", PDF_EXTS, "edit", menu=ED,
        per_file=lambda p, prm: pdf.watermark(p, prm["text"]),
        ask=_ask_text("text", "Watermark text:", "CONFIDENTIAL"), param_names=("text",))
    add("pdf-sign", "Sign…", PDF_EXTS, "edit", menu=ED,
        per_file=lambda p, prm: pdf.sign(p, Path(prm["sig"]), int(prm.get("page", "0")), float(prm["x"]),
                                         float(prm["y"]), float(prm.get("width", "150"))),
        ask=_ask_signature, param_names=("sig", "x", "y"),
        help="Place an image of your signature on a page")
    add("pdf-gray", "Black & white", PDF_EXTS, "edit", menu=ED, per_file=lambda p, _: pdf.grayscale(p))
    add("pdf-flatten", "Flatten forms & comments", PDF_EXTS, "edit", menu=ED, per_file=lambda p, _: pdf.flatten(p))

    # ===== security =====
    SEC = ("Password",)
    add("pdf-protect", "Add password…", PDF_EXTS, "edit", menu=SEC,
        per_file=lambda p, prm: pdf.protect(p, prm["password"]), ask=_ask_new_password, param_names=("password",))
    add("pdf-unlock", "Remove password…", PDF_EXTS, "edit", menu=SEC,
        per_file=lambda p, prm: pdf.unlock(p, prm.get("password")), ask=_ask_unlock_password,
        param_names=("password",))

    # ===== extract =====
    EX = ("Extract",)
    add("pdf-ocr", "Make searchable (OCR)", PDF_EXTS, "extract", per_file=lambda p, _: pdf.ocr(p))
    add("img-ocr", "Copy text out of image (OCR)", IMG_IN - VECTOR_EXTS, "extract",
        per_file=lambda p, _: pdf.image_to_text(p))
    add("copy-text", "Copy all text", PDF_EXTS | EBOOK_EXTS, "extract", batch=lambda ps, _: pdf.all_text(ps))
    add("pdf-extract-images", "Images", PDF_EXTS, "extract", menu=EX, per_file=lambda p, _: pdf.extract_images(p))
    add("vid-snapshot", "A still frame (JPG)", VIDEO_EXTS, "extract", menu=EX, per_file=lambda p, _: media.snapshot(p))
    add("vid-extract-subs", "Subtitles (.srt)", VIDEO_EXTS, "extract", menu=EX,
        per_file=lambda p, _: media.extract_subtitles(p))
    add("vid-frames", "Frames (one per second)", VIDEO_EXTS, "extract", menu=EX,
        per_file=lambda p, _: media.extract_frames(p))
    add("img-strip", "Remove location/camera info", IMG_IN - VECTOR_EXTS, "extract",
        per_file=lambda p, _: images.strip_metadata(p))

    # ===== archives =====
    add("arc-extract", "Extract here", ARCHIVE_EXTS, "archive", per_file=lambda p, _: archives.extract(p, here=True))
    add("arc-extract-folder", "Extract to its own folder", ARCHIVE_EXTS, "archive",
        per_file=lambda p, _: archives.extract(p))

    # ===== More ▸ (any file, folders too) =====
    MORE = ("More",)
    ANY = {ANY_FILE, DIR}
    add("zip", "Compress to ZIP", ANY, "more", menu=MORE, batch=lambda ps, _: archives.make_zip(ps))
    add("7z", "Compress to 7z (smaller)", ANY, "more", menu=MORE, batch=lambda ps, _: archives.make_7z(ps))
    add("rename", "Rename selected…", {ANY_FILE}, "more", menu=MORE,
        batch=lambda ps, prm: files.rename(sorted(ps, key=natural_key), prm["pattern"]), ask=_ask_rename,
        param_names=("pattern",))
    add("details", "Show details", {ANY_FILE}, "more", menu=MORE, batch=lambda ps, _: files.details(ps))
    add("checksum", "Checksum (SHA-256)", {ANY_FILE}, "more", menu=MORE, batch=lambda ps, _: files.checksums(ps))
    add("palette", "All actions…", {ANY_FILE, DIR}, "palette",
        batch=lambda ps, _: ([], ""), help="Search every FileWizard action for the selection")
    add("folder-combine-pdf", "Combine PDFs/images inside into one PDF", {DIR}, "more", menu=MORE,
        per_file=lambda p, _: pdf.combine_any(_folder_docs(p)))
    return ops


def _folder_docs(folder: Path) -> list[Path]:
    from .images import IMAGE_EXTS as IM

    items = sorted((f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in DOC_LIKE | IM),
                   key=natural_key)
    if not items:
        raise UserError(f"No PDFs, images or documents directly inside '{folder.name}'.")
    pdfs_out = [f for f in items if "(combined)" not in f.stem]
    return pdfs_out


# Shown only in the full (native) menu and the "All actions…" palette when the classic
# registry menu has to fit Windows' 16-entry limit.
SECONDARY = {
    "img-to-tiff", "img-to-bmp", "img-to-ico", "img-to-gif", "img-to-avif", "img-contact-sheet",
    "img-resize-25pct", "img-resize-75pct", "img-resize-3840px", "img-resize-1280px", "img-resize-800px",
    "img-rotate-180", "img-rotate-hflip", "img-rotate-vflip", "img-gray", "img-trim", "img-square", "img-nobg",
    "img-strip", "img-ocr", "pdf-reverse", "pdf-remove-blank", "pdf-rotate-180", "pdf-gray", "pdf-flatten",
    "pdf-extract-images", "pdf-watermark", "pdf-to-txt", "vid-to-wmv", "vid-to-avi", "vid-to-mkv", "vid-frames",
    "vid-rotate-180", "vid-rotate-hflip", "vid-rotate-vflip", "media-speed-1_25", "media-speed-1_5",
    "media-speed-0_5", "media-speed-2_0", "vid-under-100", "vid-480p", "aud-to-flac", "aud-to-ogg",
    "aud-to-opus", "aud-to-aiff", "aud-to-wav", "7z", "checksum", "rename", "vid-snapshot", "media-normalize",
    "img-under-1024kb", "img-under-custom", "details", "img-watermark", "img-icons", "vid-subs",
    "vid-extract-subs", "copy-text", "pdf-rotate-some", "img-stitch-v", "img-stitch-h", "media-ringtone", "pdf-under-5mb", "pdf-under-1mb", "pdf-under-custom",
    "vid-1080p", "vid-720p", "vid-under-custom", "vid-to-webm", "img-resize-3840px", "pdf-to-jpg",
}
MULTI = {"combine-pdf", "media-join", "img-contact-sheet", "img-stitch-v", "img-stitch-h"}


def _finalize(ops: list[Operation]) -> list[Operation]:
    from dataclasses import replace

    return [replace(op, priority=2 if op.id in SECONDARY else 1, multi=op.id in MULTI) for op in ops]


OPERATIONS: list[Operation] = _finalize(_build())
BY_ID: dict[str, Operation] = {op.id: op for op in OPERATIONS}
ALL_EXTS: list[str] = sorted({e for op in OPERATIONS for e in op.exts if e not in (DIR, ANY_FILE)})

_ids = [op.id for op in OPERATIONS]
assert len(_ids) == len(set(_ids)), "duplicate operation id"


def for_ext(ext: str) -> list[Operation]:
    return [op for op in OPERATIONS if op.applies_to(ext)]


def for_selection(files: list[Path]) -> list[Operation]:
    """Actions that make sense for this selection: they apply to every selected file
    (batch actions like Combine/Join/ZIP need only apply to each file too)."""
    return [op for op in OPERATIONS if op.id != "palette" and all(op.applies_to_path(f) for f in files)]


def execute(op: Operation, files: list[Path], params: Params) -> tuple[list[Path], list[str], list[str]]:
    """Run op over files. Returns (outputs, errors, messages); one bad file doesn't stop the rest."""
    from .util import PROGRESS, Cancelled

    files = [f for f in files if op.applies_to_path(f)]
    if not files:
        raise UserError(f"'{op.label}' doesn't apply to the selected file type.")
    outputs: list[Path] = []
    errors: list[str] = []
    messages: list[str] = []

    def take(res: Result):
        if isinstance(res, tuple):
            outs, msg = res
            outputs.extend(outs)
            if msg:
                messages.append(msg)
        else:
            outputs.extend(res)

    if op.batch:
        take(op.batch(files, params))
        return outputs, errors, messages
    n = len(files)
    for i, f in enumerate(files):
        PROGRESS.base, PROGRESS.span = i / n, 1 / n
        PROGRESS.report(0.0, f"{op.label.rstrip('…')}: {f.name}" + (f"  ({i + 1} of {n})" if n > 1 else ""))
        try:
            take(op.per_file(f, params))
        except Cancelled:
            raise
        except UserError as e:
            errors.append(str(e))
        except Exception as e:  # keep going; report at the end
            errors.append(f"{f.name}: {type(e).__name__}: {e}")
    PROGRESS.base, PROGRESS.span = 0.0, 1.0
    return outputs, errors, messages
