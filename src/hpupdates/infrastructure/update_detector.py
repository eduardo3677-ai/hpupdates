"""Update detector — mirrors HP.SUDFClient.Detector.UpdateDetector.

Local validation of SUDF updates: device matching, file version comparison,
BIOS date checking, store package verification.
"""
from __future__ import annotations


from hpupdates.infrastructure._detector import (
    InstallStatus,
    SUDFUpdate,
    DotNetVersion,
    UpdateDetector,
    compare_versions,
    resolve_path,
)

__all__ = [
    "InstallStatus",
    "SUDFUpdate",
    "DotNetVersion",
    "UpdateDetector",
    "compare_versions",
    "resolve_path",
]
