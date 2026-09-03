"""Pytest configuration for hp-driverctl tests."""

import sys
from pathlib import Path

# Ensure src is on the path even without the package installed
src = Path(__file__).resolve().parent.parent / "src"
if str(src) not in sys.path:
    sys.path.insert(0, str(src))
