# Overnight build log

## Start here (morning summary)

**Try it:** GitHub → Actions → latest green *CI* run → Artifacts → **FileWizard-Setup** → run it
(one UAC prompt) → right-click anything → **FileWizard ▸** (Windows 11: *Show more options* / Shift+right-click).

What's verified on real Windows by CI (not just on Linux):
- The full right-click menu, built by a native shell extension: every action, with icons, "Merge" only
  when several files are selected, and mixed selections narrowed to shared actions. Driven through the
  same COM interfaces Explorer uses, including clicks that produce the right output files.
- The classic (registry) menu as a fallback: 16-entry Windows limit measured and respected, with "All actions…".
- The installer: silent install → smoke test of the installed app (PDF, HEIC, video, audio) → uninstall cleanup.
- Word/Excel → PDF through LibreOffice on Windows.

What is NOT verified (needs your PC): Microsoft Office conversion (no Office on CI runners),
how the dialogs look with your Windows theme, and the per-user (no-admin) native menu (see #17).

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
| 17 | Prove it works for a normal user | (a) HKLM registration for the elevated test run; (b) per-user install probed + clicked through a non-elevated "basic user" token (`runas /trustlevel:0x20000`) | (a) ✅ **12/13 pass with HKLM**: full 27-item PDF menu, Merge only with 2+ files, mixed PDF+JPG narrows to shared actions, click → correct combined PDF. (b) ❌ per-user registration not shown even to the non-elevated process | (a) done, (b) open |
| 19 | Per-user vs all-users install for the installer | per-user is unproven, HKLM is proven | **Decision: installer installs for all users** (one UAC prompt, Program Files, HKLM). DLL reads its manifest from its own folder. Per-user stays as a CI experiment with the DLL log on | ✅ decided |
| 20 | DLL log showed "Black  white" in the menu | Windows menus treat `&` as the shortcut-key marker | escape as `&&` in native + classic menus; probe now decodes like Windows does; test asserts "Black & white" | ✅ fixed |
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

## Backlog (rough priority)

- [x] CI green on Windows, including multi-select merge through the real shell
- [x] Standalone installer (no Python needed) built by CI
- [x] README rewrite for the new features
- [x] Image "compress under N KB/MB" (upload forms), Show details
- [ ] Settings window (notifications on/off, output folder)
- [ ] Image → favicon set / app icon pack
- [ ] PDF: sign with image, crop margins, n-up
- [ ] Video: add subtitles, extract subtitles
