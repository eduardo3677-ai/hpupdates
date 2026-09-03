from pathlib import Path

from hpupdates.core.services import DriverService
from hpupdates.infrastructure.catalog.validator import JsonCatalog
from hpupdates.models.models import Device, DriverPackage


def test_scan_matches_hardware_ids_and_prefers_newest(tmp_path: Path) -> None:
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(
        """{
          "schema_version": 1,
          "packages": [
            {"id":"old","name":"Audio","version":"1.2.0","vendor":"HP","category":"driver","download_url":"https://ftp.hp.com/old.exe","hardware_ids":["HDAUDIO\\\\FUNC_01&VEN_10EC"],"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
            {"id":"new","name":"Audio","version":"1.10.0","vendor":"HP","category":"driver","download_url":"https://ftp.hp.com/new.exe","hardware_ids":["HDAUDIO\\\\FUNC_01&VEN_10EC"],"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
          ]
        }""",
        encoding="utf-8",
    )
    device = Device(
        instance_id="dev1",
        name="Realtek Audio",
        hardware_ids=(r"HDAUDIO\FUNC_01&VEN_10EC&DEV_0295",),
        current_version="1.0.0",
    )

    result = DriverService(JsonCatalog(catalog_file)).scan([device])

    assert len(result) == 1
    assert result[0].package.id == "new"
    assert result[0].status == "update_available"


def test_scan_reports_missing_driver() -> None:
    package = DriverPackage(
        id="wifi",
        name="Wi-Fi",
        version="2.0",
        vendor="HP",
        category="driver",
        download_url="https://ftp.hp.com/wifi.exe",
        hardware_ids=(r"PCI\VEN_8086&DEV_1234",),
        sha256="c" * 64,
    )
    device = Device(
        instance_id="dev2",
        name="Unknown network controller",
        hardware_ids=(r"PCI\VEN_8086&DEV_1234&SUBSYS_0000",),
        current_version=None,
        problem_code=28,
    )

    result = DriverService.from_packages([package]).scan([device])

    assert result[0].status == "missing_driver"
