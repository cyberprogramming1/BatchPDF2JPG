# PDF to JPG Converter

Desktop tool (Windows-first, runs anywhere Python + Poppler run) that converts one PDF or up to
**50 PDFs** into one `.jpg` per page. CustomTkinter GUI, drag & drop, progress bar, live log,
non-blocking failure alerts, and a headless CLI.

```
pdf2jpg_tool/
  main.py                 entry point (GUI; "--cli" for headless)
  pdf2jpg/
    config.py             limits, quality presets, thread policy
    paths.py              Poppler discovery, safe folder names, output layout
    logger.py             rotating log file
    engine.py             conversion engine (no GUI code)
    gui.py                CustomTkinter interface
    cli.py                command-line interface
  tests/                  pytest suite + GUI smoke test
  poppler/                <- you put Poppler here before building (step 2)
  PDF2JPG.spec           PyInstaller build definition
  build_exe.bat           one-click build
  requirements.txt / requirements-dev.txt
```

## Output layout

| Input | Result |
|---|---|
| 1 PDF `Invoice.pdf` | `<output>\Invoice\page_1.jpg, page_2.jpg ...` |
| 2-50 PDFs | `<output>\PDF_Batch_20261103_091500\Invoice\page_1.jpg ...` and one sub-folder per PDF |

Sub-folders carry the PDF's exact file name. Only if two PDFs from different folders share a name is the
second one suffixed (`scan`, `scan_2`) so nothing is overwritten. Re-converting a single PDF into the same
output folder overwrites files with the same names.

## Quality presets

| Preset | DPI | JPEG quality | Typical A4 page size |
|---|---|---|---|
| Standard | 150 | 90 | 1240 x 1754 px |
| High | 300 | 95 | 2480 x 3508 px |
| Ultra | 600 | 98 | 4961 x 7016 px |

All presets use 4:4:4 chroma (no colour smoothing, so coloured text stays crisp). JPEG is always lossy;
quality 95-98 is visually indistinguishable from the source for documents. If you need a bit-exact copy,
PNG would be the right format.

Pages so large that they would exceed JPEG's 65,535 px limit or ~150 megapixels (huge posters/plans) are
rendered at a reduced DPI automatically and a warning is logged, instead of crashing.

## Memory & throttling

* One page at a time per worker: render -> save -> close bitmap -> `gc.collect()`.
* Thread count **Auto** = 4 / 3 / 2 workers for 150 / 300 / 600 DPI, always leaving one CPU core free.
* On Windows, Poppler child processes run at *below-normal priority* with no console window, so the PC
  stays responsive during large batches.
* Each Poppler call has a 5-minute timeout; a stuck page cannot hang the queue.
* Files are written as `.part` then renamed, so a cancel/crash never leaves a broken JPG.

---

## 1. Set up Python (development / building)

Python 3.10+ (64-bit). From the project folder:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
```

## 2. Poppler setup (Windows)

The app needs Poppler's `pdftoppm.exe` and `pdfinfo.exe`.

1. Download the latest **Poppler for Windows** zip from the *Releases* page of
   `github.com/oschwartz10612/poppler-windows` (file name like `Release-xx.xx.x-0.zip`).
2. Extract it. Inside you will find a folder like `poppler-xx.xx.x\` containing `Library\` (and `share\`).
3. Copy the **`Library`** folder into this project's `poppler` folder, so that this file exists:

   ```
   pdf2jpg_tool\poppler\Library\bin\pdftoppm.exe
   ```

   (`Library\bin` holds the exe and DLLs; keep `Library\share` too, it contains font/encoding data that
   some PDFs, especially CJK ones, need.)
4. That is all. The app looks for Poppler in this order: `POPPLER_PATH` environment variable ->
   embedded/bundled `poppler\` folder -> `poppler\` next to the program -> system `PATH`.

Quick check: `poppler\Library\bin\pdftoppm.exe -v` should print a version.

*Linux / macOS (running from source only):* `sudo apt install poppler-utils` or `brew install poppler`.

## 3. Run from source

```bat
python main.py                       :: GUI
python main.py --cli report.pdf C:\scans -o D:\out --dpi 300 --workers 2
python -m pytest -q                  :: tests (need Poppler)
```

## 4. Build the standalone `.exe`

**One click:** double-click `build_exe.bat` (creates a venv, installs deps, runs the tests, builds).

**Manual, step by step:**

```bat
.venv\Scripts\activate
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean PDF2JPG.spec
```

**Equivalent single command without the spec file:**

```bat
pyinstaller --noconfirm --clean --onefile --windowed --name PDF2JPG ^
  --collect-all customtkinter --collect-all tkinterdnd2 ^
  --add-data "poppler;poppler" main.py
```

Result: **`dist\PDF2JPG.exe`**, a single file (expect roughly 50-70 MB with Poppler embedded). Copy it to any Windows 10/11 64-bit PC
and double-click. No Python, no Poppler, no install needed.

How the embedding works: `--add-data "poppler;poppler"` packs the folder into the exe. At start-up
PyInstaller unpacks it to a temp folder and `paths.find_poppler()` finds
`<temp>\poppler\Library\bin`, which is passed to `pdf2image` as `poppler_path`.
`collect-all customtkinter` bundles its theme/font files and `collect-all tkinterdnd2` bundles the native
drag-and-drop DLLs; forgetting either is the usual cause of "works in Python, crashes as exe".

### Packaging notes

* First launch of a one-file exe takes a few seconds (self-extraction). For instant start-up use `--onedir`
  (use `--onedir` instead of `--onefile`) and ship the `dist\PDF2JPG` folder.
* Some antivirus products flag unsigned PyInstaller executables. UPX is already disabled, which removes the
  most common trigger; code-signing the exe removes the rest.
* Console flashes are suppressed and Poppler children are started hidden, so the windowed build never
  pops up black windows.
* Logs: `%LOCALAPPDATA%\PDF2JPG\logs\pdf2jpg.log` (rotating, 4 x 1 MB).
* To add an icon, place `assets\icon.ico` in the project; the spec picks it up automatically.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Poppler was not found" at start-up | Complete step 2; the path must be `poppler\Library\bin\pdftoppm.exe`. |
| "PDF is password-protected" | Remove the password first (password PDFs are reported, not converted). |
| Out-of-memory message at 600 DPI | Set Threads to 1 or choose 300 DPI. |
| Drag & drop does nothing | `tkinterdnd2` missing or the app runs elevated (Windows blocks drops into admin apps from a normal Explorer). Use *Add PDFs...* instead. |
