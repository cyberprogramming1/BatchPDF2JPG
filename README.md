# BatchPDF2JPG

[![Latest release](https://img.shields.io/github/v/release/cyberprogramming1/BatchPDF2JPG?label=download)](../../releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Platform: Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)

A simple, free desktop tool that converts PDF files into JPG images, **one image per page**.
Drop in one PDF or up to **50 at once**, pick a quality, and click **Convert**.

![BatchPDF2JPG screenshot](docs/screenshot.png)

## Download

1. Go to the [**Releases**](../../releases/latest) page and download `PDF2JPG.exe`.
2. Double-click it. No installation, no Python, no extra files needed (Poppler is built in).
3. The first launch can take a few seconds while the app unpacks itself.

**Requires:** Windows 10 or 11, 64-bit.

> **SmartScreen warning?** The app is not code-signed, so Windows may show "Windows protected your PC".
> Click **More info → Run anyway**. You can verify your download against the SHA256 hash in the release notes:
> `Get-FileHash PDF2JPG.exe -Algorithm SHA256`

## Features

- Convert a single PDF or a batch of up to 50 PDFs
- Drag and drop, file picker, or import a whole folder
- Quality presets: **Standard** (150 DPI), **High** (300 DPI), **Ultra** (600 DPI)
- Two output layouts: one folder per PDF, or **all images in one folder**
- Progress bar, live log, and pop-up alerts for failed files. The rest of the queue keeps going
- Cancel at any time; no half-written images are left behind
- Memory-friendly: renders page by page with a limited number of threads, so large batches don't freeze your PC
- Optional command-line mode for scripts and automation

## How to use

1. Add your PDFs: drag them into the window, or click **Add PDFs...** / **Add Folder...**
2. Choose **Quality** and the **Output folder**.
3. Optional: tick **Put all images in ONE folder** if you don't want a sub-folder per PDF.
4. Click **Convert**, then **Open Output Folder** when it finishes.

### Output layout

| Input | Result |
|---|---|
| One PDF, `Invoice.pdf` | `Invoice\page_1.jpg`, `page_2.jpg`, ... |
| Several PDFs (default) | `PDF_Batch_<date_time>\Invoice\page_1.jpg`, `...\Report\page_1.jpg`, ... |
| Several PDFs + "one folder" | `PDF_Batch_<date_time>\Invoice_page_1.jpg`, `Report_page_1.jpg`, ... |

If two PDFs share the same name, the second gets a suffix (`scan`, `scan_2`), so nothing is overwritten.

### Quality presets

| Preset | DPI | JPEG quality | A4 page size |
|---|---|---|---|
| Standard | 150 | 90 | 1240 × 1754 px |
| High | 300 | 95 | 2480 × 3508 px |
| Ultra | 600 | 98 | 4961 × 7016 px |

### Threads

**Auto** (recommended) picks 4, 3 or 2 parallel conversions for 150, 300 and 600 DPI and always leaves one CPU core free.
Choose **1** if your PC has little RAM and you convert large PDFs at 600 DPI.

## Good to know

- JPEG is a lossy format. All presets use 4:4:4 chroma (no colour smoothing), and quality 90-98 is visually
  indistinguishable from the source for documents. Need an exact copy? Use PNG instead (not supported yet).
- Password-protected PDFs are not supported; they are reported as failed and skipped.
- Extremely large pages (posters, plans) that would exceed JPEG's size limits are rendered at a reduced DPI automatically,
  with a warning in the log.
- Log file: `%LOCALAPPDATA%\PDF2JPG\logs\pdf2jpg.log`

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Poppler was not found" | Only when running from source. See *Run from source* below. |
| Out-of-memory message at 600 DPI | Set **Threads** to 1 or choose 300 DPI. |
| Drag and drop does nothing | Windows blocks drops into apps running as administrator. Start the app normally, or use **Add PDFs...**. |
| Antivirus blocks the file | The app is unsigned. Check the SHA256 hash against the release notes, then allow it. |

---

## For developers

### Project layout

```
main.py                 entry point (GUI; "--cli" for headless)
pdf2jpg/
  config.py             limits, quality presets, thread policy
  paths.py              Poppler discovery, safe folder names, output layout
  logger.py             rotating log file
  engine.py             conversion engine (no GUI code)
  gui.py                CustomTkinter interface
  cli.py                command-line interface
tests/                  pytest suite + GUI smoke test
poppler/                Poppler goes here before building (not committed)
PDF2JPG.spec            PyInstaller build definition
build_exe.bat           one-click build
```

### Run from source

Python 3.10+ (64-bit), plus Poppler:

1. Download **Poppler for Windows** from the [poppler-windows releases](https://github.com/oschwartz10612/poppler-windows/releases).
2. Extract it and copy its **`Library`** folder into this project's `poppler` folder, so that
   `poppler\Library\bin\pdftoppm.exe` exists. (Linux: `sudo apt install poppler-utils`; macOS: `brew install poppler`.)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

python main.py                                   :: GUI
python main.py --cli report.pdf C:\scans -o D:\out --dpi 300 --workers 2 --flat
python -m pytest -q                              :: tests (need Poppler)
```

The app looks for Poppler in this order: `POPPLER_PATH` environment variable → bundled `poppler\` folder → `poppler\` next to the
program → system `PATH`.

### Build the standalone `.exe`

With Poppler in place (see above), double-click `build_exe.bat`, or run:

```bat
.venv\Scripts\activate
pip install -r requirements-dev.txt
pyinstaller --noconfirm --clean PDF2JPG.spec
```

The result is `dist\PDF2JPG.exe` (about 85 MB, Poppler embedded). The spec bundles `customtkinter` (themes and fonts),
`tkinterdnd2` (drag-and-drop DLLs) and the `poppler` folder. Without those, the app works from source but crashes as an exe.

Notes: first launch of a one-file exe takes a few seconds (use `--onedir` for instant start-up); UPX is disabled on purpose
because it triggers antivirus false positives.

### How it works

- One Poppler call per page, then save, close the bitmap, `gc.collect()`: only one page bitmap is alive per worker.
- A bounded thread pool converts several PDFs in parallel; Poppler runs as a child process (hidden, below-normal priority on Windows).
- Images are written to a `.part` file and renamed, so cancel, crash or a full disk never leaves a broken JPG.
- The engine reports through an event callback only, so the GUI and the CLI share the same code.

## License

MIT, see [LICENSE](LICENSE). Third-party components, including the GPL-licensed Poppler that is embedded in the released
executable, are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
