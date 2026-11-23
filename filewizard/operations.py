"""Every action FileWizard offers, in menu order.

Each Operation knows which file extensions it appears on, how to ask the user for any
input (once per batch, not once per file), and how to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import ui
from .office import SHEET_EXTS, SLIDE_EXTS, WORD_EXTS
from .util import UserError, natural_key

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".heic", ".heif", ".ico"}
PDF_EXTS = {".pdf"}
OFFICE_EXTS = WORD_EXTS | SLIDE_EXTS | SHEET_EXTS

Params = dict[str, str]


@dataclass(frozen=True)
class Operation:
    id: str
    label: str
    exts: frozenset[str]
    group: str
    # per_file(path, params) -> outputs ; or batch(paths, params) -> outputs
    per_file: Callable[[Path, Params], list[Path]] | None = None
    batch: Callable[[list[Path], Params], list[Path]] | None = None
    # ask(paths) -> params, or None if the user cancelled
    ask: Callable[[list[Path]], Params | None] | None = None
    param_names: tuple[str, ...] = ()
    exclude_exts: frozenset[str] = field(default_factory=frozenset)

    def applies_to(self, ext: str) -> bool:
        ext = ext.lower()
        return ext in self.exts and ext not in self.exclude_exts


# ---------- prompts ----------

def _ask_pages(verb: str):
    def ask(paths: list[Path]) -> Params | None:
        from .pdf import _open

        counts = []
        for p in paths[:3]:
            try:
                d = _open(p)
                counts.append(f"{p.name}: {d.page_count} pages")
                d.close()
            except UserError:
                pass
        hint = ("\n" + "\n".join(counts)) if counts else ""
        v = ui.ask_string(f"Pages to {verb} (e.g. 1-3, 5, 8-){hint}")
        return None if v is None else {"pages": v}
    return ask


def _ask_resize(paths):
    v = ui.ask_string("New size: a percentage (50%), longest side in pixels (1920), or a box (800x600):", "50%")
    return None if v is None else {"size": v}


def _ask_new_password(paths):
    pw = ui.ask_new_password()
    return None if pw is None else {"password": pw}


def _ask_unlock_password(paths):
    from .pdf import is_locked

    if not any(is_locked(p) for p in paths):
        return {}  # only "owner"/permissions protection: no password needed
    pw = ui.ask_string("Password to open the PDF:", secret=True)
    return None if pw is None else {"password": pw}


def _ask_order(title):
    def ask(paths):
        ordered = ui.order_files(sorted(paths, key=natural_key), title)
        return None if ordered is None else {"__order__": "\n".join(map(str, ordered))}
    return ask


def _ordered(paths: list[Path], params: Params) -> list[Path]:
    if "__order__" in params:
        return [Path(p) for p in params["__order__"].split("\n")]
    return sorted(paths, key=natural_key)


# ---------- the list ----------

def _build() -> list[Operation]:
    from . import images, office, pdf

    fs = frozenset
    ops: list[Operation] = []

    def add(*a, **k):
        ops.append(Operation(*a, **k))

    # Convert: documents
    add("office-to-pdf", "Convert to PDF", fs(OFFICE_EXTS), "Convert",
        per_file=lambda p, _: office.convert(p, ".pdf"))
    add("word-to-docx", "Convert to Word (.docx)", fs({".doc", ".rtf", ".odt", ".docm"}), "Convert",
        per_file=lambda p, _: office.convert(p, ".docx"))
    add("pdf-to-docx", "Convert to Word (.docx)", fs(PDF_EXTS), "Convert",
        per_file=lambda p, _: pdf.to_docx(p))
    add("pdf-to-png", "Convert to PNG (each page)", fs(PDF_EXTS), "Convert",
        per_file=lambda p, _: pdf.to_images(p, ".png"))
    add("pdf-to-jpg", "Convert to JPG (each page)", fs(PDF_EXTS), "Convert",
        per_file=lambda p, _: pdf.to_images(p, ".jpg"))
    add("pdf-to-txt", "Convert to text (.txt)", fs(PDF_EXTS), "Convert",
        per_file=lambda p, _: pdf.to_text(p))

    # Convert: images
    add("img-to-pdf", "Convert to PDF", fs(IMAGE_EXTS), "Convert",
        per_file=lambda p, _: pdf.images_to_pdf([p]))
    for ext, name, excl in ((".png", "PNG", {".png"}), (".jpg", "JPG", {".jpg", ".jpeg"}),
                            (".webp", "WebP", {".webp"}), (".ico", "icon (.ico)", {".ico"}),
                            (".bmp", "BMP", {".bmp"}), (".tiff", "TIFF", {".tif", ".tiff"})):
        add(f"img-to-{ext[1:]}", f"Convert to {name}", fs(IMAGE_EXTS), "Convert",
            per_file=lambda p, _, e=ext: images.convert(p, e), exclude_exts=fs(excl))

    # PDF tools
    add("pdf-merge", "Merge selected PDFs…", fs(PDF_EXTS), "PDF tools",
        batch=lambda ps, prm: pdf.merge(_ordered(ps, prm)), ask=_ask_order("Merge PDFs"))
    add("img-combine-pdf", "Combine selected into one PDF…", fs(IMAGE_EXTS), "PDF tools",
        batch=lambda ps, prm: pdf.images_to_pdf(_ordered(ps, prm)), ask=_ask_order("Combine into PDF"))
    add("pdf-split", "Split into single pages", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, _: pdf.split(p))
    add("pdf-extract", "Extract pages…", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, prm: pdf.extract_pages(p, prm["pages"]), ask=_ask_pages("keep"),
        param_names=("pages",))
    add("pdf-delete", "Delete pages…", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, prm: pdf.delete_pages(p, prm["pages"]), ask=_ask_pages("delete"),
        param_names=("pages",))
    add("pdf-rotate-right", "Rotate right (all pages)", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, _: pdf.rotate(p, 90))
    add("pdf-rotate-left", "Rotate left (all pages)", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, _: pdf.rotate(p, 270))
    add("pdf-reverse", "Reverse page order", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, _: pdf.reverse(p))
    add("pdf-number", "Add page numbers", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, _: pdf.add_page_numbers(p))
    add("pdf-compress", "Compress", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, _: pdf.compress(p))
    add("pdf-extract-images", "Extract images", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, _: pdf.extract_images(p))
    add("pdf-protect", "Add password…", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, prm: pdf.protect(p, prm["password"]), ask=_ask_new_password,
        param_names=("password",))
    add("pdf-unlock", "Remove password…", fs(PDF_EXTS), "PDF tools",
        per_file=lambda p, prm: pdf.unlock(p, prm.get("password")), ask=_ask_unlock_password,
        param_names=("password",))

    # Image tools
    add("img-resize", "Resize…", fs(IMAGE_EXTS), "Image tools",
        per_file=lambda p, prm: images.resize(p, prm["size"]), ask=_ask_resize, param_names=("size",))
    add("img-compress", "Compress", fs(IMAGE_EXTS), "Image tools",
        per_file=lambda p, _: images.compress(p))
    add("img-rotate-right", "Rotate right", fs(IMAGE_EXTS), "Image tools",
        per_file=lambda p, _: images.rotate(p, 90))
    add("img-rotate-left", "Rotate left", fs(IMAGE_EXTS), "Image tools",
        per_file=lambda p, _: images.rotate(p, 270))
    add("img-strip", "Remove location/camera info", fs(IMAGE_EXTS), "Image tools",
        per_file=lambda p, _: images.strip_metadata(p))
    return ops


OPERATIONS: list[Operation] = _build()
BY_ID: dict[str, Operation] = {op.id: op for op in OPERATIONS}
ALL_EXTS: list[str] = sorted({e for op in OPERATIONS for e in op.exts})


def for_ext(ext: str) -> list[Operation]:
    return [op for op in OPERATIONS if op.applies_to(ext)]


def execute(op: Operation, files: list[Path], params: Params) -> tuple[list[Path], list[str]]:
    """Run op over files. Returns (outputs, error messages); one bad file doesn't stop the rest."""
    files = [f for f in files if op.applies_to(f.suffix)]
    if not files:
        raise UserError(f"'{op.label}' doesn't apply to the selected file type.")
    outputs: list[Path] = []
    errors: list[str] = []
    if op.batch:
        outputs.extend(op.batch(files, params))
        return outputs, errors
    for f in files:
        try:
            outputs.extend(op.per_file(f, params))
        except UserError as e:
            errors.append(str(e))
        except Exception as e:  # keep going; report at the end
            errors.append(f"{f.name}: {type(e).__name__}: {e}")
    return outputs, errors
