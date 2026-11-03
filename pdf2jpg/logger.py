"""Application logging: rotating file + console (when a console exists)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .paths import log_dir

LOGGER_NAME = "pdf2jpg"
_configured = False


def setup_logging() -> logging.Logger:
    """Configure logging once. Never raises: a read-only profile must not stop the app."""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s: %(message)s")

    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(directory / "pdf2jpg.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:  # noqa: BLE001 - logging must never block start-up
        pass

    # A PyInstaller --windowed build has sys.stderr = None.
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger.propagate = False
    _configured = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
