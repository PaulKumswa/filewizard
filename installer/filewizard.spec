# PyInstaller spec: builds dist/FileWizard/ with
#   filewizard.exe   console app (command line: filewizard list, doctor, run ...)
#   filewizardw.exe  windowed app Explorer launches (no console flash)
# Run from the repo root:  pyinstaller installer/filewizard.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas, binaries, hidden = [], [], []
for pkg in ("pymupdf", "pillow_heif", "rawpy", "imageio_ffmpeg", "pdf2docx", "py7zr", "openpyxl", "markdown"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception as e:  # optional package missing: build without it
        print("skipping", pkg, e)
hidden += ["filewizard." + m for m in ("archives", "cli", "collector", "docs", "files", "images", "media",
                                        "office", "operations", "pdf", "settings", "shell_menu", "ui", "util")]
hidden += ["win32com.client", "pythoncom", "pywintypes"]
# OCR language data (downloaded by CI into installer/tessdata); PyMuPDF has the OCR engine built in
import os
if os.path.exists("tessdata/eng.traineddata"):
    datas += [("tessdata/eng.traineddata", "tessdata")]

a = Analysis(["entry.py"], pathex=[".."], datas=datas, binaries=binaries, hiddenimports=hidden,
             # onnxruntime/pymupdf_layout/pymupdf4llm arrive as optional PyMuPDF extras that
             # FileWizard never uses (~tens of MB); the frozen smoke test guards this list.
             excludes=["matplotlib", "pandas", "scipy", "IPython", "pytest", "onnxruntime",
                       "pymupdf_layout", "pymupdf4llm", "networkx", "sympy"], noarchive=False)
pyz = PYZ(a.pure)

common = dict(debug=False, strip=False, upx=False, icon="filewizard.ico")
exe_console = EXE(pyz, a.scripts, [], exclude_binaries=True, name="filewizard", console=True, **common)
exe_window = EXE(pyz, a.scripts, [], exclude_binaries=True, name="filewizardw", console=False, **common)
coll = COLLECT(exe_console, exe_window, a.binaries, a.datas, strip=False, upx=False, name="FileWizard")
