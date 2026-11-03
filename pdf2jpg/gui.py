"""CustomTkinter front-end.

Thread model: the conversion runs in worker threads that only ever call
``queue.Queue.put``. The Tk main thread drains that queue in ``_poll`` -- no widget is
ever touched from a worker thread.
"""

from __future__ import annotations

import queue
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from . import config
from .engine import (
    ConversionJob,
    ConversionSettings,
    Event,
    FinishedEvent,
    LogEvent,
    PdfDoneEvent,
    PdfResult,
    ProgressEvent,
    Status,
    BatchSummary,
)
from .logger import get_logger
from .paths import default_output_dir, expand_inputs, find_poppler, open_folder, path_key

log = get_logger()

# Optional drag & drop support (pip install tkinterdnd2). The app works without it.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _DND_IMPORTED = True
    _BASES: tuple[type, ...] = (ctk.CTk, TkinterDnD.DnDWrapper)
except Exception:  # noqa: BLE001
    DND_FILES = None  # type: ignore[assignment]
    TkinterDnD = None  # type: ignore[assignment]
    _DND_IMPORTED = False
    _BASES = (ctk.CTk,)

LEVEL_COLORS = {
    "INFO": "#dce4ee",
    "SUCCESS": "#7ee787",
    "WARNING": "#e5c07b",
    "ERROR": "#ff6b6b",
}
LIST_BG, LIST_FG, LIST_SEL = "#2b2b2b", "#dce4ee", "#1f6aa5"


class App(*_BASES):  # type: ignore[misc]
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()

        self._dnd_ok = False
        if _DND_IMPORTED:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self._dnd_ok = True
            except Exception:  # noqa: BLE001
                log.warning("Drag & drop unavailable", exc_info=True)

        self.title(f"{config.APP_NAME}  v{config.APP_VERSION}")
        self.geometry("920x820")
        self.minsize(820, 720)

        self._files: list[Path] = []
        self._keys: set[str] = set()
        self._events: "queue.Queue[Event]" = queue.Queue()
        self._job: Optional[ConversionJob] = None
        self._last_output: Optional[Path] = None
        self._toasts: list[ctk.CTkToplevel] = []
        self._toasts_this_run = 0
        self._poppler = find_poppler()

        self._build_ui()
        self._refresh_list_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll_id = self.after(config.POLL_INTERVAL_MS, self._poll)

        if not self._poppler.ok:
            self._log("ERROR", self._poppler.message)
            self.after(300, lambda: messagebox.showerror("Poppler missing", self._poppler.message, parent=self))
        else:
            self._log("INFO", self._poppler.message)
        if not self._dnd_ok:
            self._log("INFO", "Drag & drop not available (tkinterdnd2 missing) - use the Add buttons.")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)
        px = 16

        # Header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=px, pady=(14, 4))
        ctk.CTkLabel(head, text=config.APP_NAME, font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text=f"Convert up to {config.MAX_BATCH_FILES} PDFs to JPG - one image per page.",
            text_color="gray70",
        ).pack(anchor="w")

        # Drop zone
        self.drop_zone = ctk.CTkFrame(self, border_width=2, border_color="#3b8ed0", corner_radius=10)
        self.drop_zone.grid(row=1, column=0, sticky="ew", padx=px, pady=6)
        self.drop_zone.grid_columnconfigure((0, 1, 2, 3), weight=1)
        msg = (
            "Drag & drop PDF files or folders here"
            if self._dnd_ok
            else "Add PDF files or a folder of PDFs"
        )
        ctk.CTkLabel(self.drop_zone, text=msg, font=ctk.CTkFont(size=15)).grid(
            row=0, column=0, columnspan=4, pady=(14, 8)
        )
        self.btn_add = ctk.CTkButton(self.drop_zone, text="Add PDFs...", command=self._add_files_dialog)
        self.btn_folder = ctk.CTkButton(self.drop_zone, text="Add Folder...", command=self._add_folder_dialog)
        self.btn_remove = ctk.CTkButton(
            self.drop_zone, text="Remove Selected", fg_color="gray35", hover_color="gray25", command=self._remove_selected
        )
        self.btn_clear = ctk.CTkButton(
            self.drop_zone, text="Clear All", fg_color="gray35", hover_color="gray25", command=self._clear_files
        )
        for col, b in enumerate((self.btn_add, self.btn_folder, self.btn_remove, self.btn_clear)):
            b.grid(row=1, column=col, padx=8, pady=(0, 14), sticky="ew")

        # File list
        lst = ctk.CTkFrame(self)
        lst.grid(row=2, column=0, sticky="ew", padx=px, pady=6)
        lst.grid_columnconfigure(0, weight=1)
        self.lbl_count = ctk.CTkLabel(lst, text="", anchor="w")
        self.lbl_count.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 2))
        self.listbox = tk.Listbox(
            lst,
            height=6,
            selectmode=tk.EXTENDED,
            bg=LIST_BG,
            fg=LIST_FG,
            selectbackground=LIST_SEL,
            selectforeground="white",
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
            exportselection=False,
        )
        self.listbox.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 10))
        sb = ctk.CTkScrollbar(lst, command=self.listbox.yview, height=10)  # height=10: don't force a tall row
        sb.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(0, 10))
        self.listbox.configure(yscrollcommand=sb.set)

        # Options
        opt = ctk.CTkFrame(self)
        opt.grid(row=3, column=0, sticky="ew", padx=px, pady=6)
        opt.grid_columnconfigure(5, weight=1)

        ctk.CTkLabel(opt, text="Quality").grid(row=0, column=0, padx=(12, 6), pady=(12, 4), sticky="w")
        self.quality_var = tk.StringVar(value=config.DEFAULT_PRESET.label)
        self.menu_quality = ctk.CTkOptionMenu(
            opt,
            values=[p.label for p in config.QUALITY_PRESETS],
            variable=self.quality_var,
            command=lambda _v: self._update_hint(),
            width=190,
        )
        self.menu_quality.grid(row=0, column=1, padx=6, pady=(12, 4))

        ctk.CTkLabel(opt, text="Threads").grid(row=0, column=2, padx=(18, 6), pady=(12, 4), sticky="w")
        self.workers_var = tk.StringVar(value="Auto")
        self.menu_workers = ctk.CTkOptionMenu(opt, values=list(config.WORKER_CHOICES), variable=self.workers_var, width=90)
        self.menu_workers.grid(row=0, column=3, padx=6, pady=(12, 4))

        self.lbl_hint = ctk.CTkLabel(opt, text="", text_color="gray70", anchor="w")
        self.lbl_hint.grid(row=1, column=0, columnspan=6, sticky="ew", padx=12, pady=(0, 6))

        ctk.CTkLabel(opt, text="Output folder").grid(row=2, column=0, padx=(12, 6), pady=(0, 6), sticky="w")
        self.out_var = tk.StringVar(value=str(default_output_dir()))
        self.entry_out = ctk.CTkEntry(opt, textvariable=self.out_var)
        self.entry_out.grid(row=2, column=1, columnspan=4, sticky="ew", padx=6, pady=(0, 6))
        self.btn_browse = ctk.CTkButton(opt, text="Browse...", width=100, command=self._browse_output)
        self.btn_browse.grid(row=2, column=5, padx=(6, 12), pady=(0, 6), sticky="e")
        self.flat_var = tk.BooleanVar(value=False)
        self.chk_flat = ctk.CTkCheckBox(
            opt,
            text="Put all images in ONE folder when converting several PDFs (files named PDFname_page_1.jpg)",
            variable=self.flat_var,
        )
        self.chk_flat.grid(row=3, column=0, columnspan=6, sticky="w", padx=12, pady=(2, 12))
        self._update_hint()

        # Actions
        act = ctk.CTkFrame(self, fg_color="transparent")
        act.grid(row=4, column=0, sticky="ew", padx=px, pady=6)
        act.grid_columnconfigure((0, 1, 2), weight=1, uniform="act")
        self.btn_convert = ctk.CTkButton(
            act, text="Convert", height=42, font=ctk.CTkFont(size=15, weight="bold"), command=self._start
        )
        self.btn_convert.grid(row=0, column=0, padx=(0, 6), sticky="ew")
        self.btn_cancel = ctk.CTkButton(
            act, text="Cancel", height=42, fg_color="#a33", hover_color="#822", state="disabled", command=self._cancel
        )
        self.btn_cancel.grid(row=0, column=1, padx=6, sticky="ew")
        self.btn_open = ctk.CTkButton(
            act, text="Open Output Folder", height=42, fg_color="gray35", hover_color="gray25", command=self._open_output
        )
        self.btn_open.grid(row=0, column=2, padx=(6, 0), sticky="ew")

        # Progress
        prog = ctk.CTkFrame(self, fg_color="transparent")
        prog.grid(row=5, column=0, sticky="ew", padx=px, pady=(6, 2))
        prog.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(prog)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.lbl_progress = ctk.CTkLabel(prog, text="Ready.", anchor="w")
        self.lbl_progress.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        # Log
        self.log_box = ctk.CTkTextbox(self, wrap="word", state="disabled", font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.grid(row=6, column=0, sticky="nsew", padx=px, pady=(6, 14))
        for level, color in LEVEL_COLORS.items():
            self.log_box.tag_config(level, foreground=color)

        # Drag & drop wiring (root + drop zone; Tk walks up to the nearest registered ancestor)
        if self._dnd_ok:
            for widget in (self, self.drop_zone):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:  # noqa: BLE001
                    log.warning("Could not register drop target", exc_info=True)

    # ------------------------------------------------------------ helpers
    @property
    def _running(self) -> bool:
        return self._job is not None

    def _update_hint(self) -> None:
        self.lbl_hint.configure(text=config.PRESETS_BY_LABEL[self.quality_var.get()].hint)

    def _log(self, level: str, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {message}\n", level)
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > config.LOG_MAX_LINES:
            self.log_box.delete("1.0", f"{lines - config.LOG_MAX_LINES}.0")
        self.log_box.configure(state="disabled")
        self.log_box.see("end")

    def _refresh_list_state(self) -> None:
        n = len(self._files)
        mode = "single-PDF mode: one folder" if n == 1 else "batch mode: parent folder + one subfolder per PDF"
        self.lbl_count.configure(text=f"{n} / {config.MAX_BATCH_FILES} PDF file(s) selected" + (f"   ({mode})" if n else ""))

    def _set_running(self, running: bool) -> None:
        idle = "disabled" if running else "normal"
        for w in (
            self.btn_add, self.btn_folder, self.btn_remove, self.btn_clear, self.btn_convert,
            self.btn_browse, self.menu_quality, self.menu_workers, self.entry_out, self.chk_flat,
        ):
            w.configure(state=idle)
        self.btn_cancel.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------- file selection
    def _add_paths(self, raw_paths) -> None:
        if self._running:
            self._log("WARNING", "Conversion in progress - cannot change the file list now.")
            return
        pdfs, ignored = expand_inputs(raw_paths)
        added = dup = skipped = 0
        for p in pdfs:
            key = path_key(p)
            if key in self._keys:
                dup += 1
                continue
            if len(self._files) >= config.MAX_BATCH_FILES:
                skipped += 1
                continue
            self._files.append(p)
            self._keys.add(key)
            self.listbox.insert("end", str(p))
            added += 1
        self._refresh_list_state()
        if added:
            self._log("INFO", f"Added {added} PDF(s).")
        if dup:
            self._log("WARNING", f"{dup} duplicate(s) skipped.")
        if ignored:
            self._log("WARNING", f"{ignored} item(s) ignored (not a PDF or unreadable).")
        if skipped:
            self._log("WARNING", f"Limit of {config.MAX_BATCH_FILES} PDFs reached - {skipped} file(s) not added.")
            messagebox.showwarning(
                "Limit reached",
                f"A maximum of {config.MAX_BATCH_FILES} PDFs can be converted at once.\n"
                f"{skipped} file(s) were not added.",
                parent=self,
            )

    def _on_drop(self, event) -> str:
        try:
            self._add_paths(self.tk.splitlist(event.data))
        except Exception:  # noqa: BLE001
            log.exception("Drop handling failed")
            self._log("ERROR", "Could not read the dropped items.")
        return "copy"

    def _add_files_dialog(self) -> None:
        paths = filedialog.askopenfilenames(parent=self, title="Select PDF files", filetypes=[("PDF files", "*.pdf *.PDF")])
        if paths:
            self._add_paths(paths)

    def _add_folder_dialog(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Select a folder containing PDFs")
        if folder:
            before = len(self._files)
            self._add_paths([folder])
            if len(self._files) == before:
                self._log("WARNING", "No new PDFs found in that folder.")

    def _remove_selected(self) -> None:
        if self._running:
            return
        for idx in reversed(self.listbox.curselection()):
            path = self._files.pop(idx)
            self._keys.discard(path_key(path))
            self.listbox.delete(idx)
        self._refresh_list_state()

    def _clear_files(self) -> None:
        if self._running:
            return
        self._files.clear()
        self._keys.clear()
        self.listbox.delete(0, "end")
        self._refresh_list_state()

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Choose output folder", initialdir=self.out_var.get() or None)
        if folder:
            self.out_var.set(folder)

    # ------------------------------------------------------------ run control
    def _start(self) -> None:
        if self._running:
            return
        if not self._files:
            messagebox.showinfo("Nothing to convert", "Add at least one PDF first.", parent=self)
            return
        if not self._poppler.ok:
            messagebox.showerror("Poppler missing", self._poppler.message, parent=self)
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showwarning("Output folder", "Choose an output folder first.", parent=self)
            return

        preset = config.PRESETS_BY_LABEL[self.quality_var.get()]
        settings = ConversionSettings(
            dpi=preset.dpi,
            jpeg_quality=preset.jpeg_quality,
            workers=config.resolve_workers(self.workers_var.get(), preset.dpi),
            throttle_ms=0,
            flat_output=bool(self.flat_var.get()),
        )
        try:
            job = ConversionJob(list(self._files), Path(out), settings, self._events.put, self._poppler.path)
        except ValueError as exc:
            messagebox.showwarning("Cannot start", str(exc), parent=self)
            return

        self._job = job
        self._toasts_this_run = 0
        self._last_output = None
        self.progress.set(0)
        self.lbl_progress.configure(text="Preparing...")
        self._set_running(True)
        job.start()

    def _cancel(self) -> None:
        if self._job:
            self._job.cancel()
            self.btn_cancel.configure(state="disabled")
            self.lbl_progress.configure(text="Cancelling after the current page(s) ...")
            self._log("WARNING", "Cancel requested - finishing the pages in progress.")

    def _open_output(self) -> None:
        target = self._last_output
        if not target or not target.exists():
            target = Path(self.out_var.get().strip() or default_output_dir()).expanduser()
        if not target.exists():
            messagebox.showinfo("Output folder", "The output folder does not exist yet. Convert something first.", parent=self)
            return
        try:
            open_folder(target)
        except Exception as exc:  # noqa: BLE001
            log.exception("open_folder failed")
            messagebox.showerror("Open folder", f"Could not open the folder:\n{exc}", parent=self)

    # ----------------------------------------------------------- event pump
    def _poll(self) -> None:
        latest: Optional[ProgressEvent] = None
        try:
            for _ in range(1000):   # bounded so the UI never starves
                try:
                    ev = self._events.get_nowait()
                except queue.Empty:
                    break
                if isinstance(ev, LogEvent):
                    self._log(ev.level, ev.message)
                elif isinstance(ev, ProgressEvent):
                    latest = ev
                elif isinstance(ev, PdfDoneEvent):
                    self._on_pdf_done(ev.result)
                elif isinstance(ev, FinishedEvent):
                    if latest:
                        self._show_progress(latest)
                        latest = None
                    self._on_finished(ev.summary)
            if latest:
                self._show_progress(latest)
        except Exception:  # noqa: BLE001 - the pump must survive any single bad event
            log.exception("Error while handling job events")
        self._poll_id = self.after(config.POLL_INTERVAL_MS, self._poll)

    def _show_progress(self, ev: ProgressEvent) -> None:
        if ev.total <= 0:
            return
        frac = min(1.0, ev.done / ev.total)
        self.progress.set(frac)
        tail = f"  |  {ev.current}" if ev.current else ""
        self.lbl_progress.configure(text=f"{frac * 100:.0f}%  |  {ev.done}/{ev.total} pages{tail}")

    def _on_pdf_done(self, res: PdfResult) -> None:
        if res.status in (Status.FAILED, Status.PARTIAL):
            self._toast(f"Failed: {res.source.name}" if res.status is Status.FAILED else f"Partial: {res.source.name}", res.error)

    def _on_finished(self, summary: BatchSummary) -> None:
        self._job = None
        self._set_running(False)
        self._last_output = summary.output_dir
        total_known = bool(summary.results)
        if not summary.cancelled and not summary.fatal_error and total_known:
            self.progress.set(1.0)

        if summary.fatal_error:
            status = f"Stopped: {summary.fatal_error}"
        elif summary.cancelled:
            status = "Cancelled."
        elif summary.problems:
            status = "Finished with errors."
        else:
            status = "Completed successfully."
        self.lbl_progress.configure(text=f"{status}  {summary.headline()}")
        self._log("SUCCESS" if not (summary.problems or summary.fatal_error) else "WARNING", f"{status} {summary.headline()}")
        if summary.output_dir:
            self._log("INFO", f"Output: {summary.output_dir}")

        if summary.problems or summary.fatal_error:
            lines = [f"- {r.source.name}: {r.error}" for r in summary.problems]
            if summary.fatal_error:
                lines.insert(0, f"- {summary.fatal_error}")
            shown = lines[:15]
            if len(lines) > len(shown):
                shown.append(f"... and {len(lines) - len(shown)} more (see the log).")
            messagebox.showwarning(
                "Conversion finished with problems",
                "These items could not be fully converted. The rest of the queue was processed normally.\n\n"
                + "\n".join(shown),
                parent=self,
            )

    # --------------------------------------------------------------- toasts
    def _toast(self, title: str, text: str) -> None:
        """Non-blocking alert so failures are visible without pausing the queue."""
        if self._toasts_this_run >= config.MAX_TOASTS_PER_RUN:
            return
        self._toasts_this_run += 1
        try:
            w, h, gap = 380, 86, 10
            self.update_idletasks()
            x = self.winfo_rootx() + self.winfo_width() - w - 24
            y = self.winfo_rooty() + self.winfo_height() - (h + gap) * (len(self._toasts) + 1) - 24
            top = ctk.CTkToplevel(self)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            top.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
            frame = ctk.CTkFrame(top, border_width=2, border_color="#ff6b6b", corner_radius=8)
            frame.pack(fill="both", expand=True)
            ctk.CTkLabel(frame, text=title, text_color="#ff6b6b", font=ctk.CTkFont(weight="bold"), anchor="w").pack(
                fill="x", padx=10, pady=(8, 0)
            )
            ctk.CTkLabel(frame, text=text[:160], anchor="w", justify="left", wraplength=w - 30).pack(
                fill="x", padx=10, pady=(2, 8)
            )
            self._toasts.append(top)

            def close(_e=None, t=top) -> None:
                if t in self._toasts:
                    self._toasts.remove(t)
                try:
                    t.destroy()
                except Exception:  # noqa: BLE001
                    pass

            top.bind("<Button-1>", close)
            frame.bind("<Button-1>", close)
            top.after(8000, close)
        except Exception:  # noqa: BLE001 - an alert must never break the run
            log.exception("Could not show toast")

    # ---------------------------------------------------------------- close
    def _on_close(self) -> None:
        if self._running:
            if not messagebox.askyesno("Quit", "A conversion is still running. Cancel it and quit?", parent=self):
                return
            if self._job:
                self._job.cancel()
        try:
            self.after_cancel(self._poll_id)
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


def run_gui() -> None:
    App().mainloop()
