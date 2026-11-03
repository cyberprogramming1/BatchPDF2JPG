"""Conversion engine: PDF -> page_N.jpg, throttled, page-by-page, GUI-independent.

Design notes
------------
* One Poppler call per page (``first_page == last_page``) so only a single bitmap is
  ever alive per worker; it is closed and garbage-collected before the next page.
* A bounded ``ThreadPoolExecutor`` converts several PDFs in parallel. Poppler runs as a
  child process, so threads parallelise properly despite the GIL.
* Every JPEG is written to ``*.part`` and atomically renamed, so a crash, cancel or full
  disk never leaves a half-written ``page_N.jpg``.
* The engine talks to the outside world only through an ``on_event`` callback. The GUI
  hands it ``queue.Queue.put`` (thread-safe); the CLI hands it ``print``.
"""

from __future__ import annotations

import errno
import gc
import math
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence

import pdf2image.pdf2image as _p2i
from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPopplerTimeoutError
from PIL import Image

from . import config
from .logger import get_logger
from .paths import (
    find_poppler,
    make_batch_root,
    path_key,
    sanitize_folder_name,
    unique_name,
)

log = get_logger()

# Pages are capped by MAX_PAGE_PIXELS, so Pillow's decompression-bomb guard
# (which would otherwise reject large legitimate pages) can be relaxed to match.
Image.MAX_IMAGE_PIXELS = config.MAX_PAGE_PIXELS + 1_000_000


# ---------------------------------------------------------------------------
# Windows: no console flashes + low priority for Poppler child processes
# ---------------------------------------------------------------------------
def _install_quiet_popen() -> None:
    """pdf2image's ``pdfinfo`` call opens a console window in a --windowed build.

    Wrap Popen so every Poppler child runs hidden, below-normal priority (keeps the
    machine responsive during heavy batches) and with stdin closed.
    """
    if os.name != "nt" or getattr(_p2i, "_pdf2jpg_patched", False):
        return
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    below_normal = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
    base = _p2i.Popen

    class _QuietPopen(base):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | create_no_window | below_normal
            kwargs.setdefault("stdin", subprocess.DEVNULL)
            super().__init__(*args, **kwargs)

    _p2i.Popen = _QuietPopen
    _p2i._pdf2jpg_patched = True


_install_quiet_popen()


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
class Status(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"      # some pages failed
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ConversionSettings:
    dpi: int
    jpeg_quality: int
    workers: int
    throttle_ms: int = 0
    page_timeout: int = config.PAGE_TIMEOUT_SECONDS
    flat_output: bool = False   # batch only: all images in ONE folder, named <pdf>_page_N.jpg


@dataclass
class PdfResult:
    source: Path
    out_dir: Optional[Path] = None
    status: Status = Status.FAILED
    pages_total: int = 0
    pages_ok: int = 0
    error: str = ""
    page_errors: list[str] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class BatchSummary:
    results: list[PdfResult]
    output_dir: Optional[Path]
    cancelled: bool
    elapsed: float
    fatal_error: str = ""

    def count(self, status: Status) -> int:
        return sum(1 for r in self.results if r.status is status)

    @property
    def pages_ok(self) -> int:
        return sum(r.pages_ok for r in self.results)

    @property
    def problems(self) -> list[PdfResult]:
        return [r for r in self.results if r.status in (Status.FAILED, Status.PARTIAL)]

    def headline(self) -> str:
        parts = [
            f"{self.count(Status.SUCCESS)} OK",
            f"{self.count(Status.PARTIAL)} partial",
            f"{self.count(Status.FAILED)} failed",
        ]
        if self.cancelled:
            parts.append(f"{self.count(Status.CANCELLED)} cancelled")
        return f"{', '.join(parts)}  |  {self.pages_ok} page(s) saved in {self.elapsed:.1f}s"


@dataclass(frozen=True)
class LogEvent:
    level: str        # INFO | SUCCESS | WARNING | ERROR
    message: str


@dataclass(frozen=True)
class ProgressEvent:
    done: int
    total: int
    current: str


@dataclass(frozen=True)
class PdfDoneEvent:
    result: PdfResult


@dataclass(frozen=True)
class FinishedEvent:
    summary: BatchSummary


Event = LogEvent | ProgressEvent | PdfDoneEvent | FinishedEvent


@dataclass
class _Plan:
    source: Path
    out_dir: Path
    pages: int
    sizes: dict[int, tuple[float, float]]   # page number -> (width_pt, height_pt)
    prefix: str = ""                        # flat mode: file-name prefix (the unique PDF name)

    def file_name(self, page: int) -> str:
        return f"{self.prefix}_page_{page}.jpg" if self.prefix else f"page_{page}.jpg"


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------
_PAGE_SIZE_KEY = re.compile(r"^Page\s+(\d+)\s+size$")
_SIZE_VALUE = re.compile(r"([\d.]+)\s*x\s*([\d.]+)")


def parse_page_sizes(info: dict) -> dict[int, tuple[float, float]]:
    """Extract per-page sizes (points) from a ``pdfinfo -f 1 -l N`` dictionary."""
    sizes: dict[int, tuple[float, float]] = {}
    for key, value in info.items():
        m = _PAGE_SIZE_KEY.match(str(key).strip())
        v = _SIZE_VALUE.search(str(value))
        if m and v:
            sizes[int(m.group(1))] = (float(v.group(1)), float(v.group(2)))
    if not sizes and "Page size" in info:
        v = _SIZE_VALUE.search(str(info["Page size"]))
        if v:
            sizes[1] = (float(v.group(1)), float(v.group(2)))
    return sizes


def effective_dpi(dpi: int, size_pts: Optional[tuple[float, float]]) -> int:
    """Lower the DPI for oversized pages so the bitmap fits JPEG limits and RAM."""
    if not size_pts:
        return dpi
    w = size_pts[0] / 72 * dpi
    h = size_pts[1] / 72 * dpi
    if w <= 0 or h <= 0:
        return dpi
    scale = 1.0
    longest = max(w, h)
    if longest > config.JPEG_MAX_DIMENSION:
        scale = min(scale, config.JPEG_MAX_DIMENSION / longest)
    if w * h > config.MAX_PAGE_PIXELS:
        scale = min(scale, math.sqrt(config.MAX_PAGE_PIXELS / (w * h)))
    if scale >= 1.0:
        return dpi
    return max(config.MIN_EFFECTIVE_DPI, int(dpi * scale * 0.98))


def friendly_error(exc: BaseException) -> str:
    """Translate Poppler / OS exceptions into a one-line, user-readable reason."""
    raw = str(exc).strip()
    low = raw.lower()
    detail = next((ln.strip() for ln in reversed(raw.splitlines()) if ln.strip()), "")

    if isinstance(exc, PDFInfoNotInstalledError):
        return "Poppler is not installed or not found (see README)"
    if isinstance(exc, PDFPopplerTimeoutError):
        return "Poppler timed out (page too complex or file damaged)"
    if isinstance(exc, MemoryError):
        return "Out of memory - try a lower DPI or fewer threads"
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc.filename or detail}"
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "Disk is full"
    if "password" in low or "encrypt" in low:
        return "PDF is password-protected / encrypted (not supported)"
    if any(s in low for s in ("syntax error", "xref", "page count", "may not be a pdf", "not a valid pdf")):
        return f"PDF is corrupt or unreadable ({detail[:120]})"
    return f"{type(exc).__name__}: {detail[:200] or 'unknown error'}"


def validate_inputs(pdfs: Sequence[Path | str]) -> list[Path]:
    """De-duplicate and enforce the 1..MAX_BATCH_FILES rule."""
    seen: set[str] = set()
    unique: list[Path] = []
    for p in pdfs:
        key = path_key(p)
        if key not in seen:
            seen.add(key)
            unique.append(Path(p))
    if not unique:
        raise ValueError("No PDF files selected.")
    if len(unique) > config.MAX_BATCH_FILES:
        raise ValueError(f"Too many files: {len(unique)} selected, the limit is {config.MAX_BATCH_FILES}.")
    return unique


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------
class ConversionJob:
    def __init__(
        self,
        pdfs: Sequence[Path | str],
        output_root: Path | str,
        settings: ConversionSettings,
        on_event: Callable[[Event], None],
        poppler_path: Optional[str] | object = ...,
    ) -> None:
        self.pdfs = validate_inputs(pdfs)
        self.output_root = Path(output_root).expanduser()
        self.settings = settings
        self._on_event = on_event
        self.poppler_path = find_poppler().path if poppler_path is ... else poppler_path

        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._done_pages = 0
        self._total_pages = 0
        self._fatal = ""
        self._thread: Optional[threading.Thread] = None

    # -- public API ---------------------------------------------------------
    def start(self) -> None:
        """Run in a background thread (GUI)."""
        self._thread = threading.Thread(target=self.run, name="convert-job", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> BatchSummary:
        """Blocking run. Always emits exactly one FinishedEvent, even on unexpected errors."""
        t0 = time.perf_counter()
        results: list[PdfResult] = []
        result_dir: Optional[Path] = None
        try:
            result_dir = self._run(results)
        except Exception as exc:  # noqa: BLE001 - last line of defence
            log.exception("Unexpected failure in conversion job")
            self._fatal = self._fatal or friendly_error(exc)
            self._emit_log("ERROR", f"Fatal error: {self._fatal}")
        summary = BatchSummary(
            results=results,
            output_dir=result_dir,
            cancelled=self._cancel.is_set() and not self._fatal,
            elapsed=time.perf_counter() - t0,
            fatal_error=self._fatal,
        )
        self._emit(FinishedEvent(summary))
        return summary

    # -- internals ----------------------------------------------------------
    def _emit(self, event: Event) -> None:
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a broken listener must not kill workers
            log.exception("Event listener failed")

    def _emit_log(self, level: str, message: str) -> None:
        py_level = {"ERROR": "error", "WARNING": "warning"}.get(level, "info")
        getattr(log, py_level)(message)
        self._emit(LogEvent(level, message))

    def _run(self, results: list[PdfResult]) -> Optional[Path]:
        s = self.settings
        self.output_root.mkdir(parents=True, exist_ok=True)

        single = len(self.pdfs) == 1
        flat = s.flat_output and not single
        parent = self.output_root if single else make_batch_root(self.output_root)
        self._emit_log(
            "INFO",
            f"Starting: {len(self.pdfs)} PDF(s), {s.dpi} DPI, JPEG quality {s.jpeg_quality}, "
            f"{s.workers} thread(s)" + (", single folder." if flat else "."),
        )

        # ---- Phase 1: inspect every PDF (cheap) so progress has a real total ----
        used: set[str] = set()
        plans: list[_Plan] = []
        for src in self.pdfs:
            if self._cancel.is_set():
                break
            name = unique_name(sanitize_folder_name(src.stem), used)
            if name != src.stem:
                self._emit_log("WARNING", f"{src.name}: output name '{name}' (name clash or invalid characters).")
            try:
                plan = self._inspect(src, parent if flat else parent / name, name if flat else "")
            except Exception as exc:  # noqa: BLE001
                res = PdfResult(source=src, status=Status.FAILED, error=friendly_error(exc))
                log.debug("Inspect failure for %s", src, exc_info=True)
                results.append(res)
                self._emit_log("ERROR", f"{src.name}: {res.error}")
                self._emit(PdfDoneEvent(res))
                continue
            plans.append(plan)
            self._emit_log("INFO", f"{src.name}: {plan.pages} page(s)")

        self._total_pages = sum(p.pages for p in plans)
        self._emit(ProgressEvent(0, self._total_pages, ""))

        # ---- Phase 2: convert, bounded concurrency ----
        if plans and not self._cancel.is_set():
            with ThreadPoolExecutor(max_workers=max(1, s.workers), thread_name_prefix="pdf") as pool:
                for res in pool.map(self._convert_pdf, plans):
                    results.append(res)

        # ---- Cleanup: don't leave empty folders behind ----
        for plan in plans:
            self._remove_if_empty(plan.out_dir)
        if not single:
            self._remove_if_empty(parent)

        if single:
            return plans[0].out_dir if plans and plans[0].out_dir.exists() else None
        return parent if parent.exists() else None

    def _inspect(self, src: Path, out_dir: Path, prefix: str = "") -> _Plan:
        if not src.is_file():
            raise FileNotFoundError(f"File not found: {src}")
        if src.stat().st_size == 0:
            raise ValueError("File is empty (0 bytes); not a valid PDF")
        with open(src, "rb") as fh:
            if b"%PDF-" not in fh.read(1024):
                raise ValueError("Not a valid PDF (missing %PDF header)")

        info = pdfinfo_from_path(str(src), poppler_path=self.poppler_path, timeout=config.PDFINFO_TIMEOUT_SECONDS)
        pages = int(info.get("Pages", 0))
        if pages < 1:
            raise ValueError("PDF has no pages")
        sizes = parse_page_sizes(info)
        if pages > 1:
            try:  # per-page sizes so mixed-size PDFs and giant pages are handled correctly
                full = pdfinfo_from_path(
                    str(src),
                    poppler_path=self.poppler_path,
                    timeout=config.PDFINFO_TIMEOUT_SECONDS,
                    first_page=1,
                    last_page=pages,
                )
                sizes = parse_page_sizes(full) or sizes
            except Exception:  # noqa: BLE001 - sizes are an optimisation, not a requirement
                log.debug("Per-page size lookup failed for %s", src, exc_info=True)
        return _Plan(src, out_dir, pages, sizes, prefix)

    def _convert_pdf(self, plan: _Plan) -> PdfResult:
        res = PdfResult(source=plan.source, out_dir=plan.out_dir, pages_total=plan.pages)
        t0 = time.perf_counter()
        try:
            if self._cancel.is_set():
                res.status = Status.CANCELLED
                return res

            plan.out_dir.mkdir(parents=True, exist_ok=True)
            self._emit_log("INFO", f"Converting {plan.source.name} ...")
            warned_scaled = False

            for page in range(1, plan.pages + 1):
                if self._cancel.is_set():
                    res.status = Status.CANCELLED
                    break
                try:
                    dpi = effective_dpi(self.settings.dpi, plan.sizes.get(page) or plan.sizes.get(1))
                    if dpi != self.settings.dpi and not warned_scaled:
                        warned_scaled = True
                        self._emit_log(
                            "WARNING",
                            f"{plan.source.name}: very large page(s) rendered at reduced DPI "
                            f"({dpi} instead of {self.settings.dpi}) to stay within image/RAM limits.",
                        )
                    self._render_page(plan, page, dpi)
                    res.pages_ok += 1
                except Exception as exc:  # noqa: BLE001 - one bad page must not stop the PDF
                    reason = friendly_error(exc)
                    res.page_errors.append(f"page {page}: {reason}")
                    log.debug("Page failure %s p%d", plan.source, page, exc_info=True)
                    self._emit_log("ERROR", f"{plan.source.name} page {page}: {reason}")
                    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                        self._abort("Disk is full - conversion stopped.")
                finally:
                    self._tick(plan.source.name)

                if self.settings.throttle_ms and self._cancel.wait(self.settings.throttle_ms / 1000):
                    res.status = Status.CANCELLED
                    break

            if res.status is not Status.CANCELLED:
                if res.pages_ok == plan.pages:
                    res.status = Status.SUCCESS
                elif res.pages_ok > 0:
                    res.status = Status.PARTIAL
                    res.error = f"{plan.pages - res.pages_ok} of {plan.pages} page(s) failed"
                else:
                    res.status = Status.FAILED
                    res.error = res.page_errors[0] if res.page_errors else "No pages converted"
            elif self._fatal:
                res.status = Status.FAILED
                res.error = self._fatal
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected error converting %s", plan.source)
            res.status = Status.FAILED
            res.error = friendly_error(exc)
        finally:
            res.seconds = time.perf_counter() - t0
            self._finish_pdf(res)
        return res

    def _finish_pdf(self, res: PdfResult) -> None:
        if res.status is Status.SUCCESS:
            self._emit_log("SUCCESS", f"{res.source.name}: {res.pages_ok} page(s) saved ({res.seconds:.1f}s)")
        elif res.status is Status.PARTIAL:
            self._emit_log("WARNING", f"{res.source.name}: {res.error}")
        elif res.status is Status.FAILED:
            self._emit_log("ERROR", f"{res.source.name}: FAILED - {res.error}")
        elif res.status is Status.CANCELLED:
            self._emit_log("WARNING", f"{res.source.name}: cancelled after {res.pages_ok} page(s)")
        self._emit(PdfDoneEvent(res))

    def _render_page(self, plan: _Plan, page: int, dpi: int) -> None:
        """Render exactly one page, save it atomically, and release its memory."""
        final = plan.out_dir / plan.file_name(page)
        part = plan.out_dir / f".{plan.file_name(page)}.part"
        images: list[Image.Image] = []
        img: Optional[Image.Image] = None
        converted: Optional[Image.Image] = None
        try:
            images = convert_from_path(
                str(plan.source),
                dpi=dpi,
                first_page=page,
                last_page=page,
                fmt="ppm",
                thread_count=1,
                poppler_path=self.poppler_path,
                timeout=self.settings.page_timeout,
            )
            if not images:
                raise RuntimeError("Poppler returned no image for this page")
            img = images[0]
            if img.mode != "RGB":
                converted = img.convert("RGB")
                img = converted
            img.save(
                part,
                format="JPEG",
                quality=self.settings.jpeg_quality,
                subsampling=0,       # 4:4:4 - keeps fine coloured text sharp
                optimize=True,
                dpi=(dpi, dpi),
            )
            os.replace(part, final)
        finally:
            for im in (converted, *images):
                if im is not None:
                    try:
                        im.close()
                    except Exception:  # noqa: BLE001
                        pass
            del img, converted, images
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            gc.collect()

    def _tick(self, name: str) -> None:
        with self._lock:
            self._done_pages += 1
            done, total = self._done_pages, self._total_pages
        self._emit(ProgressEvent(done, total, name))

    def _abort(self, reason: str) -> None:
        with self._lock:
            first = not self._fatal
            self._fatal = self._fatal or reason
        self._cancel.set()
        if first:
            self._emit_log("ERROR", reason)

    @staticmethod
    def _remove_if_empty(directory: Path) -> None:
        try:
            directory.rmdir()   # only succeeds when empty
        except OSError:
            pass
