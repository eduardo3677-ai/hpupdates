from hpupdates.core.services import DriverService, SoftwareService
from hpupdates.models.models import (
    Device,
    DeviceRule,
    DriverPackage,
    InstalledSoftware,
    SoftwareRule,
)


def _package(**changes: object) -> DriverPackage:
    values: dict[str, object] = {
        "id": "sp1",
        "name": "HP Audio",
        "version": "10.0",
        "vendor": "HP",
        "category": "driver",
        "download_url": "https://ftp.hp.com/sp1.exe",
        "hardware_ids": (r"PCI\VEN_1234&DEV_5678",),
        "sha256": "a" * 64,
        "device_rules": (
            DeviceRule(
                device_id=r"PCI\VEN_1234&DEV_5678&SUBSYS_ABCD103C",
                hardware_id=r"PCI\VEN_1234&DEV_5678&SUBSYS_ABCD103C",
                class_guid="{CLASS}",
                driver_version="2.0.0.0",
                driver_date="20260101",
                driver_provider="HP",
            ),
        ),
    }
    values.update(changes)
    return DriverPackage(**values)  # type: ignore[arg-type]


def test_driver_detection_uses_reference_driver_version_not_softpaq_version() -> None:
    device = Device(
        instance_id=r"PCI\VEN_1234&DEV_5678&SUBSYS_ABCD103C\1",
        name="Audio",
        hardware_ids=(r"PCI\VEN_1234&DEV_5678&SUBSYS_ABCD103C&REV_01",),
        current_version="1.5.0.0",
        driver_date="20250101",
        driver_provider="HP",
        class_guid="{CLASS}",
    )

    result = DriverService.from_packages([_package()]).scan([device])

    assert len(result) == 1
    assert result[0].status == "update_available"
    assert result[0].available_version == "2.0.0.0"


def test_driver_detection_marks_problem_28_or_no_version_as_missing() -> None:
    devices = [
        Device("one", "Missing", (r"PCI\VEN_1234&DEV_5678",), None, 28),
        Device("two", "No driver", (r"PCI\VEN_1234&DEV_5678",), None, 0),
    ]

    result = DriverService.from_packages([_package()]).scan(devices)

    assert [item.status for item in result] == ["missing_driver", "missing_driver"]


def test_driver_matching_tolerates_instance_revision_and_subsystem_fallback() -> None:
    package = _package(
        device_rules=(
            DeviceRule(
                device_id=r"PCI\VEN_1234&DEV_5678&SUBSYS_OTHER",
                hardware_id=r"PCI\VEN_1234&DEV_5678&SUBSYS_OTHER",
                class_guid="{CLASS}",
                driver_version="2.0",
            ),
        ),
    )
    device = Device(
        r"PCI\VEN_1234&DEV_5678&SUBSYS_LOCAL&REV_02\ABC",
        "Device",
        (r"PCI\VEN_1234&DEV_5678&SUBSYS_LOCAL&REV_02",),
        "1.0",
        class_guid="{CLASS}",
    )

    result = DriverService.from_packages([package]).scan([device])

    assert result[0].status == "update_available"


def test_newer_local_driver_is_not_downgraded() -> None:
    device = Device(
        r"PCI\VEN_1234&DEV_5678",
        "Device",
        (r"PCI\VEN_1234&DEV_5678",),
        "3.0",
        driver_date="20270101",
    )
    result = DriverService.from_packages([_package()]).scan([device])
    assert result[0].status == "current"


def test_microsoft_reference_does_not_replace_vendor_driver() -> None:
    package = _package(
        device_rules=(
            DeviceRule(
                device_id=r"PCI\VEN_1234&DEV_5678",
                hardware_id=r"PCI\VEN_1234&DEV_5678",
                class_guid="{CLASS}",
                driver_version="9.0",
                driver_provider="Microsoft",
            ),
        )
    )
    device = Device(
        r"PCI\VEN_1234&DEV_5678",
        "Device",
        (r"PCI\VEN_1234&DEV_5678",),
        "1.0",
        driver_provider="HP Inc.",
        class_guid="{CLASS}",
    )

    assert DriverService.from_packages([package]).scan([device])[0].status == "current"


def test_software_matches_upgrade_code_before_name_and_detects_update() -> None:
    installed = [
        InstalledSoftware(
            name="Localized HP Utility",
            version="1.0",
            vendor="HP Inc.",
            upgrade_code="{PRODUCT-CODE}",
            architecture="64",
        )
    ]
    package = _package(
        category="software",
        hardware_ids=(r"HP\SYSTEM_ABCD",),
        device_rules=(),
        software_rules=(
            SoftwareRule(
                name="HP Utility",
                version="2.0",
                vendor="HP Inc.",
                upgrade_code="{PRODUCT-CODE}",
                architecture="64",
            ),
        ),
    )

    result = SoftwareService.from_packages([package]).scan(installed)

    assert len(result) == 1
    assert result[0].status == "update_available"
    assert result[0].installed_version == "1.0"


def test_software_detects_missing_and_current() -> None:
    missing_package = _package(
        id="missing",
        category="software",
        hardware_ids=(r"HP\SYSTEM_ABCD",),
        device_rules=(),
        software_rules=(SoftwareRule("HP Missing", "1.0", "HP"),),
    )
    current_package = _package(
        id="current",
        category="software",
        hardware_ids=(r"HP\SYSTEM_ABCD",),
        device_rules=(),
        software_rules=(SoftwareRule("HP Current", "2.0", "HP"),),
    )
    installed = [InstalledSoftware("HP Current", "2.1", "HP")]

    result = SoftwareService.from_packages([missing_package, current_package]).scan(installed)

    assert {item.package.id: item.status for item in result} == {
        "missing": "missing_software",
        "current": "current",
    }


def test_software_without_detection_rules_is_not_reported_missing() -> None:
    package = _package(
        category="software",
        hardware_ids=(r"HP\SYSTEM_ABCD",),
        device_rules=(),
        software_rules=(),
    )

    assert SoftwareService.from_packages([package]).scan([]) == []
