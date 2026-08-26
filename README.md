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
  .venv/                               Already created & populated for you
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

## 3. Point the app at the venv's Python

In the app's **Cài đặt** (Settings) panel, set "Python interpreter" to the
venv's interpreter so PyMuPDF/requests are found:

```
/Users/cuonghoang/PDF Tools/PythonEngine/.venv/bin/python3
```

(Leaving it as `python3` works only if your default `python3` on PATH
already has `pymupdf` and `requests` installed.)

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
