"""Auto-detection of the local HP device profile.

All CLI commands use this to get sys_id, serial, product number, OS code,
PnP devices, and BIOS info automatically — the user never has to pass them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from hpupdates.infrastructure.os_params import create_os_code
from hpupdates.infrastructure.windows.backend import WindowsDriverBackend, CommandRunner
from hpupdates.models.models import Device, MachineProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceProfile:
    """Complete auto-detected device profile.

    Every field is populated from the local system — no user input required.
    """

    machine_profile: MachineProfile
    sys_id: str
    serial_number: str
    product_number: str
    model: str
    manufacturer: str
    os_code: str
    os_caption: str
    os_version: str
    os_build: str
    os_architecture: str
    edition_id: str
    display_version: str
    pnp_devices: list[str]
    bios_info: dict[str, str]
    devices: list[Device]
    bios_version: str
    bios_release_date: str

    def summary(self) -> dict[str, str]:
        """Return a summary dict suitable for display."""
        return {
            "SysID": self.sys_id,
            "Serial": self.serial_number,
            "ProductNumber": self.product_number,
            "Model": self.model,
            "Manufacturer": self.manufacturer,
            "OSCode": self.os_code,
            "OS": self.os_caption,
            "OSVersion": self.os_version,
            "OSBuild": self.os_build,
            "OSArch": self.os_architecture,
            "Edition": self.edition_id,
            "Release": self.display_version,
            "BIOS": self.bios_version,
            "BIOSDate": self.bios_release_date,
            "PnPDevices": str(len(self.pnp_devices)),
        }


def detect_profile() -> DeviceProfile:
    """Auto-detect the complete device profile from the local system.

    This is the single entry point all CLI commands use. It:
    1. Queries WMI for SysId, serial, product number, model, OS info
    2. Queries Win32_PnPEntity for all hardware IDs
    3. Queries Win32_BIOS for BIOS version and release date
    4. Computes the OS code from the detected OS info

    On non-Windows it raises RuntimeError with a clear message.
    """
    backend = WindowsDriverBackend(CommandRunner())

    # 1. Machine profile (SysId, serial, PN, model, OS)
    mp = backend.machine_profile()

    # 2. OS info + OS code
    os_info = backend.get_os_info()
    os_code = create_os_code(
        os_info["os_product_name"],
        os_info["os_version_name"],
        os_info["architecture"],
        os_info["release_id"],
    )

    # 3. PnP devices
    pnp_devices = backend.get_pnp_device_ids()

    # 4. BIOS info
    bios_info = backend.get_bios_info()

    # 5. Full device inventory
    devices = backend.inventory()

    return DeviceProfile(
        machine_profile=mp,
        sys_id=mp.system_id,
        serial_number=mp.serial_number,
        product_number=mp.product_number,
        model=mp.model,
        manufacturer=mp.manufacturer,
        os_code=os_code,
        os_caption=mp.os_caption,
        os_version=mp.os_version,
        os_build=mp.os_build,
        os_architecture=mp.os_architecture,
        edition_id=mp.edition_id,
        display_version=mp.display_version,
        pnp_devices=pnp_devices,
        bios_info=bios_info,
        devices=devices,
        bios_version=bios_info.get("bios_rom_family", ""),
        bios_release_date=bios_info.get("bios_release_date", ""),
    )


def detect_profile_or_exit() -> DeviceProfile:
    """Like detect_profile() but exits with a clean message on non-Windows."""
    import sys

    try:
        return detect_profile()
    except Exception as exc:
        print(f"Error: Cannot detect device profile: {exc}", file=sys.stderr)
        print(
            "This command must run on a Windows HP device with PowerShell available.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
