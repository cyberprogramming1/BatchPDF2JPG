# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller --noconfirm --clean PDF2JPG.spec
import os
from PyInstaller.utils.hooks import collect_all

block_cipher = None
project = os.path.abspath(SPECPATH)

# --- Poppler must be present before building -------------------------------
poppler_dir = os.path.join(project, "poppler")
if not os.path.isfile(os.path.join(poppler_dir, "Library", "bin", "pdftoppm.exe")):
    raise SystemExit(
        "\n[ERROR] poppler\\Library\\bin\\pdftoppm.exe not found.\n"
        "        Download Poppler for Windows and copy its 'Library' folder into the\n"
        "        'poppler' folder next to this spec file (see README.md, step 2).\n"
    )

datas, binaries, hiddenimports = [], [], []
# CustomTkinter ships theme JSON + fonts; tkinterdnd2 ships the native tkdnd DLLs.
for pkg in ("customtkinter", "tkinterdnd2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Embed Poppler (exe + DLLs + share data) -> available at <_MEIPASS>/poppler/Library/bin
datas.append((poppler_dir, "poppler"))

icon_path = os.path.join(project, "assets", "icon.ico")

a = Analysis(
    ["main.py"],
    pathex=[project],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "numpy", "matplotlib", "pypdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PDF2JPG",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX triggers many antivirus false positives
    runtime_tmpdir=None,
    console=False,             # windowed GUI app
    icon=icon_path if os.path.isfile(icon_path) else None,
)
