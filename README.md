# FileWizard

Right-click file conversions and PDF tools for Windows File Explorer, so you don't have to
upload your files to random websites.

Right-click a file → **FileWizard** → pick an action. The result is saved next to the
original (nothing is ever overwritten, e.g. `report (merged).pdf`, `photo (2).png`).
Everything runs locally on your PC.

## What it does

| On… | Actions |
|---|---|
| **Word / PowerPoint / Excel** (`.docx .doc .rtf .odt .txt .pptx .ppt .odp .xlsx .xls .ods`) | Convert to PDF · old formats (`.doc .rtf .odt`) → `.docx` |
| **PDF** | Convert to Word, PNG, JPG (one image per page), text · **Merge** selected PDFs · Split into pages · Extract pages · Delete pages · Rotate · Reverse order · Add page numbers · Compress · Extract images · Add / remove password |
| **Images** (`.jpg .png .webp .heic .gif .bmp .tiff .ico`) | Convert to PDF, PNG, JPG, WebP, ICO, BMP, TIFF · **Combine** selected into one PDF · Resize · Compress · Rotate · Remove location/camera info |

**Multiple files:** select several, right-click one, and choose the action. Batch actions
like *Merge* and *Combine into one PDF* first show a window where you can reorder files.
Actions that ask a question (pages, password, size) ask once for the whole selection.

## Install

1. Install **Python 3.10+ from [python.org](https://www.python.org/downloads/)**. During
   setup, tick *"Add python.exe to PATH"*. Don't use the Microsoft Store version: it's
   sandboxed, so Explorer can't see the menu it creates (the installer checks for this).
2. Download this repo (Code → Download ZIP), unzip it, and double-click **`install.bat`**.

No admin rights are needed; the menu is added for your user account only.
To remove it, run **`uninstall.bat`**.

**Word/PowerPoint/Excel → PDF** uses Microsoft Office if you have it (identical to *Save as PDF*).
Without Office, install the free [LibreOffice](https://www.libreoffice.org/) and it's used automatically.

Run `python -m filewizard doctor` to see which optional pieces are available.

### Windows 11 note
Windows 11's new, short right-click menu only accepts apps packaged as signed MSIX, so FileWizard
shows up under **"Show more options"** (or **Shift + right-click**, or **Shift + F10**).

## Command line

Every action also works from a terminal:

```
python -m filewizard list                               # all actions and their IDs
python -m filewizard run pdf-merge a.pdf b.pdf c.pdf
python -m filewizard run pdf-extract big.pdf -p pages=1-3,7,10-
python -m filewizard run img-resize *.jpg -p size=1920
python -m filewizard export-reg menu.reg                # see exactly what install writes
```

Page ranges: `1-3,5` · `8-` (8 to end) · `-3` (1 to 3) · `last`.
Resize: `50%` · `1920` (longest side in px) · `800x600` (fit inside).

## Known limitations

- **PDF → Word** is a best-effort reconstruction (via `pdf2docx`). Simple documents convert
  well; complex layouts, forms and scans won't be perfect. Every tool, paid or free, has this
  problem, because PDFs don't store paragraphs.
- **Scanned PDFs** (images of text) have no OCR yet: *to text* and *to Word* need real text.
- **Compress** mainly shrinks PDFs with large images. Text-only PDFs are usually already small,
  and if compressing wouldn't save much, nothing is written.
- **Password removal** needs the password. This removes protection you're entitled to remove;
  it doesn't crack anything. PDFs that open freely but block printing/copying are unlocked
  without one.
- After updating the code, run `install.bat` again.
- Logs: `%LOCALAPPDATA%\FileWizard\filewizard.log`.

## How it works (for the curious)

- Menu entries live in `HKCU\Software\Classes\SystemFileAssociations\<.ext>\shell\FileWizard`,
  a cascading submenu that works no matter which app opens that file type.
- Explorer starts one process **per selected file**. `filewizard/collector.py` has them
  elect a leader through an atomic lock folder; the leader gathers every path, then runs
  the action once. That's what makes *Merge* work from a right-click.
- Explorer runs `pythonw.exe %LOCALAPPDATA%\FileWizard\launch.pyw` (no console window).
  Using a script rather than `python -m` keeps the clicked folder off Python's import path.
- Libraries: [PyMuPDF](https://pymupdf.readthedocs.io/) (PDF), [Pillow](https://python-pillow.org/)
  (images), [pillow-heif](https://github.com/bigcat88/pillow_heif) (iPhone photos),
  [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx), pywin32 (driving Office).
  PyMuPDF is AGPL-licensed: fine for personal use; check the license if you ever distribute
  this commercially.

## Development

```
pip install -e ".[dev]"
python -m pytest
```
The tests run on Linux too (registry code is tested as data; Office conversion is tested via
LibreOffice when installed).
