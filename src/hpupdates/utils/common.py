"""Shared utilities for hpupdates."""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("hpupdates")


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == "win32"


def is_wsl() -> bool:
    """Check if running inside WSL."""
    return sys.platform == "linux" and Path("/mnt/c/Windows").exists()


def sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def truncate(s: str, max_len: int = 60) -> str:
    """Truncate a string with ellipsis if too long."""
    return s[:max_len - 1] + "…" if len(s) > max_len else s


def safe_int(value: Any, default: int = 0) -> int:
    """Safely convert to int, returning default on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default
