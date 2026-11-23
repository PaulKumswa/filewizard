# Overnight build log

## Start here (morning summary)

**Try it:** GitHub → Actions → latest green *CI* run → Artifacts → **FileWizard-Setup** → unzip → run
(SmartScreen: *More info → Run anyway*; one admin prompt) → right-click anything → **FileWizard ▸**.
Windows 11: *Show more options* / Shift+right-click, or tick the installer option that brings back the full menu.

**Verified on real Windows by CI** (GitHub's Windows Server runners), not just on Linux:
- The full right-click menu (native shell extension): every action, icons, "Merge" only with 2+ files,
  mixed selections narrowed to shared actions, recent actions on top, hidden-actions setting,
  folder-background menu (paste clipboard as file). Driven through the same COM interfaces Explorer
  uses, including clicks that produce the right files.
- The installed app **as a normal (non-admin) user**: menu appears, clicking "Merge selected PDFs" works.
- The installer: silent install, smoke test (PDF, HEIC, video, audio, **OCR without installing Tesseract**),
  uninstall leaves nothing behind. **Startup 0.25 s; JPG→PNG end to end 0.32 s.**
- Classic registry menu fallback (Windows' 16-entry cap measured and respected) incl. multi-select merge.
- Word/Excel → PDF through LibreOffice on Windows. Every dialog opens/closes cleanly in light and dark mode.

**Not verified (needs your PC):** Microsoft Office itself (no Office on CI; the code is defensive: separate
instance, fail-fast on prompts, 3-minute watchdog), how dialogs look under your Windows theme, and the
visual effect of the Windows 11 full-menu option (the setting itself is tested).

**Note on the timeline:** work paused at ~3:00 AM Eastern (08:00 UTC) when this session hit its usage limit,
and resumed at 7:10 AM Eastern just to fix the one failing test from the last run (a colour-emoji font
picked by a test) and push. The last run with the installer fully green end to end is run #25; the final
pushes (OCR languages, paper size, font fix) passed all 282 local tests and are in CI now.

Running notes below: what was tried, what happened, what's next.

## Decisions & experiments (closed loops)

| # | Question | Tried | Result | Status |
|---|----------|-------|--------|--------|
| 1 | Can I verify the menu on real Windows without a Windows PC? | GitHub Actions `windows-latest` + a probe that builds the context menu through the same COM objects Explorer uses (`IShellFolder` → `IContextMenu`) | FileWizard appears in the real menu ✅ | done |
| 2 | Probe saw FileWizard ▸ but its submenu was empty | pywin32 `IContextMenu3` | not exposed by pywin32 → can't simulate hover | ❌ dropped |
| 3 | (same) | raw ctypes vtable calls: `IContextMenu3::HandleMenuMsg2(WM_INITMENUPOPUP)` — what Explorer sends on hover | works: probe now sees the full cascade; 8/8 real-shell tests pass (single click, 4-PDF merge, jpg+png mixed) | ✅ done |
| 10 | CI dump showed only 16 entries per FileWizard menu | experiment: 30 flat / 3×10 nested / `ExtendedSubCommandsKey` flat / nested | **every layout capped at 16 entries** (incl. submenu headers). Hard Windows limit for registry cascades | confirmed |
| 11 | Live with the 16 cap? | compact classic menu: core actions + "All actions…" palette (searchable window with everything) | always ≤16, tested; palette screenshot-verified under Xvfb | ✅ fallback |
| 12 | Full menu without the cap + selection-aware items (Merge only when several selected) | native `IContextMenu` shell extension DLL (C++), built by MSVC in CI | compiles (MSVC + MinGW); first run: menu didn't appear | see 16–17 |
| 16 | Why didn't the native menu appear in CI? Hypothesis A: elevated processes ignore per-user COM classes | direct `CoCreateInstance` test + opt-in DLL log | **A was wrong**: CoCreateInstance works and the DLL loads. But the DLL log shows the *shell* never requested the handler: in elevated processes the shell skips per-user shell extensions (7-Zip's machine-wide one still loads) | ✅ diagnosed |
| 17 | Prove it works for a normal user | (a) HKLM registration for the elevated test run; (b) per-user install probed + clicked through a non-elevated "basic user" token (`runas /trustlevel:0x20000`) | (a) ✅ **12/13 pass with HKLM**: full 27-item PDF menu, Merge only with 2+ files, mixed PDF+JPG narrows to shared actions, click → correct combined PDF. (b) ❌ per-user registration not shown even to the non-elevated process; DLL log confirms the shell never loads it | (a) done; (b) closed: installer goes all-users (#19) |
| 19 | Per-user vs all-users install for the installer | per-user is unproven, HKLM is proven | **Decision: installer installs for all users** (one UAC prompt, Program Files, HKLM). DLL reads its manifest from its own folder. Per-user stays as a CI experiment with the DLL log on | ✅ decided |
| 20 | DLL log showed "Black  white" in the menu | Windows menus treat `&` as the shortcut-key marker | escape as `&&` in native + classic menus; probe now decodes like Windows does; test asserts "Black & white" | ✅ fixed |
| 23 | Installer was 179 MB | exclude onnxruntime / pymupdf_layout / pymupdf4llm (optional PyMuPDF extras FileWizard doesn't use) | smoke test still passes; artifact 168 MB zipped (most of the rest is ffmpeg, OpenCV for PDF→Word, PyMuPDF) | ✅ done |
| 24 | **End-to-end as a real user** | all-users installer → non-elevated process right-clicks 2 PDFs → clicks "Merge selected PDFs" | ✅ merged PDF produced; uninstall leaves nothing behind | ✅ done |
| 25 | Folder-background menu in classic mode came back empty in the probe | IContextMenu2 fallback | still empty: a background menu probed outside a real Explorer window doesn't fill static cascades (no view site). FileWizard entry itself is present. Test now checks the registered verb + runs its command like Explorer; native mode does the full click | ✅ adjusted |
| 26 | OCR needed a separate Tesseract install | PyMuPDF has the OCR engine built in; it only needs language data | installer bundles `eng.traineddata` (4 MB); CI: the installed app read "Invoice…" from an image | ✅ done |
| 27 | "Show more options" on Windows 11 | opt-in, per-user, reversible Windows setting that restores the full menu | installer checkbox (unchecked) + settings toggle + CLI; CI round-trips the setting | ✅ done |
| 28 | How fast is a right-click action? | CI times the installed exe | **startup 0.25 s; JPG→PNG end to end 0.32 s** | ✅ measured |
| 29 | Office path review (can't run Office in CI) | read every COM call | .txt via Word could hit Word's hidden encoding prompt → .txt now always uses FileWizard's own text→PDF; password/link prompts already fail fast | ✅ fixed |
| 30 | Robustness review | read-only source folders; rename failing midway; parallel batches; non-English/emoji names; malicious tar links; Python 3.10/3.11 | results fall back to Downloads\FileWizard; rename is all-or-nothing (rollback); **found + fixed a real race** (parallel threads shared a probe filename → 5 of 12 outputs misplaced); unicode works everywhere; tar links/devices blocked on any Python | ✅ done |
| 31 | Do all ~150 actions survive every file kind they accept? | new test runs every action on samples of every kind (images incl. HEIC/SVG, PDF, video, audio, CSV/JSON/XLSX, Markdown, ZIP, fonts, folders) | no crashes; reviewed every polite refusal: all legitimate except a confusing "select at least two files" message for one-document folders → fixed | ✅ done |
| 22 | All-users installer: "filewizard.exe not found" in Program Files | read log | Inno Setup defaults to 32-bit mode → installed to `Program Files (x86)` and the 32-bit registry view. Set 64-bit install mode | ✅ fixed |
| 21 | Mixed PDF + JPG selection said "Merge selected PDFs…" | manifest carries an optional label for mixed selections | shows "Combine selected into one PDF…" | ✅ fixed |
| 13 | First native-DLL CI run: all classic-mode shell tests errored in setup | read the CI log | **real bug**: `install` printed "→" and crashed with UnicodeEncodeError on Windows' default cp1252 console. Would have broken `install.bat` for many users. Fixed (ASCII messages + CLI sets `errors=replace`) + regression test | ✅ fixed |
| 14 | First installer build failed at "Silent install" | read log | install had actually worked; my CI script piped `reg query` into `Select-Object -First 12`, which broke the pipe and made reg.exe exit 1 | ✅ fixed (CI script) |
| 15 | Word → PDF only tested on Linux | CI job installs LibreOffice on Windows via Chocolatey, runs Word/Excel → PDF + combine | ✅ passes on real Windows (path auto-detected under Program Files) | done |
| 18 | Does the frozen installer build actually work? | silent install on the runner + smoke test of the installed exe | ✅ install OK; installed exe did PDF→PNG, JPG→HEIC, video→MP3 and video→GIF with the bundled ffmpeg | done |
| 4 | Mixed selections (a .jpg + a .png) | v1 numbered verbs per file type, so "Convert to WebP" had different key names on .jpg vs .png | fixed: keys use one global number per action; a unit test asserts every verb has one key name everywhere | done |
| 5 | Menu for file types FileWizard doesn't know (e.g. `.bin`) without showing two FileWizard menus on known types | `HKCU\Software\Classes\*\shell\FileWizard` + `AppliesTo = NOT (System.FileExtension:=".pdf" OR …)` | works on real Windows: `.bin` gets More ▸, `.pdf` shows exactly one FileWizard | ✅ done |
| 6 | Where does ffmpeg come from? | `imageio-ffmpeg` wheel (bundled static ffmpeg with x264/x265/VP9/AV1/Opus/MP3) | works, no manual install | done |
| 7 | AVIF/HEIC output | Pillow 12 has native AVIF; pillow-heif writes HEIC | both round-trip in tests | done |
| 8 | PyMuPDF quirk: reading `needs_pass` after `authenticate()` corrupts decrypted saves | check encryption *before* authenticating | fixed + regression test (from v1) | done |
| 9 | Extracting `proj.zip` that contains `proj/…` landed in `proj (2)` | unwrap same-named single top folder before "extract here" | fixed + test | done |

## Not doing (and why)

- **Windows 11 top-level (short) menu.** Requires an `IExplorerCommand` COM DLL inside a *signed* MSIX/sparse package. Signing needs a paid certificate or developer mode on your PC; not something I can produce overnight. FileWizard lives under "Show more options" / Shift+right-click. Revisit if you want: it's a C++/Rust DLL project.
- **RAR creation.** Proprietary format; extraction works (via 7-Zip or Windows' built-in `tar`).
- **Background removal by default.** The AI model is ~170 MB; it's offered but installs only if you `pip install rembg`.

## Built tonight (beyond v1)

- Nested menu (Convert to ▸ …) everywhere; native full menu with icons, recent actions on top,
  selection-aware items; classic fallback that respects Windows' 16-entry cap + "All actions…" palette
- Video & audio (bundled ffmpeg): convert, compress (incl. fit-under-N-MB), resize, trim, rotate, speed,
  mute, normalize, join, soundtrack, subtitles, frames, ringtone, "MP4 that plays everywhere"
- Images: HEIC/AVIF/SVG/RAW/PSD…, compress-to-size, resize presets, flips, crop borders, pad square,
  watermark, icon pack, stitch, GIF, slideshow, contact sheet, OCR, metadata/GPS removal
- PDF: combine anything, PDF → Excel tables, sign, page picker (extract/delete/rotate some), compress-to-size,
  watermark, numbers, 2/4-up, trim margins, remove blank pages, flatten, grayscale, OCR, copy text
- Office: → PDF/PNG/text, compress pictures inside; ebooks, Markdown, HTML, CSV → PDF; CSV ⇄ Excel ⇄ JSON
- Archives (zip-slip safe), ZIP/7z, batch rename (`{taken}` photo date), checksums, Show details (GPS warning)
- UX: progress + Cancel, Done toast (Open / Show in folder / Undo → Recycle Bin), settings window,
  parallel image batches, output-folder setting
- Installer (all users, 64-bit), CI on real Windows incl. real-shell clicks and a non-elevated user check

## Next ideas (not started)

- **Windows 11 short (top-level) menu**: needs an `IExplorerCommand` handler shipped in a *signed* sparse
  MSIX package. Signing needs a certificate (self-signed would mean adding it to your machine's trusted
  store), and CI can't see the Win11 short menu to verify it. Meanwhile: Shift+right-click, or the
  opt-in "always show the full menu" setting.
- **Microsoft Office on your PC**: the COM path is hardened (separate instance, fail-fast prompts,
  watchdog) but has never run against real Office. Try Word → PDF first and tell me what happens.
- **Code signing** the installer (removes the SmartScreen warning): needs a paid certificate.
- **More OCR languages**: the installer bundles English; others are a ~2–4 MB download each.
