"""Shared CLI helpers — builders for SUDF client and Windows backend."""

from __future__ import annotations

from hpupdates.infrastructure.windows.backend import WindowsDriverBackend


def _build_sudf_client() -> SudfClient:  # type: ignore[name-defined]  # noqa: F821
    """Build a SUDF client with default credentials."""
    from hpupdates.infrastructure.sudf import SudfClient

    return SudfClient()


def _build_windows_backend() -> WindowsDriverBackend:
    """Build a WindowsDriverBackend (fails gracefully on non-Windows)."""
    from hpupdates.infrastructure.windows.backend import CommandRunner

    return WindowsDriverBackend(CommandRunner())
