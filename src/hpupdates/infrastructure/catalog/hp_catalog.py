from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from hpupdates.infrastructure.catalog.validator import JsonCatalog
from hpupdates.infrastructure.endpoints import require_operational_endpoint
from hpupdates.models.models import MachineProfile


class HpCatalogError(RuntimeError):
    pass


class HpCatalogNotFoundError(HpCatalogError):
    pass


@dataclass(frozen=True, slots=True)
class HpPlatform:
    system_id: str
    product_name: str
    system_family: str
    os_version: str
    architecture: str
    release_filename: str
    release_id: str
    display_version: str
    build: str
    is_windows_11: bool


class HpImageAssistantCatalogProvider:
    """Download current public HP Image Assistant reference catalogs."""

    BASE_URL = require_operational_endpoint("hpia_catalog").url
    PLATFORM_URL = BASE_URL + "platformList.cab"
    HP_MANUFACTURERS = {"hp", "hp inc.", "hewlett-packard", "hewlett packard"}

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        retries: int = 3,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        cab_extractor: Callable[[bytes], bytes] | None = None,
        max_download_bytes: int = 64 * 1024 * 1024,
        max_extracted_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self.client = client or httpx.Client(follow_redirects=False, timeout=timeout)
        self.retries = max(1, retries)
        self.sleep = sleep
        self.cab_extractor = cab_extractor or self._extract_cab
        self.max_download_bytes = max_download_bytes
        self.max_extracted_bytes = max_extracted_bytes

    def refresh(self, profile: MachineProfile, output: Path) -> Path:
        if profile.manufacturer.strip().casefold() not in self.HP_MANUFACTURERS:
            raise HpCatalogError("official HP catalogs require an HP-manufactured computer")
        try:
            platform_xml = self.cab_extractor(self._download(self.PLATFORM_URL))
            platform = self._select_platform(platform_xml, profile)
            reference_url = self._reference_url(platform)
            reference_xml = self.cab_extractor(self._download(reference_url))
            document = self.map_reference_xml(reference_xml, profile)
        except HpCatalogError:
            raise
        except (ET.ParseError, OSError, subprocess.SubprocessError) as exc:
            raise HpCatalogError(f"could not process the latest HP catalog: {exc}") from exc
        document["source"] = {
            "provider": "HP Image Assistant",
            "platform_url": self.PLATFORM_URL,
            "reference_url": reference_url,
            "system_id": profile.system_id,
            "os_build": profile.os_build,
        }
        return self._write_validated(output, document)

    def _download(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with self.client.stream(
                    "GET", url, headers={"Accept": "application/octet-stream"}
                ) as response:
                    if response.is_redirect:
                        raise HpCatalogError("HP catalog redirects are not followed")
                    if response.status_code in {408, 429} or response.status_code >= 500:
                        raise httpx.HTTPStatusError(
                            "transient HP catalog response",
                            request=response.request,
                            response=response,
                        )
                    if response.status_code == 404:
                        raise HpCatalogNotFoundError(f"HP catalog not found: {url}")
                    response.raise_for_status()
                    declared_size = response.headers.get("content-length")
                    if declared_size and int(declared_size) > self.max_download_bytes:
                        raise HpCatalogError("HP catalog exceeds the download size limit")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self.max_download_bytes:
                            raise HpCatalogError("HP catalog exceeds the download size limit")
                    return bytes(content)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    self.sleep(2**attempt)
        raise HpCatalogError(
            f"could not download the latest HP catalog after {self.retries} attempts"
        ) from last_error

    @staticmethod
    def _select_platform(xml_bytes: bytes, profile: MachineProfile) -> HpPlatform:
        root = ET.fromstring(xml_bytes)
        matching: list[HpPlatform] = []
        for node in root.findall("Platform"):
            system_id = (node.findtext("SystemID") or "").strip().upper()
            if system_id != profile.system_id.strip().upper():
                continue
            product_name = (node.findtext("ProductName") or "").strip()
            family = (node.findtext("SystemFamily") or "").strip()
            for os_node in node.findall("OS"):
                architecture = (os_node.findtext("OSArchitecture") or "").strip().upper()
                build = (os_node.findtext("OSBuildId") or "").strip()
                matching.append(
                    HpPlatform(
                        system_id=system_id,
                        product_name=product_name,
                        system_family=family,
                        os_version=(os_node.findtext("OSVersion") or "").strip(),
                        architecture=architecture,
                        release_filename=(os_node.findtext("OSReleaseIdFilename") or "").strip(),
                        release_id=(os_node.findtext("OSReleaseId") or "").strip(),
                        display_version=(os_node.findtext("OSReleaseIdDisplay") or "").strip(),
                        build=build,
                        is_windows_11=(os_node.findtext("IsWindows11") or "").lower() == "true",
                    )
                )
        if not matching:
            raise HpCatalogError(
                f"system ID {profile.system_id!r} is not present in the current HP platform list"
            )
        architecture_matches = [
            item for item in matching if item.architecture == profile.os_architecture.upper()
        ]
        if not architecture_matches:
            raise HpCatalogError(
                "the current HP platform list has no catalog for this architecture"
            )
        candidates = architecture_matches
        exact_build = [item for item in candidates if item.build == profile.os_build]
        if exact_build:
            return exact_build[0]
        exact_release = [
            item
            for item in candidates
            if profile.display_version
            and profile.display_version.lower()
            in {
                item.release_id.lower(),
                item.display_version.lower(),
                item.release_filename.lower(),
            }
        ]
        if exact_release:
            return exact_release[0]
        raise HpCatalogError(
            "the current HP platform list has no catalog for this Windows build, "
            "release, and architecture"
        )

    @classmethod
    def _reference_url(cls, platform: HpPlatform) -> str:
        os_major = "11.0" if platform.is_windows_11 else platform.os_version
        filename = f"{platform.system_id}_{platform.architecture}_{os_major}"
        if platform.release_filename:
            filename += f".{platform.release_filename.lower()}"
        return f"{cls.BASE_URL}{platform.system_id.lower()}/{filename.lower()}.cab"

    @staticmethod
    def map_reference_xml(xml_bytes: bytes, profile: MachineProfile) -> dict[str, object]:
        root = ET.fromstring(xml_bytes)
        hardware_by_package: dict[str, set[str]] = {}
        device_rules_by_package: dict[str, list[dict[str, str]]] = {}
        for device in root.findall("./Devices/Device"):
            identifiers = {
                value.strip()
                for value in (device.findtext("DeviceId"), device.findtext("HardwareId"))
                if value and value.strip()
            }
            for reference in device.findall("./Solutions/UpdateInfo"):
                package_id = (reference.get("IdRef") or "").strip().lower()
                hardware_by_package.setdefault(package_id, set()).update(identifiers)
                device_rules_by_package.setdefault(package_id, []).append(
                    {
                        "device_id": (device.findtext("DeviceId") or "").strip(),
                        "hardware_id": (device.findtext("HardwareId") or "").strip(),
                        "class_guid": (device.findtext("ClassGuid") or "").strip(),
                        "driver_version": (device.findtext("DriverVersion") or "").strip(),
                        "driver_date": (device.findtext("DriverDate") or "").strip(),
                        "driver_provider": (device.findtext("DriverProvider") or "").strip(),
                    }
                )

        software_rules_by_package: dict[str, list[dict[str, str]]] = {}
        for software in root.findall("./SystemInfo/SoftwareInstalled/Software"):
            for reference in software.findall("./Solutions/UpdateInfo"):
                package_id = (reference.get("IdRef") or "").strip().lower()
                software_rules_by_package.setdefault(package_id, []).append(
                    {
                        "name": (software.findtext("Name") or "").strip(),
                        "version": (software.findtext("Version") or "").strip(),
                        "vendor": (software.findtext("Vendor") or "").strip(),
                        "upgrade_code": (software.findtext("UpgradeCode") or "").strip(),
                        "architecture": HpImageAssistantCatalogProvider._software_architecture(
                            software.findtext("Is64") or ""
                        ),
                        "kind": "win32",
                    }
                )
        for software in root.findall("./SystemInfo/UWPApps/UWPApp"):
            for reference in software.findall("./Solutions/UpdateInfo"):
                package_id = (reference.get("IdRef") or "").strip().lower()
                software_rules_by_package.setdefault(package_id, []).append(
                    {
                        "name": (
                            software.findtext("DisplayName") or software.findtext("Name") or ""
                        ).strip(),
                        "version": (software.findtext("Version") or "").strip(),
                        "vendor": (
                            software.findtext("PublisherDisplayName")
                            or software.findtext("PublisherId")
                            or ""
                        ).strip(),
                        "upgrade_code": (software.findtext("FullName") or "").strip(),
                        "architecture": "",
                        "kind": "appx",
                    }
                )

        packages: list[dict[str, object]] = []
        for item in root.findall("./Solutions/UpdateInfo"):
            package_id = (item.findtext("Id") or "").strip().lower()
            sha256 = (item.findtext("SHA256") or "").strip().lower()
            raw_url = (item.findtext("Url") or "").strip()
            if not package_id or len(sha256) != 64 or not raw_url:
                continue
            url = HpImageAssistantCatalogProvider._normalize_hp_url(raw_url)
            hardware_ids = sorted(hardware_by_package.get(package_id, set()))
            if not hardware_ids:
                hardware_ids = [f"HP\\SYSTEM_{profile.system_id}"]
            silent = (item.findtext("SilentInstall") or "").strip()
            packages.append(
                {
                    "id": package_id,
                    "name": (item.findtext("Name") or package_id).strip(),
                    "version": (item.findtext("Version") or "0").strip(),
                    "vendor": (item.findtext("Vendor") or "HP").strip(),
                    "category": HpImageAssistantCatalogProvider._category(
                        item.findtext("Category") or ""
                    ),
                    "download_url": url,
                    "hardware_ids": hardware_ids,
                    "sha256": sha256,
                    "silent_args": [silent] if silent else [],
                    "release_date": (item.findtext("DateReleased") or "").strip() or None,
                    "architecture": profile.os_architecture,
                    "device_rules": device_rules_by_package.get(package_id, []),
                    "software_rules": software_rules_by_package.get(package_id, []),
                }
            )
        return {"schema_version": 1, "packages": packages}

    @staticmethod
    def _software_architecture(value: str) -> str:
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return "64"
        if normalized in {"false", "0", "no"}:
            return "32"
        return ""

    @staticmethod
    def _category(value: str) -> str:
        normalized = value.strip().lower()
        if normalized.startswith("driver"):
            return "driver"
        if "firmware" in normalized or "bios" in normalized:
            return "firmware"
        return "software"

    @staticmethod
    def _normalize_hp_url(raw_url: str) -> str:
        candidate = raw_url if "://" in raw_url else "https://" + raw_url.lstrip("/")
        parsed = urlparse(candidate)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise HpCatalogError("HP package URL uses an unsupported scheme")
        hostname = (parsed.hostname or "").lower()
        allowed_hosts = {
            urlparse(require_operational_endpoint(name).url).hostname
            for name in ("softpaq_primary", "softpaq_extended")
        }
        if hostname not in allowed_hosts:
            raise HpCatalogError("HP package URL points to an unexpected host")
        return f"https://{hostname}" + (parsed.path or "/")

    def _extract_cab(self, content: bytes) -> bytes:
        """Extract XML from a CAB file using whichever tool is available.

        Tries (in order): tar.exe (Windows bsdtar), bsdtar, 7z, tar.
        All are command-line tools that ship with the OS or are easy to install.
        No native Windows DLLs or Linux-only libraries are required.
        """
        from pathlib import Path

        with tempfile.TemporaryDirectory(prefix="hpupdates-cab-") as directory:
            root = Path(directory)
            cab = root / "catalog.cab"
            cab.write_bytes(content)

            # Find the best available CAB-capable extractor.
            extractor = (
                shutil.which("tar.exe")  # Windows 10+ ships bsdtar as tar.exe
                or shutil.which("bsdtar")  # Linux/Mac with libarchive
                or shutil.which("7z")  # 7-Zip (Windows/Linux)
                or shutil.which("tar")  # Fallback: GNU tar (may not handle CAB)
            )
            if extractor is None:
                raise HpCatalogError("No CAB extractor found. Install 'tar' (bsdtar) or '7z'.")

            # Convert path for Windows when running under WSL.
            cab_argument = str(cab)
            wslpath = shutil.which("wslpath")
            if wslpath is not None and not sys.platform.startswith("win"):
                with contextlib.suppress(
                    subprocess.CalledProcessError, FileNotFoundError
                ):
                    cab_argument = subprocess.run(
                        [wslpath, "-w", str(cab)],
                        check=True, capture_output=True, text=True,
                    ).stdout.strip()

            try:
                # List archive contents.
                listing = subprocess.run(
                    [extractor, "-tf", cab_argument],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                raise HpCatalogError("Could not inspect the HP catalog CAB") from exc

            members = [name.strip() for name in listing if name.strip()]
            if len(members) > 16:
                raise HpCatalogError("HP catalog contains too many archive members")
            if any(
                Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name
                for name in members
            ):
                raise HpCatalogError("HP catalog contains an unsafe archive path")

            xml_members = [name for name in members if name.lower().endswith(".xml")]
            if len(xml_members) != 1:
                raise HpCatalogError("HP catalog CAB must contain exactly one XML reference file")

            # Extract the XML to stdout.
            process = subprocess.Popen(
                [extractor, "-xOf", cab_argument, xml_members[0]],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdout is not None
            content_xml = bytes(process.stdout.read(self.max_extracted_bytes + 1))
            if len(content_xml) > self.max_extracted_bytes:
                process.kill()
                process.wait()
                raise HpCatalogError("HP catalog exceeds the extracted size limit")
            _, stderr = process.communicate()
            if process.returncode != 0:
                raise HpCatalogError(
                    "Could not extract the HP catalog: " + stderr.decode(errors="replace").strip()
                )
            return content_xml

    @staticmethod
    def _write_validated(output: Path, document: dict[str, object]) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".part")
        try:
            temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
            JsonCatalog(temporary).packages()
            temporary.replace(output)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if isinstance(exc, HpCatalogError):
                raise
            raise HpCatalogError(f"downloaded HP catalog is invalid: {exc}") from exc
        return output
