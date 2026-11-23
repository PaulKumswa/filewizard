"""Documents that aren't Office or PDF: ebooks, Markdown/HTML/text, and tabular data."""

from __future__ import annotations

import csv
import html as html_lib
import io
import json
from pathlib import Path

from .util import UserError, report, sibling, unique_dir

EBOOK_EXTS = {".epub", ".mobi", ".fb2", ".xps", ".oxps", ".cbz"}
MARKUP_EXTS = {".md", ".markdown", ".html", ".htm"}
TEXT_EXTS = {".txt", ".log", ".json", ".xml", ".py", ".js", ".ts", ".css", ".java", ".c", ".cpp", ".cs",
             ".ini", ".yaml", ".yml", ".csv"}
DATA_EXTS = {".csv", ".tsv", ".xlsx", ".xlsm", ".json"}

_CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.4; color: #111; }
h1 { font-size: 20pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; }
pre, code { font-family: monospace; font-size: 9.5pt; background: #f3f3f3; }
pre { padding: 6pt; }
table { border-collapse: collapse; } td, th { border: 1px solid #999; padding: 3pt 6pt; }
blockquote { color: #444; margin-left: 12pt; border-left: 3px solid #ccc; padding-left: 8pt; }
img { max-width: 100%; }
"""


def read_text(path: Path) -> str:
    """Read text whatever its encoding (UTF-8 with/without BOM, UTF-16, or Windows-1252)."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------- HTML/Markdown/text -> PDF ----------

def html_to_pdf_file(html: str, out: Path, base_dir: Path) -> Path:
    import pymupdf

    archive = pymupdf.Archive(str(base_dir))
    story = pymupdf.Story(html=html, user_css=_CSS, archive=archive)
    writer = pymupdf.DocumentWriter(str(out))
    mediabox = pymupdf.paper_rect("letter")
    where = mediabox + (54, 54, -54, -54)  # 0.75" margins
    more, pages = True, 0
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
        pages += 1
        report(None, f"Laying out page {pages}")
        if pages > 5000:
            break
    writer.close()
    return out


def markdown_to_html(text: str, title: str) -> str:
    try:
        import markdown
    except ImportError as e:
        raise UserError("Markdown support needs the 'markdown' package (pip install markdown).") from e
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc"])
    return f"<html><head><meta charset='utf-8'><title>{html_lib.escape(title)}</title></head><body>{body}</body></html>"


def markup_to_pdf(src: Path) -> list[Path]:
    text = read_text(src)
    html = markdown_to_html(text, src.stem) if src.suffix.lower() in (".md", ".markdown") else text
    return [html_to_pdf_file(html, sibling(src, None, ".pdf"), src.parent)]


def markdown_to_html_file(src: Path) -> list[Path]:
    html = markdown_to_html(read_text(src), src.stem)
    styled = html.replace("</head>", f"<style>{_CSS} body{{max-width:46em;margin:2em auto;padding:0 1em}}</style></head>")
    out = sibling(src, None, ".html")
    out.write_text(styled, encoding="utf-8")
    return [out]


def text_to_pdf(src: Path) -> list[Path]:
    text = read_text(src)
    body = f"<pre style='white-space: pre-wrap; background: none'>{html_lib.escape(text)}</pre>"
    return [html_to_pdf_file(f"<html><body>{body}</body></html>", sibling(src, None, ".pdf"), src.parent)]


# ---------- ebooks ----------

def ebook_to_pdf(src: Path) -> list[Path]:
    import pymupdf

    try:
        doc = pymupdf.open(src)
    except Exception as e:
        raise UserError(f"Couldn't open {src.name}: {e}. (DRM-protected ebooks can't be converted.)") from e
    if doc.is_reflowable:
        doc.layout(rect=pymupdf.paper_rect("letter"), fontsize=11)
    toc = doc.get_toc()
    report(None, f"Converting {src.name}")
    pdf = pymupdf.open("pdf", doc.convert_to_pdf())
    if toc:
        try:
            pdf.set_toc(toc)
        except Exception:
            pass
    meta = {k: v for k, v in (doc.metadata or {}).items() if k in ("title", "author", "subject") and v}
    if meta:
        pdf.set_metadata(meta)
    out = sibling(src, None, ".pdf")
    pdf.save(out, garbage=3, deflate=True)
    return [out]


def ebook_to_text(src: Path) -> list[Path]:
    import pymupdf

    doc = pymupdf.open(src)
    text = "\n".join(p.get_text() for p in doc)
    out = sibling(src, None, ".txt")
    out.write_text(text, encoding="utf-8")
    return [out]


# ---------- tabular data ----------

def _read_csv(path: Path) -> list[list[str]]:
    text = read_text(path)
    if path.suffix.lower() == ".tsv":
        dialect = csv.excel_tab
    else:
        try:
            dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def _typed(v: str):
    """'42' -> 42, '3.5' -> 3.5, so Excel treats numbers as numbers. Keeps leading zeros (ZIPs, IDs)."""
    s = v.strip()
    if not s or (len(s) > 1 and s[0] == "0" and s[1] != "."):
        return v
    if s.lstrip("-").isdigit():
        return int(s) if len(s) < 16 else v  # long digit strings (card/phone numbers) stay text
    if s.lower().lstrip("+-") in ("nan", "inf", "infinity"):
        return v
    try:
        return float(s)
    except ValueError:
        return v


def _rows_from(path: Path) -> list[tuple[str, list[list]]]:
    """Return [(sheet_name, rows)] for any data file."""
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv"):
        return [(path.stem, _read_csv(path))]
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError as e:
            raise UserError("Excel support needs 'openpyxl' (pip install openpyxl).") from e
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheets = [(ws.title, [["" if c is None else c for c in row] for row in ws.iter_rows(values_only=True)])
                  for ws in wb.worksheets]
        wb.close()
        return sheets
    if ext == ".json":
        try:
            data = json.loads(read_text(path))
        except json.JSONDecodeError as e:
            raise UserError(f"{path.name} isn't valid JSON: {e}") from e
        if isinstance(data, dict):  # {"rows": [...]} or a single record
            lists = [v for v in data.values() if isinstance(v, list)]
            data = lists[0] if len(lists) == 1 else [data]
        if not isinstance(data, list) or not data:
            raise UserError(f"{path.name} isn't a list of records, so it can't become a table.")
        if all(isinstance(r, dict) for r in data):
            cols: list[str] = []
            for r in data:
                cols += [k for k in r if k not in cols]
            rows = [cols] + [[_cell(r.get(c, "")) for c in cols] for r in data]
        else:
            rows = [[_cell(v) for v in (r if isinstance(r, list) else [r])] for r in data]
        return [(path.stem, rows)]
    raise UserError(f"Don't know how to read {path.name} as a table.")


def _cell(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def to_xlsx(src: Path) -> list[Path]:
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in _rows_from(src):
        ws = wb.create_sheet(title=(name or "Sheet")[:31].translate(str.maketrans("", "", "[]:*?/\\")) or "Sheet")
        for row in rows:
            ws.append([_typed(v) if isinstance(v, str) else v for v in row])
        if rows:  # bold header, freeze it, size columns
            for c in ws[1]:
                c.font = openpyxl.styles.Font(bold=True)
            ws.freeze_panes = "A2"
            for i in range(1, max(len(r) for r in rows) + 1):
                width = max((len(str(r[i - 1])) for r in rows[:500] if len(r) >= i), default=8)
                ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)
    out = sibling(src, None, ".xlsx")
    wb.save(out)
    return [out]


def to_csv(src: Path) -> list[Path]:
    sheets = _rows_from(src)
    sheets = [s for s in sheets if any(any(str(c).strip() for c in r) for r in s[1])] or sheets[:1]
    if len(sheets) == 1:
        targets = [sibling(src, None, ".csv")]
    else:
        folder = unique_dir(src.with_name(f"{src.stem} (csv)"))
        targets = [folder / f"{name}.csv" for name, _ in sheets]
    for (name, rows), out in zip(sheets, targets):
        with open(out, "w", newline="", encoding="utf-8-sig") as f:  # BOM: Excel opens UTF-8 correctly
            csv.writer(f).writerows(rows)
    return targets


def to_json(src: Path) -> list[Path]:
    name, rows = _rows_from(src)[0]
    if not rows:
        raise UserError(f"{src.name} is empty.")
    header = [str(h) or f"column{i + 1}" for i, h in enumerate(rows[0])]
    records = [{h: (_typed(v) if isinstance(v, str) else v) for h, v in zip(header, r)} for r in rows[1:]]
    out = sibling(src, None, ".json")
    out.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return [out]


def table_to_pdf(src: Path) -> list[Path]:
    """CSV/TSV as a readable, printable table (header row bold, zebra stripes)."""
    rows = _rows_from(src)[0][1]
    if not rows:
        raise UserError(f"{src.name} is empty.")
    if len(rows) > 20000:
        raise UserError(f"{src.name} has {len(rows):,} rows: too many for a PDF. Convert it to Excel instead.")
    esc = html_lib.escape
    head = "".join(f"<th>{esc(str(c))}</th>" for c in rows[0])
    body = "".join(
        f"<tr{' class=z' if i % 2 else ''}>" + "".join(f"<td>{esc(str(c))}</td>" for c in r) + "</tr>"
        for i, r in enumerate(rows[1:]))
    css = "<style>table{font-size:8pt} th{background:#dde3f0} tr.z td{background:#f4f6fa}</style>"
    html = f"<html><head>{css}</head><body><h3>{esc(src.name)}</h3><table><tr>{head}</tr>{body}</table></body></html>"
    return [html_to_pdf_file(html, sibling(src, None, ".pdf"), src.parent)]
