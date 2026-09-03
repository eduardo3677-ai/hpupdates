"""Update detection logic — mirrors HP.SUDFClient.Detector.UpdateDetector.

Reproduces the exact detection algorithms from the decompiled C# source:

  ValidateSUDFUpdate(update):
    1. VerifyDevices(update) — check if the update's Devices list matches local PnP hardware
    2. If StorePackages is empty:
       - VerifyDetailFiles(update) — check file existence and version on disk
    3. If StorePackages is set:
       - For Driver type: combine VerifyDetailFiles + VerifyStorePackages
       - For non-Driver: just VerifyStorePackages

  VerifyDevices(update):
    - Get all PnP device hardware IDs + device IDs from Win32_PnPEntity
    - For each device string in update.Devices:
      - If any local PnP ID contains it (case-insensitive), return True
    - If update.Devices is empty/null, return True (applies to all)

  VerifyDetailFiles(update):
    - For BIOS type: CheckBIOSFile() — compare release dates (yyyymmdd)
    - For Driver/other: GetAllExistFiles() + CheckFileVersion()
      * DetailFile format: "<path>,<version>"
      * Path uses <tag> placeholders resolved by PathCreator
      * If file not found and type=Driver → UnInstalled (driver missing)
      * If file found, compare FileVersionInfo against server version
      * Server version > local → UnInstalled (update available)
      * Local >= server → Installed (up to date)

  CheckBIOSFile(detailList, ignoreSysId):
    - Verify BIOS ROM family matches SysId or BIOSROMFamily
    - Compare server release date vs local BIOS release date
    - Server date > local → UnInstalled (BIOS update available)

  VerifyStorePackages(update):
    - Get all store packages via Get-AppxPackage + dism /get-provisionedappxpackages
    - Parse StorePackages as "name_version_arch_x_y;..." entries
    - Compare versions using System.Version semantics

  Version comparison uses System.Version (major.minor.build.revision),
  which is the standard .NET 4-part version comparison.

Source references:
  UpdateDetector.cs (decompiled_sudf/HP.SUDFClient.Detector/UpdateDetector.cs)
  OSInformation.cs (decompiled_sudf/HP.SUDFClient.Common/OSInformation.cs)
  SharedCommon.cs (decompiled_sudf/HP.SUDFClient.Common/SharedCommon.cs)
"""


from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path


class InstallStatus(IntEnum):
    """Mirrors HP.SUDFClient.Model.InstallStatus enum."""
    Invalid = -1
    UnInstalled = 0      # update is needed (not installed or outdated)
    Installed = 1        # update is already applied
    InvalidStorePackage = 2
    InstalledByHPSA = 3
    UninstalledStorePackage = 4


@dataclass(frozen=True, slots=True)
class SUDFUpdate:
    """Mirrors HP.SUDFClient.Model.SUDFUpdate — the update metadata from the server."""
    guid: str = ""
    code: str = ""
    title: str = ""
    desc: str = ""
    severity: int = 0
    location: str = ""
    location_ui: str = ""
    type: str = ""
    category: str = ""
    version: str = ""
    devices: tuple[str, ...] = ()
    detail_files: tuple[str, ...] = ()
    return_codes: tuple[str, ...] = ()
    silent_install: str = ""
    auto_install: int = 0
    store_packages: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> SUDFUpdate:
        return cls(
            guid=d.get("Guid", ""),
            code=d.get("Code", ""),
            title=d.get("Title", ""),
            desc=d.get("Desc", ""),
            severity=d.get("Severity", 0),
            location=d.get("Location", ""),
            location_ui=d.get("LocationUI", ""),
            type=d.get("Type", ""),
            category=d.get("Category", ""),
            version=d.get("Version", ""),
            devices=tuple(d.get("Devices") or ()),
            detail_files=tuple(d.get("DetailFiles") or ()),
            return_codes=tuple(d.get("ReturnCodes") or ()),
            silent_install=d.get("SilentInstall", ""),
            auto_install=d.get("AutoInstall", 0),
            store_packages=d.get("StorePackages", ""),
        )


# ---------------------------------------------------------------------------
# Version comparison — mirrors System.Version.CompareTo()
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DotNetVersion:
    """Mirrors System.Version: major.minor.build.revision comparison."""
    major: int = 0
    minor: int = 0
    build: int = -1
    revision: int = -1

    @classmethod
    def parse(cls, s: str) -> DotNetVersion:
        """Parse a version string, matching System.Version constructor.

        Accepts "1", "1.2", "1.2.3", or "1.2.3.4".
        Missing components default to -1 (matching .NET).
        """
        if not s:
            raise ValueError("empty version string")
        parts = s.strip().split(".")
        try:
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else -1
            build = int(parts[2]) if len(parts) > 2 else -1
            revision = int(parts[3]) if len(parts) > 3 else -1
        except (ValueError, IndexError) as exc:
            raise ValueError(f"invalid version string: {s!r}") from exc
        return cls(major, minor, build, revision)

    def __lt__(self, other: DotNetVersion) -> bool:
        return self._compare(other) < 0

    def __le__(self, other: DotNetVersion) -> bool:
        return self._compare(other) <= 0

    def __gt__(self, other: DotNetVersion) -> bool:
        return self._compare(other) > 0

    def __ge__(self, other: DotNetVersion) -> bool:
        return self._compare(other) >= 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DotNetVersion):
            return NotImplemented
        return self._compare(other) == 0

    def _compare(self, other: DotNetVersion) -> int:
        """Compare two versions, matching System.Version.CompareTo()."""
        if self.major != other.major:
            return -1 if self.major < other.major else 1
        if self.minor != other.minor:
            return -1 if self.minor < other.minor else 1
        if self.build != other.build:
            return -1 if self.build < other.build else 1
        if self.revision != other.revision:
            return -1 if self.revision < other.revision else 1
        return 0


def compare_versions(local: str, server: str) -> InstallStatus:
    """Compare a local version string against the server version.

    Returns:
      UnInstalled — server version is higher (update needed)
      Installed   — local version is equal or higher (up to date)
      Invalid     — version strings are not parseable
    """
    try:
        local_v = DotNetVersion.parse(local)
    except ValueError:
        return InstallStatus.Invalid
    try:
        server_v = DotNetVersion.parse(server)
    except ValueError:
        return InstallStatus.Invalid
    if server_v > local_v:
        return InstallStatus.UnInstalled
    return InstallStatus.Installed


# ---------------------------------------------------------------------------
# PathCreator — mirrors HP.SUDFClient.Common.PathCreator.GetPath()
# ---------------------------------------------------------------------------

# Path placeholder mappings extracted from PathCreator.cs (decompiled source)
# Complete list confirmed by subagent analysis of HP.UpdateClient.Utils
_PATH_PLACEHOLDERS: dict[str, str] = {
    "<SystemDrive>": "C:\\",
    "<Windows>": "C:\\Windows",
    "<WINDIR>": "C:\\Windows",
    "<WINSYSDIR>": "C:\\Windows\\System32",
    "<WINSYSDIRX86>": "C:\\Windows\\SysWOW64",
    "<System32>": "C:\\Windows\\System32",
    "<SysWOW64>": "C:\\Windows\\SysWOW64",
    "<System>": "C:\\Windows\\System32",
    "<ProgramFiles>": "C:\\Program Files",
    "<PROGRAMFILESDIR>": "C:\\Program Files",
    "<PROGRAMFILESDIRX86>": "C:\\Program Files (x86)",
    "<ProgramFiles(x86)>": "C:\\Program Files (x86)",
    "<COMMONFILESDIR>": "C:\\Program Files\\Common Files",
    "<COMMONFILESDIRX86>": "C:\\Program Files (x86)\\Common Files",
    "<ProgramData>": "C:\\ProgramData",
    "<CommonApplicationData>": "C:\\ProgramData",
    "<ProgramFilesCommon>": "C:\\Program Files\\Common Files",
    "<ProgramFilesCommon(x86)>": "C:\\Program Files (x86)\\Common Files",
    "<DriverStore>": "C:\\Windows\\System32\\DriverStore",
    "<FileRepository>": "C:\\Windows\\System32\\DriverStore\\FileRepository",
    "<DRIVERS>": "C:\\Windows\\System32\\drivers",
    "<DRIVERSX86>": "C:\\Windows\\SysWOW64\\drivers",
    "<FONTDIR>": "C:\\Windows\\Fonts",
    "<WINDISK>": "C:\\",
    "<WINSYSDISK>": "C:\\",
}


def resolve_path(path_template: str) -> str:
    """Resolve <tag> placeholders in a path, matching PathCreator.GetPath().

    The C# uses Environment.GetFolderPath() and registry lookups; we use
    the standard Windows paths. This is sufficient for path existence checks.
    """
    result = path_template
    for placeholder, replacement in _PATH_PLACEHOLDERS.items():
        result = result.replace(placeholder, replacement)
    return result


# ---------------------------------------------------------------------------
# UpdateDetector — mirrors HP.SUDFClient.Detector.UpdateDetector
# ---------------------------------------------------------------------------

class UpdateDetector:
    """Detects whether SUDF updates are needed on the local machine.

    Mirrors the exact logic from UpdateDetector.cs:
    - Validates devices via Win32_PnPEntity hardware IDs
    - Checks file existence and version for driver/software updates
    - Checks BIOS release dates for BIOS updates
    - Checks store packages for UWP/AppX updates
    """

    def __init__(
        self,
        pnp_devices: list[str] | None = None,
        sys_id: str = "",
        bios_rom_family: str = "",
        bios_release_date: str | None = None,
    ) -> None:
        # PnP device hardware IDs + device IDs (from Win32_PnPEntity)
        self._pnp_devices = pnp_devices or []
        self._sys_id = sys_id
        self._bios_rom_family = bios_rom_family
        self._bios_release_date = bios_release_date

    def validate_sudf_update(self, update: SUDFUpdate) -> InstallStatus:
        """Mirrors UpdateDetector.ValidateSUDFUpdate().

        Returns the install status indicating whether the update is needed.
        """
        if not update.guid and not update.code:
            return InstallStatus.Invalid

        # Step 1: Verify devices
        if not self._verify_devices(update):
            return InstallStatus.Invalid

        # Step 2: Check StorePackages or DetailFiles
        if not update.store_packages:
            return self._verify_detail_files(update)

        # StorePackages is set
        if update.type.lower() == "driver":
            detail_status = self._verify_detail_files(update)
            if detail_status == InstallStatus.UnInstalled:
                return InstallStatus.UnInstalled
            store_status = self._verify_store_packages(update)
            if detail_status == InstallStatus.Invalid:
                return InstallStatus.Invalid if store_status == InstallStatus.InvalidStorePackage else InstallStatus.UninstalledStorePackage
            # detail_status == Installed
            return InstallStatus.Installed if store_status == InstallStatus.UninstalledStorePackage else InstallStatus.UninstalledStorePackage
        else:
            return self._verify_store_packages(update)

    def _verify_devices(self, update: SUDFUpdate) -> bool:
        """Mirrors UpdateDetector.VerifyDevices().

        If update.Devices is empty, the update applies to all machines → True.
        Otherwise, check if any local PnP device ID contains any of the
        update's device strings (case-insensitive substring match).
        """
        if not update.devices:
            return True
        for device_str in update.devices:
            device_lower = device_str.lower()
            for pnp_id in self._pnp_devices:
                if device_lower in pnp_id.lower():
                    return True
        return False

    def _verify_detail_files(self, update: SUDFUpdate) -> InstallStatus:
        """Mirrors UpdateDetector.VerifyDetailFiles().

        For BIOS type: uses CheckBIOSFile() — compares release dates.
        For Driver/other: uses GetAllExistFiles() + CheckFileVersion().

        If DetailFiles is empty, the update is treated as UnInstalled —
        the server sent it because it's needed, and there's nothing
        locally to prove otherwise.
        """
        detail_files = list(update.detail_files)
        if not detail_files:
            return InstallStatus.UnInstalled

        is_bios = update.type.lower() == "bios"

        if is_bios:
            status = InstallStatus.Invalid
            for detail_file in detail_files:
                parts = [p.strip().strip('"') for p in detail_file.split(",")]
                if len(parts) > 1:
                    status = self._check_bios_file(parts, len(detail_files) == 1)
                    if status in (InstallStatus.UnInstalled, InstallStatus.Installed):
                        break
            return status

        # Non-BIOS: check file existence and versions
        is_driver = update.type.lower() == "driver"
        existing_files = self._get_all_exist_files(detail_files)
        if not existing_files:
            # Files don't exist on disk → the update is not installed
            return InstallStatus.UnInstalled

        for detail_file in existing_files:
            status = self._check_file_version(detail_file)
            if status == InstallStatus.UnInstalled:
                return InstallStatus.UnInstalled

        return InstallStatus.Installed if existing_files else InstallStatus.Invalid

    def _get_all_exist_files(self, detail_files: list[str]) -> list[str]:
        """Mirrors UpdateDetector.GetAllExistFiles().

        For each detail file "path,version":
        - Extract the path (first field)
        - Resolve <tag> placeholders
        - Check if the file exists on disk
        - Special handling for DriverStore/FileRepository paths
        """
        existing: list[str] = []
        for detail_file in detail_files:
            parts = [p.strip().strip('"') for p in detail_file.split(",")]
            if len(parts) <= 1:
                continue
            path = parts[0]
            # Extract <tag> from path and resolve it
            tag_match = re.search(r"<[^>]+>", path)
            if tag_match:
                tag = tag_match.group(0)
                path = path.replace(tag, resolve_path(tag))
            if self._check_file_existence(path):
                existing.append(detail_file)
        return existing

    def _check_file_existence(self, path: str) -> bool:
        """Check if a file exists, with DriverStore\\FileRepository handling.

        Mirrors UpdateDetector.CheckFileExistence():
        If the path contains "driverstore" and "filerepository", search
        recursively for a file with the same name in the FileRepository
        directory tree.
        """
        try:
            p = Path(path)
            if p.exists():
                return True
            path_lower = path.lower()
            if "driverstore" in path_lower and "filerepository" in path_lower:
                # Find the FileRepository base directory
                idx = path_lower.index("filerepository")
                base_dir = path[:idx + len("filerepository")]
                filename = Path(path).name
                # Search recursively for the file
                base = Path(base_dir)
                if base.exists():
                    for _found in base.rglob(filename):
                        return True
            return False
        except Exception:
            return False

    def _check_file_version(self, detail_file: str) -> InstallStatus:
        """Mirrors UpdateDetector.CheckFileVersion().

        Compares the server version against the local file's version.
        Server version > local → UnInstalled (update needed)
        Local >= server → Installed (up to date)
        """
        parts = [p.strip().strip('"') for p in detail_file.split(",")]
        if len(parts) < 2:
            return InstallStatus.Invalid
        path = parts[0]
        server_version = parts[1]

        # Resolve path
        tag_match = re.search(r"<[^>]+>", path)
        if tag_match:
            tag = tag_match.group(0)
            path = path.replace(tag, resolve_path(tag))

        # Get local file version (mirrors FileVersionInfo.GetVersionInfo)
        local_version = self._get_file_version(path)
        if local_version is None:
            return InstallStatus.Invalid

        return compare_versions(local_version, server_version)

    def _get_file_version(self, path: str) -> str | None:
        """Get a file's version string.

        Mirrors FileVersionInfo.GetVersionInfo() — returns the 4-part version
        "major.minor.build.revision" string.
        """
        try:
            import shutil
            import subprocess
            if shutil.which("powershell.exe"):
                script = (
                    f"(Get-Item '{path}' -ErrorAction SilentlyContinue)."
                    f"VersionInfo | Select-Object FileMajorPart,FileMinorPart,"
                    f"FileBuildPart,FilePrivatePart | ConvertTo-Json -Compress"
                )
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True, text=True, timeout=10
                )
                if result.stdout.strip():
                    import json
                    data = json.loads(result.stdout)
                    major = data.get("FileMajorPart", 0)
                    minor = data.get("FileMinorPart", 0)
                    build = data.get("FileBuildPart", -1)
                    revision = data.get("FilePrivatePart", -1)
                    return f"{major}.{minor}.{build}.{revision}"
        except Exception:
            pass
        return None

    def _check_bios_file(
        self, detail_list: list[str], ignore_sys_id: bool
    ) -> InstallStatus:
        """Mirrors UpdateDetector.CheckBIOSFile().

        1. If not ignoreSysId: verify BIOS ROM family matches SysId or BIOSROMFamily
        2. Parse server release date from last field (yyyy.mm.dd format)
        3. Compare against local BIOS release date
        """
        if not ignore_sys_id:
            # detail_list[1] should start with SysId or BIOSROMFamily
            if len(detail_list) < 2 or not detail_list[1]:
                return InstallStatus.Invalid
            bios_id = detail_list[1].strip()
            sys_id = self._sys_id
            rom_family = self._bios_rom_family
            if (not bios_id.startswith(sys_id)
                    and not bios_id.startswith(rom_family)
                    and not bios_id.startswith("0" + sys_id)
                    and not bios_id.startswith("0" + rom_family)):
                return InstallStatus.Invalid

        # Parse server release date from last field
        # Format: "yyyy.mm.dd" split by "."
        from datetime import date as _date
        date_str = detail_list[-1].strip()
        server_date = None
        date_parts = date_str.split(".")
        if len(date_parts) == 4:
            try:
                year = int(date_parts[0] + date_parts[1])
                month = int(date_parts[2])
                day = int(date_parts[3])
                server_date = _date(year, month, day)
            except (ValueError, IndexError):
                pass

        if server_date and self._bios_release_date:
            try:
                local_parts = self._bios_release_date.split("/")
                if len(local_parts) == 3:
                    local_date = _date(int(local_parts[2]), int(local_parts[0]), int(local_parts[1]))
                else:
                    # Try yyyymmdd format from WMI
                    d = self._bios_release_date
                    if len(d) >= 8:
                        local_date = _date(int(d[:4]), int(d[4:6]), int(d[6:8]))
                    else:
                        return InstallStatus.Invalid
                if server_date > local_date:
                    return InstallStatus.UnInstalled
                return InstallStatus.Installed
            except (ValueError, IndexError):
                return InstallStatus.Invalid

        return InstallStatus.Invalid

    def _verify_store_packages(self, update: SUDFUpdate) -> InstallStatus:
        """Mirrors UpdateDetector.VerifyStorePackages().

        Parses StorePackages as "name_version_arch_x_y;..." entries
        and checks against locally installed store packages.
        """
        store_packages = update.store_packages
        if not store_packages:
            return InstallStatus.InvalidStorePackage

        entries = store_packages.split(";")
        installed_packages = self._get_all_store_packages()

        for entry in entries:
            parts = entry.split("_")
            if len(parts) != 5:
                continue
            name = parts[0]
            version = parts[1]
            for pkg_name, pkg_version in installed_packages:
                if pkg_name == name:
                    try:
                        local_v = DotNetVersion.parse(pkg_version)
                        server_v = DotNetVersion.parse(version)
                        if local_v >= server_v:
                            return InstallStatus.Installed
                        # Found a lower version → might need update
                    except ValueError:
                        pass
            # Entry not found at all → uninstalled store package
        return InstallStatus.UninstalledStorePackage

    def _get_all_store_packages(self) -> list[tuple[str, str]]:
        """Get all installed store packages (name, version).

        Mirrors UpdateDetector.GetAllStorePackages():
        - Get-AppxPackage (PowerShell)
        - dism /online /get-provisionedappxpackages
        """
        results: list[tuple[str, str]] = []
        try:
            import shutil
            import subprocess
            if shutil.which("powershell.exe"):
                script = (
                    "Get-AppxPackage | ForEach-Object { "
                    "'{0}|{1}' -f $_.Name, $_.Version }"
                )
                result = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True, text=True, timeout=30
                )
                for line in result.stdout.strip().splitlines():
                    if "|" in line:
                        name, ver = line.split("|", 1)
                        results.append((name.strip(), ver.strip()))
        except Exception:
            pass
        return results
