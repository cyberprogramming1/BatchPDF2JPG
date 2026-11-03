"""Filesystem helpers: Poppler discovery, safe folder names, output layout."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .config import APP_ID

IS_WINDOWS = os.name == "nt"
_EXE = ".exe" if IS_WINDOWS else ""


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
def app_base_dir() -> Path:
    """Folder that holds bundled resources (PyInstaller's temp dir when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def default_output_dir() -> Path:
    return Path.home() / "Pictures" / "PDF2JPG_Output"


def log_dir() -> Path:
    if IS_WINDOWS:
        root = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(root) / APP_ID / "logs"
    return Path.home() / f".{APP_ID.lower()}" / "logs"


# ---------------------------------------------------------------------------
# Poppler discovery
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PopplerInfo:
    path: Optional[str]   # directory to pass as poppler_path; None = use PATH
    ok: bool
    message: str


def _has_poppler(directory: Path) -> bool:
    return (directory / f"pdftoppm{_EXE}").is_file() and (directory / f"pdfinfo{_EXE}").is_file()


def find_poppler() -> PopplerInfo:
    """Locate Poppler binaries.

    Search order:
      1. POPPLER_PATH environment variable
      2. ``poppler/`` folder bundled inside the executable (PyInstaller --add-data)
      3. ``poppler/`` folder next to the executable / project
      4. System PATH
    """
    candidates: list[Path] = []

    env = os.environ.get("POPPLER_PATH")
    if env:
        candidates.append(Path(env))

    bases = [app_base_dir()]
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)

    for base in bases:
        root = base / "poppler"
        candidates += [root / "Library" / "bin", root / "bin", root]
        if root.is_dir():
            try:
                candidates += [p.parent for p in root.rglob(f"pdftoppm{_EXE}")]
            except OSError:
                pass

    for cand in candidates:
        if _has_poppler(cand):
            return PopplerInfo(str(cand), True, f"Poppler found: {cand}")

    if shutil.which("pdftoppm") and shutil.which("pdfinfo"):
        return PopplerInfo(None, True, "Poppler found on system PATH")

    return PopplerInfo(
        None,
        False,
        "Poppler was not found. Place the Poppler folder next to the program as "
        "'poppler\\Library\\bin' or add Poppler's bin folder to PATH (see README).",
    )


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_folder_name(name: str) -> str:
    """Make a PDF stem safe as a folder name. Normal names pass through unchanged."""
    cleaned = _INVALID_CHARS.sub("_", name).strip().rstrip(". ")
    if not cleaned:
        cleaned = "pdf"
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned[:150]


def unique_name(name: str, used: set[str]) -> str:
    """Return ``name`` or ``name_2``, ``name_3`` ... so two PDFs never share a folder."""
    candidate, i = name, 2
    while candidate.casefold() in used:
        candidate = f"{name}_{i}"
        i += 1
    used.add(candidate.casefold())
    return candidate


def path_key(p: Path | str) -> str:
    """Normalised key for duplicate detection (case-insensitive on Windows)."""
    return os.path.normcase(os.path.abspath(str(p)))


def make_batch_root(output_root: Path) -> Path:
    """Create ``PDF_Batch_YYYYMMDD_HHMMSS`` (suffixed if it already exists)."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate, i = output_root / f"PDF_Batch_{stamp}", 2
    while candidate.exists():
        candidate = output_root / f"PDF_Batch_{stamp}_{i}"
        i += 1
    candidate.mkdir(parents=True)
    return candidate


def expand_inputs(paths: Iterable[str | Path]) -> tuple[list[Path], int]:
    """Expand files/folders into PDF paths. Returns (pdfs, ignored_count)."""
    pdfs: list[Path] = []
    ignored = 0
    for raw in paths:
        p = Path(raw)
        try:
            if p.is_dir():
                found = sorted(
                    (c for c in p.iterdir() if c.is_file() and c.suffix.lower() == ".pdf"),
                    key=lambda c: c.name.casefold(),
                )
                pdfs.extend(found)
            elif p.is_file() and p.suffix.lower() == ".pdf":
                pdfs.append(p)
            else:
                ignored += 1
        except OSError:
            ignored += 1
    return pdfs, ignored


def open_folder(path: Path | str) -> None:
    target = str(path)
    if IS_WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
