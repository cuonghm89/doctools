# C-PDF Gear

SwiftUI macOS app that drives a Python (PyMuPDF) engine to translate PDFs to
Vietnamese in place — same layout, same images/vectors, DeepL first with a
Gemini fallback.

## Project layout

```
Package.swift                          SwiftUI app (opens directly in Xcode)
Sources/CPDFGear/
  CPDFGearApp.swift                    App entry point
  ContentView.swift                    Drag&drop UI, settings, progress
  TranslationRunner.swift              Launches the Python engine as a subprocess
PythonEngine/
  translator_engine.py                 CLI entry: PDF -> PDF, layout-preserving
  router.py                            DeepL/Gemini smart routing + quota tracker
  test_router.py                       Self-check (no network), run directly
  requirements.txt
  .venv/                               Already created & populated for you (gitignored)
scripts/
  package_app.sh                       Build .app bundle + zip for release
.github/workflows/
  ci.yml                               swift build + pytest on every push/PR
  release.yml                          Build & publish a GitHub Release on tag push
```

## 1. Python engine setup

A virtualenv is already set up at `PythonEngine/.venv`. To recreate it from
scratch:

```bash
cd "PythonEngine"
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Sanity-check the router logic (no network calls, no PDF needed):

```bash
./.venv/bin/python test_router.py
```

## 2. Run the app

Open `Package.swift` in Xcode (double-click it, or `open Package.swift`),
pick the `CPDFGear` scheme and **My Mac**, then Run (⌘R).

Or from the terminal:

```bash
swift run CPDFGear
```

## 3. Python interpreter resolution

No manual setup needed: `PythonProcess.engineDir` (in
[`PythonProcess.swift`](Sources/CPDFGear/PythonProcess.swift)) auto-resolves
`PythonEngine`'s location — from the app bundle's `Resources/PythonEngine`
when running a packaged `.app` (see [Packaging](#packaging--đóng-gói-thành-app)
below), or from the project source tree when running via `swift run`/Xcode.
It then prefers `PythonEngine/.venv/bin/python3` if that venv exists, else
falls back to whatever `python3` is on `PATH`.

## 4. Get API keys

- **DeepL**: https://www.deepl.com/pro-api — a free key ends in `:fx` and
  gets routed to the free endpoint automatically; a paid key uses the pro
  endpoint.
- **Gemini** (fallback for tables, quota overflow, or DeepL errors):
  https://aistudio.google.com/apikey

Paste both into the Settings panel. DeepL key is required to start a
translation; Gemini key is optional but recommended once you're near the
500,000 char/month DeepL free quota.

## How it works

- **Router** (`router.py`): tracks DeepL usage in
  `~/Library/Application Support/CPDFGear/usage_tracker.json`,
  reset automatically each calendar month. Plain-text blocks under quota go
  to DeepL; table-shaped blocks, over-quota text, or any DeepL error go to
  Gemini 1.5 Flash.
- **Layout preservation** (`translator_engine.py`): each page's text blocks
  are read via `page.get_text("dict")` (exact bbox + font size). The old
  text is removed with a real PDF redaction (`add_redact_annot` +
  `apply_redactions`, restricted to text only — images and vector graphics
  are explicitly excluded), then the translated text is redrawn at the same
  box with `insert_textbox`.
- **Dynamic Canvas**: font size shrinks step-by-step (down to
  `original_size * Font Scale Factor`) until the translated text's
  word-wrapped layout fits the original box, measured via an uncommitted
  `Shape` (no ghost text left behind). If even the minimum size still needs
  more vertical room, the box grows downward rather than silently dropping
  text.
- **Vietnamese glyphs**: text is drawn with the system's `Arial.ttf`
  (`/System/Library/Fonts/Supplemental/`), since PyMuPDF's built-in `helv`
  font has no Vietnamese diacritics.

## Known limitations (by design, not bugs)

- The table-vs-paragraph heuristic (`is_table_block`) is a simple
  line-count/digit-density check — good enough to route obvious tables to
  Gemini, but it will misclassify some blocks. Swap in real table detection
  (e.g. `pdfplumber`) if this matters for your documents.
- API keys are stored in `UserDefaults` (not Keychain) — fine for local
  personal use, not for a version you'd hand to other people.
- The redaction box growing downward on overflow can occasionally overlap
  the line below it on very dense pages — acceptable given the 95% layout
  fidelity target, not pixel-perfect on every page.

## Packaging / đóng gói thành .app

```bash
./scripts/package_app.sh [version]
```

Builds a release binary, generates `AppIcon.icns` from
`Sources/CPDFGear/Resources/AppIcon.png`, bundles `PythonEngine`'s `.py`
files (no `.venv`, no `test_*.py`) into `Resources/`, writes `Info.plist`,
ad-hoc code-signs, and zips the result to `dist/CPDFGear-<version>-macos.zip`.

Người nhận app (đồng nghiệp) cần tự cài Python 3 + dependencies — app
**không** đóng gói sẵn 1 Python runtime (không dùng PyInstaller/tương tự,
`.venv` gốc 252MB và không portable giữa máy):

```bash
python3 -m pip install -r PythonEngine/requirements.txt
```

Vì chưa có Apple Developer ID nên app chỉ được ký **ad-hoc** (không
notarize) — lần đầu mở trên máy khác, macOS Gatekeeper sẽ chặn; mở bằng
chuột phải > **Open** (hoặc `xattr -cr CPDFGear.app`) một lần duy nhất.

## CI/CD

- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — mọi push/PR:
  `swift build` + `pytest PythonEngine`.
- [`.github/workflows/release.yml`](.github/workflows/release.yml) — push
  tag `v*.*.*` → chạy `scripts/package_app.sh`, đính file zip vào 1 GitHub
  Release tự động.

**Luồng Dev → Prod:**

```
feature/xyz  ──PR──▶  dev  ──PR (đã test ổn)──▶  main  ──tag vX.Y.Z──▶  Release
```

- Code mới: nhánh `feature/...` từ `dev`, PR vào `dev` (CI chạy tự động).
- Khi `dev` ổn định, PR `dev` → `main`.
- Release cho người dùng: `git tag vX.Y.Z && git push origin vX.Y.Z` trên
  `main` → GitHub Actions tự build + tạo Release kèm file zip cài được ngay.
