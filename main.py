"""Entry point.  GUI:  python main.py      CLI:  python main.py --cli <inputs> -o <out>"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()   # harmless; protects frozen builds from re-spawn loops

    from pdf2jpg.logger import setup_logging

    log = setup_logging()
    argv = sys.argv[1:]

    if argv and argv[0] == "--cli":
        from pdf2jpg.cli import run_cli

        return run_cli(argv[1:])

    try:
        from pdf2jpg.gui import run_gui

        run_gui()
        return 0
    except Exception as exc:  # noqa: BLE001 - show *something* if the GUI cannot even start
        log.exception("GUI crashed")
        try:
            import tkinter
            from tkinter import messagebox

            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror("PDF to JPG Converter", f"The application hit a fatal error:\n\n{exc}")
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
