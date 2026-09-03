from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from hpupdates.models.models import Device, InstalledSoftware, MachineProfile


class CommandRunner:
    def run(self, command: list[str], timeout: int = 120) -> str:
        completed = subprocess.run(
            command, check=True, text=True, capture_output=True, timeout=timeout
        )
        return completed.stdout


@dataclass
class WindowsDriverBackend:
    runner: CommandRunner
    platform: str = sys.platform

    def _require_windows(self) -> None:
        if self.platform != "win32" and shutil.which("powershell.exe") is None:
            raise OSError("this operation requires Windows")

    def inventory(self) -> list[Device]:
        self._require_windows()
        script = (
            "$ErrorActionPreference='Stop';"
            "$signed=Get-CimInstance Win32_PnPSignedDriver;"
            "Get-PnpDevice | ForEach-Object {"
            "$d=$_;$s=$signed|Where-Object DeviceID -eq $d.InstanceId|Select-Object -First 1;"
            "$compat=(Get-PnpDeviceProperty -InstanceId $d.InstanceId "
            "-KeyName DEVPKEY_Device_CompatibleIds -ErrorAction SilentlyContinue).Data;"
            "[pscustomobject]@{InstanceId=$d.InstanceId;FriendlyName=$d.FriendlyName;"
            "HardwareIds=@($d.HardwareID);DriverVersion=$s.DriverVersion;"
            "CompatibleIds=@($compat);DriverDate=$s.DriverDate;"
            "DriverProvider=$s.DriverProviderName;ProblemCode=$d.Problem;"
            "Manufacturer=$d.Manufacturer;ClassGuid=$d.ClassGuid;ClassName=$d.Class}}|"
            "ConvertTo-Json -Depth 4 -Compress"
        )
        raw = self.runner.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        parsed = json.loads(raw or "[]")
        rows = parsed if isinstance(parsed, list) else [parsed]
        return [
            Device(
                instance_id=row.get("InstanceId") or "",
                name=row.get("FriendlyName") or row.get("InstanceId") or "Unknown device",
                hardware_ids=self._strings(row.get("HardwareIds")),
                current_version=row.get("DriverVersion"),
                problem_code=row.get("ProblemCode"),
                manufacturer=row.get("Manufacturer"),
                compatible_ids=self._strings(row.get("CompatibleIds")),
                driver_date=self._wmi_date(row.get("DriverDate")),
                driver_provider=row.get("DriverProvider"),
                class_guid=row.get("ClassGuid"),
                class_name=row.get("ClassName"),
            )
            for row in rows
        ]

    @staticmethod
    def _strings(value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        values = value if isinstance(value, list) else [value]
        return tuple(str(item) for item in values if item is not None and str(item).strip())

    def installed_software(self) -> list[InstalledSoftware]:
        self._require_windows()
        script = (
            "$ErrorActionPreference='Stop';$items=@();"
            "$installer=New-Object -ComObject WindowsInstaller.Installer;"
            "function Get-UpgradeCode($productCode){"
            "if($productCode -notmatch '^\\{[0-9A-Fa-f-]{36}\\}$'){return ''};"
            "try{$local=$installer.ProductInfo($productCode,'LocalPackage');"
            "if(-not $local){return ''};$db=$installer.OpenDatabase($local,0);"
            "$view=$db.OpenView('SELECT `UpgradeCode` FROM `Upgrade`');$view.Execute();"
            "$record=$view.Fetch();if($record){return $record.StringData(1)}}catch{};return ''};"
            "$roots=@("
            "@{Path='HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*';Arch='64'},"
            "@{Path='HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*';Arch='32'},"
            "@{Path='HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*';Arch='user'});"
            "foreach($root in $roots){Get-ItemProperty $root.Path -ErrorAction SilentlyContinue|"
            "Where-Object {$_.DisplayName}|ForEach-Object {$items += [pscustomobject]@{"
            "Name=$_.DisplayName;Version=$_.DisplayVersion;Vendor=$_.Publisher;"
            "UpgradeCode=Get-UpgradeCode (Split-Path -Leaf $_.PSPath);"
            "Architecture=$root.Arch;Kind='win32'}}};"
            "Get-AppxPackage -AllUsers -ErrorAction SilentlyContinue|ForEach-Object {"
            "$items += [pscustomobject]@{Name=$_.Name;Version=$_.Version.ToString();"
            "Vendor=$_.Publisher;UpgradeCode=$_.PackageFullName;"
            "Architecture=$_.Architecture.ToString();Kind='appx'}};"
            "$items|Sort-Object Name,Version -Unique|ConvertTo-Json -Depth 3 -Compress"
        )
        raw = self.runner.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        parsed = json.loads(raw or "[]")
        rows = parsed if isinstance(parsed, list) else [parsed]
        return [
            InstalledSoftware(
                name=str(row.get("Name") or ""),
                version=str(row.get("Version") or "0"),
                vendor=str(row.get("Vendor") or ""),
                upgrade_code=str(row.get("UpgradeCode") or ""),
                architecture=str(row.get("Architecture") or ""),
                kind=str(row.get("Kind") or "win32"),
            )
            for row in rows
            if row.get("Name")
        ]

    @staticmethod
    def _wmi_date(value: object) -> str | None:
        if value is None:
            return None
        text = str(value)
        epoch_match = re.search(r"/Date\((\d+)", text)
        if epoch_match:
            timestamp = int(epoch_match.group(1)) / 1000
            return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y%m%d")
        digits = "".join(character for character in text if character.isdigit())
        return digits[:8] or None

    def machine_profile(self) -> MachineProfile:
        """Read the identifiers HP Image Assistant uses to select reference data."""
        self._require_windows()
        script = (
            "$ErrorActionPreference='Stop';"
            "$hp=Get-CimInstance -Namespace root/wmi -ClassName MS_SystemInformation;"
            "$cs=Get-CimInstance Win32_ComputerSystem;"
            "$csp=Get-CimInstance Win32_ComputerSystemProduct;"
            "$bios=Get-CimInstance Win32_BIOS;"
            "$os=Get-CimInstance CIM_OperatingSystem;"
            "$cv=Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion';"
            "[pscustomobject]@{Manufacturer=$cs.Manufacturer;Model=$cs.Model;"
            "ProductNumber=$(if($hp.SystemSKU){$hp.SystemSKU}else{$cs.SystemSKUNumber});"
            "SerialNumber=$(if($bios.SerialNumber){$bios.SerialNumber}else{$csp.IdentifyingNumber});"
            "SystemId=$hp.BaseBoardProduct;"
            "SystemFamily=$hp.SystemFamily;HpProductName=$hp.SystemProductName;"
            "OsCaption=$os.Caption;OsVersion=$os.Version;OsBuild=$os.BuildNumber;"
            "OsArchitecture=$os.OSArchitecture;EditionId=$cv.EditionID;"
            "DisplayVersion=$cv.DisplayVersion}|ConvertTo-Json -Compress"
        )
        raw = self.runner.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        row = json.loads(raw)
        architecture = str(row.get("OsArchitecture") or "")
        if "ARM" in architecture.upper():
            normalized_architecture = "ARM64" if "64" in architecture else "ARM32"
        else:
            normalized_architecture = "64" if "64" in architecture else "32"
        return MachineProfile(
            manufacturer=str(row.get("Manufacturer") or ""),
            model=str(row.get("HpProductName") or row.get("Model") or ""),
            product_number=str(row.get("ProductNumber") or ""),
            serial_number=str(row.get("SerialNumber") or ""),
            system_id=str(row.get("SystemId") or "").strip().upper(),
            system_family=str(row.get("SystemFamily") or ""),
            os_caption=str(row.get("OsCaption") or ""),
            os_version=str(row.get("OsVersion") or ""),
            os_build=str(row.get("OsBuild") or ""),
            os_architecture=normalized_architecture,
            edition_id=str(row.get("EditionId") or ""),
            display_version=str(row.get("DisplayVersion") or ""),
        )

    def verify_authenticode(self, path: str) -> bool:
        self._require_windows()
        escaped = path.replace("'", "''")
        script = f"(Get-AuthenticodeSignature -LiteralPath '{escaped}').Status"
        return (
            self.runner.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
            ).strip()
            == "Valid"
        )

    def create_restore_point(self, description: str) -> str:
        self._require_windows()
        escaped = description.replace("'", "''")
        script = f"Checkpoint-Computer -Description '{escaped}' -RestorePointType MODIFY_SETTINGS"
        return self.runner.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )

    def install_inf(self, path: str) -> str:
        self._require_windows()
        return self.runner.run(["pnputil.exe", "/add-driver", path, "/install"])

    def remove_driver(self, published_name: str, *, confirmed: bool) -> str:
        self._require_windows()
        if not confirmed:
            raise PermissionError("explicit confirmation is required")
        if not published_name.lower().startswith("oem") or not published_name.lower().endswith(
            ".inf"
        ):
            raise ValueError("published driver name must look like oemNN.inf")
        return self.runner.run(["pnputil.exe", "/delete-driver", published_name, "/uninstall"])

    def get_pnp_device_ids(self) -> list[str]:
        """Get all PnP device hardware IDs and device IDs.

        Mirrors UpdateDetector.GetPnPDevices() from HP.SUDFClient.dll:
        Queries Win32_PnPEntity and collects HardwareID + DeviceID for each device.
        Returns a distinct list of all hardware ID and device ID strings.
        """
        self._require_windows()
        script = (
            "$ErrorActionPreference='Stop';"
            "Get-CimInstance Win32_PnPEntity | ForEach-Object {"
            "$ids=@();"
            "if($_.HardwareID){$ids+=$_.HardwareID};"
            "if($_.DeviceID){$ids+=$_.DeviceID};"
            "$ids | ForEach-Object { $_ }"
            "} | Sort-Object -Unique"
        )
        raw = self.runner.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def get_bios_info(self) -> dict[str, str]:
        """Get BIOS information needed for BIOS update detection.

        Mirrors the BIOS info gathering from UpdateDetector.cs:
        - BIOSReleaseDate from registry HARDWARE\\DESCRIPTION\\System\\BIOS
        - SMBIOSBIOSVersion from Win32_BIOS (the BIOS ROM family)
        - SysId from Win32_BaseBoard.Product
        """
        self._require_windows()
        script = (
            "$ErrorActionPreference='Stop';"
            "$bios=Get-CimInstance Win32_BIOS;"
            "$baseboard=Get-CimInstance Win32_BaseBoard;"
            "$regBioDate=$null;"
            "try{$regBioDate=(Get-ItemProperty "
            "'HKLM:\\HARDWARE\\DESCRIPTION\\System\\BIOS' "
            "-ErrorAction SilentlyContinue).BIOSReleaseDate}catch{};"
            "[pscustomobject]@{"
            "BIOSReleaseDate=$regBioDate;"
            "SMBIOSBIOSVersion=$bios.SMBIOSBIOSVersion;"
            "SysId=$baseboard.Product"
            "}|ConvertTo-Json -Compress"
        )
        raw = self.runner.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        import json

        row = json.loads(raw)
        return {
            "bios_release_date": str(row.get("BIOSReleaseDate") or ""),
            "bios_rom_family": str(row.get("SMBIOSBIOSVersion") or ""),
            "sys_id": str(row.get("SysId") or "").strip(),
        }

    def get_os_info(self) -> dict[str, str]:
        """Get OS information needed for OS code generation.

        Mirrors the OS info gathering from OSInformation.cs:
        - OSProductName from registry ProductName or Win32_OperatingSystem.Caption
        - OSVersionName from Win32_OperatingSystem.Version (major.minor.build)
        - Architecture from Win32_OperatingSystem.OSArchitecture ("32" or "64")
        - ReleaseID from DisplayVersion or ReleaseId registry
        """
        self._require_windows()
        script = (
            "$ErrorActionPreference='Stop';"
            "$os=Get-CimInstance Win32_OperatingSystem;"
            "$cv=Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion';"
            "[pscustomobject]@{"
            "OSProductName=$cv.ProductName;"
            "Caption=$os.Caption;"
            "Version=$os.Version;"
            "OSArchitecture=$os.OSArchitecture;"
            "DisplayVersion=$cv.DisplayVersion;"
            "ReleaseId=$cv.ReleaseId"
            "}|ConvertTo-Json -Compress"
        )
        raw = self.runner.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        )
        import json

        row = json.loads(raw)
        arch_str = str(row.get("OSArchitecture") or "")
        arch = "64" if "64" in arch_str else ("32" if "32" in arch_str else "")
        version = str(row.get("Version") or "10.0.0")
        parts = version.split(".")
        major = int(parts[0]) if parts and parts[0].isdigit() else 10
        minor = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        build = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        from hpupdates.infrastructure.os_params import os_version_name

        os_name = os_version_name(major, minor, build)
        release_id = str(row.get("DisplayVersion") or "")
        if not release_id or release_id.lower() == "20h2":
            release_id = str(row.get("ReleaseId") or "")
        product_name = str(row.get("OSProductName") or "")
        if not product_name:
            caption = str(row.get("Caption") or "")
            product_name = (
                caption.lower().replace("microsoft", "").replace(os_name.lower(), "").strip()
            )
        return {
            "os_product_name": product_name,
            "os_version_name": os_name,
            "architecture": arch,
            "release_id": release_id,
            "version": version,
        }
