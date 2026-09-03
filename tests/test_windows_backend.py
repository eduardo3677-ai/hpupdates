import json

import pytest

from hpupdates.infrastructure.windows.backend import CommandRunner, WindowsDriverBackend


class RecordingRunner(CommandRunner):
    def __init__(self, output: str = "[]") -> None:
        self.output = output
        self.commands: list[list[str]] = []

    def run(self, command: list[str]) -> str:
        self.commands.append(command)
        return self.output


def test_inventory_parses_powershell_json() -> None:
    data = [
        {
            "InstanceId": "PCI\\VEN_1234",
            "FriendlyName": "Device",
            "HardwareIds": ["PCI\\VEN_1234&DEV_5678"],
            "DriverVersion": "1.0",
            "ProblemCode": 28,
            "Manufacturer": "HP",
        }
    ]
    runner = RecordingRunner(json.dumps(data))

    devices = WindowsDriverBackend(runner=runner, platform="win32").inventory()

    assert devices[0].instance_id == "PCI\\VEN_1234"
    assert devices[0].problem_code == 28
    assert devices[0].driver_provider is None
    assert runner.commands[0][0].lower().endswith("powershell.exe")


def test_inventory_collects_driver_date_provider_class_and_compatible_ids() -> None:
    runner = RecordingRunner(
        json.dumps(
            [
                {
                    "InstanceId": "PCI\\VEN_1234",
                    "FriendlyName": "Device",
                    "HardwareIds": ["PCI\\VEN_1234&DEV_5678"],
                    "CompatibleIds": ["PCI\\VEN_1234&DEV_5678&CC_0403"],
                    "DriverVersion": "1.2.3.4",
                    "DriverDate": "20250101",
                    "DriverProvider": "HP Inc.",
                    "ProblemCode": 0,
                    "Manufacturer": "HP",
                    "ClassGuid": "{CLASS}",
                    "ClassName": "MEDIA",
                }
            ]
        )
    )

    device = WindowsDriverBackend(runner=runner, platform="win32").inventory()[0]

    assert device.compatible_ids == ("PCI\\VEN_1234&DEV_5678&CC_0403",)
    assert device.driver_date == "20250101"
    assert device.driver_provider == "HP Inc."
    assert device.class_guid == "{CLASS}"
    assert "DEVPKEY_Device_CompatibleIds" in runner.commands[0][-1]


def test_installed_software_collects_registry_and_appx_inventory() -> None:
    runner = RecordingRunner(
        json.dumps(
            [
                {
                    "Name": "HP Utility",
                    "Version": "1.0",
                    "Vendor": "HP Inc.",
                    "UpgradeCode": "{CODE}",
                    "Architecture": "64",
                    "Kind": "win32",
                },
                {
                    "Name": "AD2F1837.HPSystemEventUtility",
                    "Version": "2.0",
                    "Vendor": "CN=HP Inc.",
                    "UpgradeCode": "AD2F1837.HPSystemEventUtility_2.0_x64",
                    "Architecture": "64",
                    "Kind": "appx",
                },
            ]
        )
    )

    software = WindowsDriverBackend(runner=runner, platform="win32").installed_software()

    assert software[0].upgrade_code == "{CODE}"
    assert software[1].kind == "appx"
    script = runner.commands[0][-1]
    assert "CurrentVersion\\Uninstall" in script
    assert "Get-AppxPackage" in script
    assert "OpenDatabase" in script
    assert "PSChildName" not in script


def test_wmi_driver_date_is_normalized_from_powershell_json_epoch() -> None:
    assert WindowsDriverBackend._wmi_date("/Date(1150848000000)/") == "20060621"


def test_machine_profile_collects_hpsa_hardware_and_os_fields() -> None:
    runner = RecordingRunner(
        json.dumps(
            {
                "Manufacturer": "HP",
                "Model": "HP EliteBook Test",
                "HpProductName": "HP EliteBook Test",
                "ProductNumber": "TEST123#ABA",
                "SerialNumber": "SN123",
                "SystemId": "8A4F",
                "SystemFamily": "103C_TEST",
                "OsCaption": "Microsoft Windows 11 Pro",
                "OsVersion": "10.0.26100",
                "OsBuild": "26100",
                "OsArchitecture": "64-bit",
                "EditionId": "Professional",
                "DisplayVersion": "24H2",
            }
        )
    )

    profile = WindowsDriverBackend(runner=runner, platform="win32").machine_profile()

    assert profile.system_id == "8A4F"
    assert profile.product_number == "TEST123#ABA"
    assert profile.serial_number == "SN123"
    assert profile.os_architecture == "64"
    script = runner.commands[0][-1]
    assert "MS_SystemInformation" in script
    assert "Win32_ComputerSystemProduct" in script
    assert "Win32_BIOS" in script
    assert "CIM_OperatingSystem" in script
    assert "SystemSKU" in script


def test_remove_requires_explicit_confirmation() -> None:
    runner = RecordingRunner()
    backend = WindowsDriverBackend(runner=runner, platform="win32")

    with pytest.raises(PermissionError, match="confirmation"):
        backend.remove_driver("oem42.inf", confirmed=False)

    assert runner.commands == []


def test_remove_uses_pnputil_after_confirmation() -> None:
    runner = RecordingRunner()
    backend = WindowsDriverBackend(runner=runner, platform="win32")

    backend.remove_driver("oem42.inf", confirmed=True)

    assert runner.commands == [["pnputil.exe", "/delete-driver", "oem42.inf", "/uninstall"]]


def test_install_inf_uses_pnputil() -> None:
    runner = RecordingRunner()
    WindowsDriverBackend(runner=runner, platform="win32").install_inf(r"C:\cache\driver.inf")
    assert runner.commands == [["pnputil.exe", "/add-driver", r"C:\cache\driver.inf", "/install"]]
