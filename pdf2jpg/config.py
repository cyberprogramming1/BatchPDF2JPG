"""Central configuration: constants, quality presets, concurrency policy."""

from __future__ import annotations

import os
from dataclasses import dataclass

APP_NAME = "PDF to JPG Converter"
APP_ID = "PDF2JPG"
APP_VERSION = "1.0.0"

# ---- Limits ---------------------------------------------------------------
MAX_BATCH_FILES = 50                 # hard cap on PDFs per run
MAX_PAGE_PIXELS = 150_000_000        # ~450 MB RGB per page; larger pages get a lower DPI
JPEG_MAX_DIMENSION = 65_500          # JPEG format limit is 65,535 px per side
MIN_EFFECTIVE_DPI = 20
PAGE_TIMEOUT_SECONDS = 300           # kill a single stuck Poppler call after this long
PDFINFO_TIMEOUT_SECONDS = 60

# ---- GUI ------------------------------------------------------------------
LOG_MAX_LINES = 2000
MAX_TOASTS_PER_RUN = 5               # pop-up alerts per run; the rest go to the final summary
POLL_INTERVAL_MS = 100


@dataclass(frozen=True)
class QualityPreset:
    label: str
    dpi: int
    jpeg_quality: int

    @property
    def hint(self) -> str:
        return f"{self.dpi} DPI  |  JPEG quality {self.jpeg_quality}  |  4:4:4 chroma (no colour smoothing)"


QUALITY_PRESETS = (
    QualityPreset("Standard - 150 DPI", 150, 90),
    QualityPreset("High - 300 DPI", 300, 95),
    QualityPreset("Ultra - 600 DPI", 600, 98),
)
PRESETS_BY_LABEL = {p.label: p for p in QUALITY_PRESETS}
DEFAULT_PRESET = QUALITY_PRESETS[1]

WORKER_CHOICES = ("Auto", "1", "2", "3", "4")
MAX_WORKERS = 4


def auto_workers(dpi: int) -> int:
    """Pick a concurrency level that keeps RAM and CPU in check.

    Each in-flight page holds a full uncompressed bitmap (A4 @ 600 DPI is ~100 MB),
    so higher DPI -> fewer parallel PDFs. One core is always left free for the GUI.
    """
    cpu = os.cpu_count() or 2
    cap = 4 if dpi <= 150 else 3 if dpi <= 300 else 2
    return max(1, min(cap, cpu - 1))


def resolve_workers(choice: str, dpi: int) -> int:
    if str(choice).strip().lower() == "auto":
        return auto_workers(dpi)
    try:
        return max(1, min(MAX_WORKERS, int(choice)))
    except (TypeError, ValueError):
        return auto_workers(dpi)
