from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Device:
    instance_id: str
    name: str
    hardware_ids: tuple[str, ...]
    current_version: str | None = None
    problem_code: int | None = None
    manufacturer: str | None = None
    compatible_ids: tuple[str, ...] = ()
    driver_date: str | None = None
    driver_provider: str | None = None
    class_guid: str | None = None
    class_name: str | None = None


@dataclass(frozen=True, slots=True)
class MachineProfile:
    manufacturer: str
    model: str
    product_number: str
    serial_number: str
    system_id: str
    system_family: str
    os_caption: str
    os_version: str
    os_build: str
    os_architecture: str
    edition_id: str
    display_version: str


@dataclass(frozen=True, slots=True)
class DeviceRule:
    device_id: str
    hardware_id: str = ""
    class_guid: str = ""
    driver_version: str = ""
    driver_date: str = ""
    driver_provider: str = ""


@dataclass(frozen=True, slots=True)
class SoftwareRule:
    name: str
    version: str
    vendor: str = ""
    upgrade_code: str = ""
    architecture: str = ""
    kind: str = "win32"


@dataclass(frozen=True, slots=True)
class InstalledSoftware:
    name: str
    version: str
    vendor: str = ""
    upgrade_code: str = ""
    architecture: str = ""
    kind: str = "win32"


@dataclass(frozen=True, slots=True)
class DriverPackage:
    id: str
    name: str
    version: str
    vendor: str
    category: str
    download_url: str
    hardware_ids: tuple[str, ...]
    sha256: str
    silent_args: tuple[str, ...] = ()
    release_date: str | None = None
    architecture: str | None = None
    device_rules: tuple[DeviceRule, ...] = ()
    software_rules: tuple[SoftwareRule, ...] = ()


@dataclass(frozen=True, slots=True)
class Recommendation:
    device: Device
    package: DriverPackage
    status: str
    installed_version: str | None = None
    available_version: str | None = None


@dataclass(frozen=True, slots=True)
class SoftwareRecommendation:
    package: DriverPackage
    status: str
    installed: InstalledSoftware | None = None
    installed_version: str | None = None
    available_version: str | None = None
