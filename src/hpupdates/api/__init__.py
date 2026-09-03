"""Public API for hpupdates.

Import from here for programmatic usage:

    from hpupdates.api import HpupdatesClient

    client = HpupdatesClient()
    updates = client.scan_updates(sys_id="83B3", country="us", language="en-US")
"""

from __future__ import annotations

from typing import Any

from hpupdates.infrastructure.installer import SoftPaqUpdate, download_and_install
from hpupdates.infrastructure.os_params import create_os_code, create_os_codes
from hpupdates.infrastructure.sudf import SudfClient, SudfCredentials, SudfRequest
from hpupdates.infrastructure.update_detector import UpdateDetector
from hpupdates.infrastructure.web import HpsaWebClient


class HpupdatesClient:
    """High-level API client for HP update operations.

    Wraps SUDF, installer, detector, and web client into a single interface.
    """

    def __init__(
        self,
        sudf: SudfClient | None = None,
        web: HpsaWebClient | None = None,
    ) -> None:
        self.sudf = sudf or SudfClient()
        self.web = web or HpsaWebClient()
        self._detector: UpdateDetector | None = None

    def scan_updates(
        self,
        sys_id: str,
        country: str = "us",
        language: str = "en-US",
        use_case: str = "HPSF",
        os_code: str = "",
    ) -> list[dict]:
        """Scan for available updates via SUDF GetUpdatesBySysId.

        Returns a list of update dicts that are needed (status=UnInstalled).
        """
        if not os_code:
            from hpupdates.infrastructure.os_params import create_os_code
            from hpupdates.infrastructure.windows.backend import WindowsDriverBackend

            try:
                backend = WindowsDriverBackend()
                os_info = backend.get_os_info()
                os_code = create_os_code(
                    os_info["os_product_name"],
                    os_info["os_version_name"],
                    os_info["architecture"],
                    os_info["release_id"],
                )
            except Exception:
                pass

        request = SudfRequest(
            use_case=use_case,
            system_id=sys_id,
            country=country,
            language=language,
            os_code=os_code,
            automatic=False,
        )
        response = self.sudf.get_updates_by_sysid(request)
        return response.get("Updates", [])

    def download_and_install(
        self,
        update: SoftPaqUpdate,
        destination: str = ".",
        silent: bool = True,
    ) -> dict[str, Any]:
        """Download and install a SoftPaq update.

        Returns a dict with the installation result.
        """
        return download_and_install(update, destination, silent=silent)


__all__ = [
    "HpupdatesClient",
    "SudfClient",
    "SudfCredentials",
    "SudfRequest",
    "SoftPaqUpdate",
    "UpdateDetector",
    "HpsaWebClient",
    "create_os_code",
    "create_os_codes",
    "download_and_install",
]
