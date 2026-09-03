"""Catalog helpers shared by other CLI modules.

All @app.command() definitions were removed during the CLI refactor —
the 8 essential commands live in sudf_cmds.py and web_cmds.py.
The helper functions below remain for infrastructure use.
"""

from __future__ import annotations


from rich.console import Console

from hpupdates.infrastructure.os_params import os_version_name

console = Console()


def _os_friendly_name(os_version: str, os_build: str) -> str:
    """Convert raw OS version string (e.g. '10.0.26200') to friendly name ('Windows 11').

    Mirrors OSInformation.OSVersionName — uses major.minor.build to determine
    whether this is Windows 10 or Windows 11 (build >= 22000).
    """
    parts = os_version.split(".")
    major = int(parts[0]) if parts and parts[0].isdigit() else 0
    minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    build = int(os_build) if os_build.isdigit() else (
        int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    )
    return os_version_name(major, minor, build)


def _sudf_category(update_type: str) -> str:
    t = update_type.lower()
    if "bios" in t or "firmware" in t:
        return "firmware"
    if "driver" in t:
        return "driver"
    return "software"
