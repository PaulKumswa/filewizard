# Overnight build log

Running notes: what was tried, what happened, what's next. Newest at the bottom of each section.

## Decisions & experiments (closed loops)

| # | Question | Tried | Result | Status |
|---|----------|-------|--------|--------|
| 1 | Can I verify the menu on real Windows without a Windows PC? | GitHub Actions `windows-latest` + a probe that builds the context menu through the same COM objects Explorer uses (`IShellFolder` → `IContextMenu`) | FileWizard appears in the real menu ✅ | done |
| 2 | Probe saw FileWizard ▸ but its submenu was empty | pywin32 `IContextMenu3` | not exposed by pywin32 → can't simulate hover | ❌ dropped |
| 3 | (same) | raw ctypes vtable calls: `IContextMenu3::HandleMenuMsg2(WM_INITMENUPOPUP)` — what Explorer sends on hover | works: probe now sees the full cascade; 8/8 real-shell tests pass (single click, 4-PDF merge, jpg+png mixed) | ✅ done |
| 10 | CI dump showed only 16 entries per FileWizard menu | experiment: 30 flat / 3×10 nested / `ExtendedSubCommandsKey` flat / nested | **every layout capped at 16 entries** (incl. submenu headers). Hard Windows limit for registry cascades | confirmed |
| 11 | Live with the 16 cap? | compact classic menu: core actions + "All actions…" palette (searchable window with everything) | always ≤16, tested; palette screenshot-verified under Xvfb | ✅ fallback |
| 12 | Full menu without the cap + selection-aware items (Merge only when several selected) | native `IContextMenu` shell extension DLL (C++), built by MSVC in CI | in progress | testing |
| 13 | First native-DLL CI run: all classic-mode shell tests errored in setup | read the CI log | **real bug**: `install` printed "→" and crashed with UnicodeEncodeError on Windows' default cp1252 console. Would have broken `install.bat` for many users. Fixed (ASCII messages + CLI sets `errors=replace`) + regression test | ✅ fixed |
| 14 | First installer build failed at "Silent install" | read log | install had actually worked; my CI script piped `reg query` into `Select-Object -First 12`, which broke the pipe and made reg.exe exit 1 | ✅ fixed (CI script) |
| 15 | Word → PDF only tested on Linux | CI job installs LibreOffice on Windows via Chocolatey, runs Word/Excel → PDF + combine | see CI | testing |
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

## Backlog (rough priority)

- [x] CI green on Windows, including multi-select merge through the real shell
- [x] Standalone installer (no Python needed) built by CI
- [x] README rewrite for the new features
- [x] Image "compress under N KB/MB" (upload forms), Show details
- [ ] Settings window (notifications on/off, output folder)
- [ ] Image → favicon set / app icon pack
- [ ] PDF: sign with image, crop margins, n-up
- [ ] Video: add subtitles, extract subtitles
