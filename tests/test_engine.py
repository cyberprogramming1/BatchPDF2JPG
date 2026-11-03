"""Engine tests. Needs Poppler on PATH (or POPPLER_PATH) and: pip install pytest pypdf pillow."""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter

from pdf2jpg import config
from pdf2jpg.engine import (
    ConversionJob,
    ConversionSettings,
    FinishedEvent,
    LogEvent,
    PdfDoneEvent,
    ProgressEvent,
    Status,
    effective_dpi,
    validate_inputs,
)
from pdf2jpg.paths import find_poppler, sanitize_folder_name, unique_name

pytestmark = pytest.mark.skipif(not find_poppler().ok, reason="Poppler not installed")


def make_pdf(path: Path, pages: int = 3) -> Path:
    imgs = []
    for i in range(pages):
        im = Image.new("RGB", (595, 842), "white")   # A4 @ 72 dpi
        ImageDraw.Draw(im).text((50, 50), f"{path.stem} page {i + 1}", fill="black")
        imgs.append(im)
    imgs[0].save(path, "PDF", save_all=True, append_images=imgs[1:], resolution=72)
    return path


def run_job(pdfs, out, dpi=150, workers=2, throttle_ms=0):
    events = []
    lock = threading.Lock()

    def sink(ev):
        with lock:
            events.append(ev)

    settings = ConversionSettings(dpi=dpi, jpeg_quality=90, workers=workers, throttle_ms=throttle_ms)
    job = ConversionJob(pdfs, out, settings, sink)
    summary = job.run()
    return summary, events


# --------------------------------------------------------------------------
def test_single_pdf_layout_and_dpi(tmp_path):
    pdf = make_pdf(tmp_path / "My Report.pdf", pages=3)
    out = tmp_path / "out"
    summary, events = run_job([pdf], out, dpi=150)

    folder = out / "My Report"
    assert summary.output_dir == folder
    assert sorted(p.name for p in folder.iterdir()) == ["page_1.jpg", "page_2.jpg", "page_3.jpg"]
    with Image.open(folder / "page_1.jpg") as im:
        assert im.format == "JPEG"
        assert im.size == (1240, 1754) or abs(im.size[0] - 1240) <= 2   # A4 @ 150 dpi
        assert round(im.info["dpi"][0]) == 150
    assert summary.count(Status.SUCCESS) == 1
    assert not list(folder.glob("*.part"))
    assert sum(isinstance(e, FinishedEvent) for e in events) == 1


@pytest.mark.parametrize("dpi,expected_w", [(150, 1240), (300, 2480)])
def test_dpi_scales_output(tmp_path, dpi, expected_w):
    pdf = make_pdf(tmp_path / "a.pdf", pages=1)
    run_job([pdf], tmp_path / "o", dpi=dpi)
    with Image.open(tmp_path / "o" / "a" / "page_1.jpg") as im:
        assert abs(im.size[0] - expected_w) <= 2


def test_batch_layout_with_failures_does_not_stop_queue(tmp_path):
    good1 = make_pdf(tmp_path / "one.pdf", 2)
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4\nthis is not really a pdf\n")
    fake = tmp_path / "fake.pdf"
    fake.write_text("hello")
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    good2 = make_pdf(tmp_path / "two.pdf", 4)

    out = tmp_path / "out"
    summary, events = run_job([good1, bad, fake, empty, good2], out, workers=3)

    batch_roots = [p for p in out.iterdir() if p.name.startswith("PDF_Batch_")]
    assert len(batch_roots) == 1 and summary.output_dir == batch_roots[0]
    subs = sorted(p.name for p in batch_roots[0].iterdir())
    assert subs == ["one", "two"]                       # failed PDFs leave no empty folders
    assert len(list((batch_roots[0] / "two").glob("page_*.jpg"))) == 4

    by_name = {r.source.name: r for r in summary.results}
    assert by_name["one.pdf"].status is Status.SUCCESS
    assert by_name["two.pdf"].status is Status.SUCCESS
    for name in ("broken.pdf", "fake.pdf", "empty.pdf"):
        assert by_name[name].status is Status.FAILED and by_name[name].error
    assert len(summary.problems) == 3
    done_events = [e for e in events if isinstance(e, PdfDoneEvent)]
    assert len(done_events) == 5                          # every PDF reports exactly once


def test_progress_is_monotonic_and_complete(tmp_path):
    pdfs = [make_pdf(tmp_path / f"p{i}.pdf", 3) for i in range(3)]
    summary, events = run_job(pdfs, tmp_path / "o", workers=3)
    prog = [e for e in events if isinstance(e, ProgressEvent)]
    assert prog[0].done == 0 and prog[0].total == 9
    assert prog[-1].done == 9
    assert sorted(e.done for e in prog) == [e.done for e in sorted(prog, key=lambda e: e.done)]
    assert len({e.done for e in prog}) == len(prog)       # no lost / duplicated increments


def test_duplicate_stems_get_unique_folders(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    p1 = make_pdf(tmp_path / "a" / "scan.pdf", 1)
    p2 = make_pdf(tmp_path / "b" / "scan.pdf", 1)
    summary, _ = run_job([p1, p2], tmp_path / "o")
    names = sorted(p.name for p in summary.output_dir.iterdir())
    assert names == ["scan", "scan_2"]


def test_encrypted_pdf_reports_clear_error(tmp_path):
    src = make_pdf(tmp_path / "plain.pdf", 1)
    w = PdfWriter(clone_from=PdfReader(str(src)))
    w.encrypt(user_password="secret", owner_password="owner")
    locked = tmp_path / "locked.pdf"
    with open(locked, "wb") as fh:
        w.write(fh)
    summary, _ = run_job([locked], tmp_path / "o")
    r = summary.results[0]
    assert r.status is Status.FAILED
    assert "password" in r.error.lower()


def test_cancel_stops_early_and_leaves_no_partial_files(tmp_path):
    pdf = make_pdf(tmp_path / "long.pdf", pages=40)
    out = tmp_path / "o"
    events: "queue.Queue" = queue.Queue()
    job = ConversionJob([pdf], out, ConversionSettings(150, 90, 1, throttle_ms=30), events.put)

    def canceller():
        while True:
            ev = events.get()
            if isinstance(ev, ProgressEvent) and ev.done >= 3:
                job.cancel()
                return

    t = threading.Thread(target=canceller)
    t.start()
    summary = job.run()
    t.join(timeout=5)
    assert summary.cancelled
    assert summary.results[0].status is Status.CANCELLED
    n = len(list((out / "long").glob("page_*.jpg")))
    assert 3 <= n < 40
    assert not list((out / "long").glob("*.part"))


def test_giant_page_dpi_is_reduced_not_crashed(tmp_path):
    w = PdfWriter()
    w.add_blank_page(width=14400, height=14400)           # 200 x 200 inch page
    big = tmp_path / "poster.pdf"
    with open(big, "wb") as fh:
        w.write(fh)
    summary, _ = run_job([big], tmp_path / "o", dpi=300)
    assert summary.results[0].status is Status.SUCCESS
    with Image.open(tmp_path / "o" / "poster" / "page_1.jpg") as im:
        assert max(im.size) <= config.JPEG_MAX_DIMENSION
        assert im.size[0] * im.size[1] <= config.MAX_PAGE_PIXELS


def test_unicode_and_spaces_in_names(tmp_path):
    pdf = make_pdf(tmp_path / "Rapor ƏĞ şəkil #1.pdf", 1)
    summary, _ = run_job([pdf], tmp_path / "çıxış")
    assert (tmp_path / "çıxış" / "Rapor ƏĞ şəkil #1" / "page_1.jpg").is_file()


# --------------------------------------------------------------------------
def test_limit_of_50():
    paths = [Path(f"/x/{i}.pdf") for i in range(51)]
    with pytest.raises(ValueError):
        validate_inputs(paths)
    assert len(validate_inputs(paths[:50])) == 50
    with pytest.raises(ValueError):
        validate_inputs([])


def test_validate_inputs_dedupes():
    assert len(validate_inputs([Path("/x/a.pdf"), Path("/x/a.pdf")])) == 1


def test_effective_dpi_rules():
    a4 = (595.0, 842.0)
    assert effective_dpi(600, a4) == 600
    assert effective_dpi(300, None) == 300
    assert effective_dpi(300, (14400.0, 14400.0)) < 300


def test_name_helpers():
    assert sanitize_folder_name('a<b>:"c"') == "a_b___c_"
    assert sanitize_folder_name("CON") == "_CON"
    assert sanitize_folder_name("   ") == "pdf"
    used: set[str] = set()
    assert [unique_name("x", used) for _ in range(3)] == ["x", "x_2", "x_3"]
    assert unique_name("X", used) == "X_4"                 # case-insensitive (Windows)


def test_no_event_for_log_is_missing(tmp_path):
    pdf = make_pdf(tmp_path / "z.pdf", 1)
    _, events = run_job([pdf], tmp_path / "o")
    assert any(isinstance(e, LogEvent) and e.level == "SUCCESS" for e in events)
