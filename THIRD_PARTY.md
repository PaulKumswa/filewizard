# Third-party components

FileWizard's own code is under the PolyForm Noncommercial License 1.0.0 (see `LICENSE`). The installer bundles these projects,
each under its own license. Personal use is fine under all of them. **If you distribute FileWizard, the GPL/AGPL items
below require the corresponding source to be offered and don't allow extra restrictions such as
"noncommercial"**, so before distributing, replace them or obtain commercial licences (Artifex sells
one for PyMuPDF; ffmpeg can be built LGPL-only).

| Component | Used for | License |
|---|---|---|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) / MuPDF (incl. built-in Tesseract OCR engine) | PDFs, ebooks, SVG, OCR | **AGPL-3.0** (commercial licence available from Artifex) |
| [FFmpeg](https://ffmpeg.org) static build via [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | video & audio | **GPL** (this build includes x264/x265) |
| [Tesseract tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) `eng.traineddata` | OCR language data | Apache-2.0 |
| [Pillow](https://python-pillow.org) | images | MIT-CMU (HPND) |
| [pillow-heif](https://github.com/bigcat88/pillow_heif) / libheif, libde265, x265 | HEIC | BSD-3 / LGPL / GPL (x265 encoder) |
| [rawpy](https://github.com/letmaik/rawpy) / LibRaw | camera RAW | MIT / LGPL-2.1 or CDDL |
| [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx), OpenCV (headless), python-docx | PDF → Word | AGPL-3.0 (pdf2docx), Apache-2.0, MIT |
| [py7zr](https://github.com/miurahr/py7zr) | 7z | LGPL-2.1 |
| [openpyxl](https://openpyxl.readthedocs.io) | Excel | MIT |
| [Python-Markdown](https://github.com/Python-Markdown/markdown) | Markdown | BSD-3 |
| [pywin32](https://github.com/mhammond/pywin32) | Office automation | PSF |
| Python + Tcl/Tk (via PyInstaller) | runtime, dialogs | PSF, Tcl/Tk licence |
| [Inno Setup](https://jrsoftware.org/isinfo.php) | installer | Inno Setup licence |

Optional, not bundled: Microsoft Office, LibreOffice (MPL-2.0), 7-Zip (LGPL), rembg (MIT) and its model.
