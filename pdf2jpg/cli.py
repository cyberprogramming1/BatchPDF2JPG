"""Headless command-line front-end (handy for scripts / scheduled jobs).

Usage:  python main.py --cli report.pdf scans_folder -o out --dpi 300
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from . import config
from .engine import ConversionJob, ConversionSettings, Event, FinishedEvent, LogEvent, ProgressEvent
from .paths import default_output_dir, expand_inputs, find_poppler


def run_cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pdf2jpg --cli", description="Convert PDFs to JPG images.")
    ap.add_argument("inputs", nargs="+", help="PDF files and/or folders containing PDFs")
    ap.add_argument("-o", "--output", default=str(default_output_dir()), help="output root folder")
    ap.add_argument("--dpi", type=int, default=config.DEFAULT_PRESET.dpi, help="150, 300 or 600 (any value allowed)")
    ap.add_argument("--quality", type=int, default=None, help="JPEG quality 1-100 (default depends on DPI)")
    ap.add_argument("--workers", default="Auto", help="Auto, 1, 2, 3 or 4")
    ap.add_argument("--throttle-ms", type=int, default=0, help="pause after each page")
    ap.add_argument("--flat", action="store_true", help="put all images in one folder (files named <pdf>_page_N.jpg)")
    args = ap.parse_args(argv)

    poppler = find_poppler()
    if not poppler.ok:
        print(poppler.message)
        return 2

    pdfs, ignored = expand_inputs(args.inputs)
    if ignored:
        print(f"Ignored {ignored} non-PDF input(s).")
    if not pdfs:
        print("No PDF files found.")
        return 2

    preset = next((p for p in config.QUALITY_PRESETS if p.dpi == args.dpi), None)
    quality = args.quality or (preset.jpeg_quality if preset else 95)
    settings = ConversionSettings(
        dpi=max(36, args.dpi),
        jpeg_quality=max(1, min(100, quality)),
        workers=config.resolve_workers(args.workers, args.dpi),
        throttle_ms=max(0, args.throttle_ms),
        flat_output=args.flat,
    )

    print_lock = threading.Lock()
    finished: list[FinishedEvent] = []

    def on_event(ev: Event) -> None:
        with print_lock:
            if isinstance(ev, LogEvent):
                print(f"[{ev.level}] {ev.message}")
            elif isinstance(ev, ProgressEvent) and ev.total and (ev.done == ev.total or ev.done % 10 == 0):
                print(f"  progress: {ev.done}/{ev.total} pages")
            elif isinstance(ev, FinishedEvent):
                finished.append(ev)

    try:
        job = ConversionJob(pdfs, Path(args.output), settings, on_event, poppler.path)
    except ValueError as exc:
        print(exc)
        return 2

    try:
        summary = job.run()
    except KeyboardInterrupt:
        job.cancel()
        print("Interrupted.")
        return 130

    print(summary.headline())
    if summary.output_dir:
        print(f"Output: {summary.output_dir}")
    return 1 if summary.problems or summary.fatal_error else 0
