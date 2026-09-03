from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

from packaging.version import InvalidVersion, Version

from hpupdates.models.models import (
    Device,
    DeviceRule,
    DriverPackage,
    InstalledSoftware,
    Recommendation,
    SoftwareRecommendation,
    SoftwareRule,
)


class Catalog(Protocol):
    def packages(self) -> list[DriverPackage]: ...


class _MemoryCatalog:
    def __init__(self, packages: Iterable[DriverPackage]) -> None:
        self._packages = list(packages)

    def packages(self) -> list[DriverPackage]:
        return self._packages


class DriverService:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    @classmethod
    def from_packages(cls, packages: Iterable[DriverPackage]) -> DriverService:
        return cls(_MemoryCatalog(packages))

    def scan(self, devices: Iterable[Device]) -> list[Recommendation]:
        packages = [package for package in self.catalog.packages() if package.category == "driver"]
        recommendations: list[Recommendation] = []
        for device in devices:
            matches = [package for package in packages if self._matches(device, package)]
            if not matches:
                continue
            package = max(matches, key=self._reference_version)
            rule = self._matching_rule(device, package)
            available = rule.driver_version if rule and rule.driver_version else package.version
            missing = device.current_version is None or device.problem_code == 28
            outdated = self._is_driver_outdated(device, rule, available)
            status = "missing_driver" if missing else "update_available" if outdated else "current"
            recommendations.append(
                Recommendation(
                    device,
                    package,
                    status,
                    installed_version=device.current_version,
                    available_version=available,
                )
            )
        return recommendations

    @classmethod
    def _is_driver_outdated(cls, device: Device, rule: DeviceRule | None, available: str) -> bool:
        if not device.current_version:
            return False
        # HPIA keeps an installed vendor driver when the reference is an inbox
        # Microsoft driver, regardless of the nominal reference version.
        if (
            rule
            and cls._is_microsoft(rule.driver_provider)
            and not cls._is_microsoft(device.driver_provider)
        ):
            return False
        # HPIA prefers a comparable driver date when both sides provide one.
        if rule and device.driver_date and rule.driver_date:
            local_date = cls._date(device.driver_date)
            reference_date = cls._date(rule.driver_date)
            if local_date and reference_date:
                return local_date < reference_date
        # A Microsoft inbox driver is replaceable by a vendor reference driver.
        if (
            rule
            and cls._is_microsoft(device.driver_provider)
            and not cls._is_microsoft(rule.driver_provider)
        ):
            return True
        return cls._version(device.current_version) < cls._version(available)

    @classmethod
    def _matches(cls, device: Device, package: DriverPackage) -> bool:
        return cls._matching_rule(device, package) is not None or any(
            cls._device_id_matches(candidate, supported)
            for candidate in (*device.hardware_ids, *device.compatible_ids, device.instance_id)
            for supported in package.hardware_ids
        )

    @classmethod
    def _matching_rule(cls, device: Device, package: DriverPackage) -> DeviceRule | None:
        candidates = (*device.hardware_ids, *device.compatible_ids, device.instance_id)
        for rule in package.device_rules:
            if (
                rule.class_guid
                and device.class_guid
                and rule.class_guid.casefold() != device.class_guid.casefold()
            ):
                continue
            references = tuple(value for value in (rule.hardware_id, rule.device_id) if value)
            if any(
                cls._device_id_matches(candidate, reference)
                for candidate in candidates
                for reference in references
            ):
                return rule
        return None

    @classmethod
    def _device_id_matches(cls, actual: str, reference: str) -> bool:
        actual = actual.strip().upper()
        reference = reference.strip().upper()
        if not actual or not reference:
            return False
        variants_actual = cls._device_id_variants(actual)
        variants_reference = cls._device_id_variants(reference)
        return bool(variants_actual & variants_reference) or actual.startswith(reference)

    @staticmethod
    def _device_id_variants(value: str) -> set[str]:
        value = (
            value.split("\\", 2)[0] + "\\" + value.split("\\", 2)[1]
            if value.count("\\") >= 2
            else value
        )
        variants = {value}
        without_revision = re.sub(r"&REV_[^&\\]+", "", value, flags=re.IGNORECASE)
        variants.add(without_revision)
        variants.add(re.sub(r"&SUBSYS_[^&\\]+", "", without_revision, flags=re.IGNORECASE))
        return variants

    @staticmethod
    def _is_microsoft(provider: str | None) -> bool:
        return bool(provider and "microsoft" in provider.casefold())

    @staticmethod
    def _date(value: str) -> datetime | None:
        digits = re.sub(r"\D", "", value)
        for format_string in ("%Y%m%d", "%m%d%Y"):
            try:
                return datetime.strptime(digits[:8], format_string)
            except ValueError:
                continue
        return None

    @classmethod
    def _reference_version(cls, package: DriverPackage) -> Version:
        versions = [cls._version(package.version)]
        versions.extend(
            cls._version(rule.driver_version)
            for rule in package.device_rules
            if rule.driver_version
        )
        return max(versions)

    @staticmethod
    def _version(value: str) -> Version:
        try:
            return Version(value.strip())
        except InvalidVersion:
            normalized = ".".join(re.findall(r"\d+", value))
            try:
                return Version(normalized or "0")
            except InvalidVersion:
                return Version("0")


class SoftwareService:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    @classmethod
    def from_packages(cls, packages: Iterable[DriverPackage]) -> SoftwareService:
        return cls(_MemoryCatalog(packages))

    def scan(self, installed: Iterable[InstalledSoftware]) -> list[SoftwareRecommendation]:
        local = list(installed)
        recommendations: list[SoftwareRecommendation] = []
        for package in self.catalog.packages():
            if package.category not in {"software", "firmware"}:
                continue
            rules = package.software_rules
            if not rules:
                # Public HP references do not provide a reliable detection rule for
                # every non-driver SoftPaq. Absence of evidence is not evidence that
                # the software is missing.
                continue
            rule, match = self._best_match(rules, local)
            if match is None:
                status = "missing_software"
                installed_version = None
            elif DriverService._version(match.version) < DriverService._version(rule.version):
                status = "update_available"
                installed_version = match.version
            else:
                status = "current"
                installed_version = match.version
            recommendations.append(
                SoftwareRecommendation(
                    package=package,
                    status=status,
                    installed=match,
                    installed_version=installed_version,
                    available_version=rule.version or package.version,
                )
            )
        return recommendations

    @staticmethod
    def _best_match(
        rules: tuple[SoftwareRule, ...], installed: list[InstalledSoftware]
    ) -> tuple[SoftwareRule, InstalledSoftware | None]:
        for rule in rules:
            for item in installed:
                if rule.kind and item.kind.casefold() != rule.kind.casefold():
                    continue
                if (
                    rule.architecture
                    and item.architecture
                    and rule.architecture.casefold() != item.architecture.casefold()
                ):
                    continue
                if (
                    rule.upgrade_code
                    and item.upgrade_code
                    and rule.upgrade_code.casefold() == item.upgrade_code.casefold()
                ):
                    return rule, item
        for rule in rules:
            for item in installed:
                if rule.name.casefold() != item.name.casefold():
                    continue
                if rule.vendor and item.vendor and rule.vendor.casefold() != item.vendor.casefold():
                    continue
                return rule, item
        return rules[0], None


class SudfScanService:
    """SUDF update scan service — integrates SUDF client + UpdateDetector.

    Mirrors the HP.SUDFClient scan flow from DownloadSUDFUpdateTask.cs:

    1. Gather device profile (SysId, Country, Language, OS code)
    2. Call GetUpdatesBySysId to get the list of available updates
    3. For each update, run UpdateDetector.ValidateSUDFUpdate() locally
    4. Return the list of updates that are needed (status=UnInstalled)

    The OS code generation mirrors OSParamsCreator.Creat() exactly.
    The device verification mirrors UpdateDetector.VerifyDevices() exactly.
    The file version comparison mirrors UpdateDetector.CheckFileVersion() exactly.
    """

    def __init__(
        self,
        sudf_client: Any,
        windows_backend: Any | None = None,
    ) -> None:
        self.sudf = sudf_client
        self.windows = windows_backend

    def scan(
        self,
        sys_id: str,
        country: str = "us",
        language: str = "en-US",
        use_case: str = "HPSF",
        os_code: str = "",
        automatic: bool = False,
        pnp_devices: list[str] | None = None,
        bios_info: dict[str, str] | None = None,
    ) -> list[dict]:
        from hpupdates.infrastructure.os_params import create_os_code, create_os_codes
        from hpupdates.infrastructure.sudf import SudfRequest
        from hpupdates.infrastructure.update_detector import (
            InstallStatus,
            UpdateDetector,
        )
        from hpupdates.infrastructure.update_detector import (
            SUDFUpdate as DetSUDFUpdate,
        )

        # Generate OS code if not provided
        if not os_code and self.windows:
            os_info = self.windows.get_os_info()
            os_code = create_os_code(
                os_info["os_product_name"],
                os_info["os_version_name"],
                os_info["architecture"],
                os_info["release_id"],
            )

        # Gather PnP devices if not provided
        if pnp_devices is None and self.windows:
            pnp_devices = self.windows.get_pnp_device_ids()

        # Gather BIOS info if not provided
        if bios_info is None and self.windows:
            bios_info = self.windows.get_bios_info()

        # Use sys_id from bios_info if available
        if not sys_id and bios_info:
            sys_id = bios_info.get("sys_id", "")

        # Build a list of OS codes to try — primary first, then fallbacks.
        # HP's server may not recognize new Windows versions (e.g. 25H2),
        # so we try progressively older versions.
        os_codes_to_try: list[str] = []
        if os_code:
            os_codes_to_try.append(os_code)
        if self.windows:
            try:
                os_info = self.windows.get_os_info()
                all_codes = create_os_codes(
                    os_info["os_product_name"],
                    os_info["os_version_name"],
                    os_info["architecture"],
                    os_info["release_id"],
                )
                for c in all_codes:
                    if c not in os_codes_to_try:
                        os_codes_to_try.append(c)
            except Exception:
                pass
        if not os_codes_to_try:
            os_codes_to_try = [""]

        # If the primary OS code looks like a Win11/Win10 version the server
        # might not know (25H2, 24H2), add fallback versions.
        if os_codes_to_try and os_codes_to_try[0]:
            primary = os_codes_to_try[0]
            import re as _re

            ver_match = _re.search(r"([A-Z0-9]+_)(\d+H\d)", primary)
            if ver_match:
                prefix = ver_match.group(1)
                versions = ["25H2", "24H2", "23H2", "22H2", "21H2"]
                for v in versions:
                    fallback = prefix + v
                    if fallback != primary and fallback not in os_codes_to_try:
                        os_codes_to_try.append(fallback)

        # Try each OS code until we get updates
        response = None
        for try_os_code in os_codes_to_try:
            request = SudfRequest(
                use_case=use_case,
                system_id=sys_id,
                country=country,
                language=language,
                os_code=try_os_code,
                automatic=automatic,
            )
            try:
                response = self.sudf.get_updates_by_sysid(request)
                updates_raw = response.get("Updates", []) or []
                if updates_raw:
                    break  # Got updates, stop trying
            except Exception:
                continue  # Try next OS code

        if response is None:
            # All OS codes failed — try with empty OS code as last resort
            request = SudfRequest(
                use_case=use_case,
                system_id=sys_id,
                country=country,
                language=language,
                os_code="",
                automatic=automatic,
            )
            try:
                response = self.sudf.get_updates_by_sysid(request)
            except Exception as exc:
                raise RuntimeError(f"SUDF scan failed for all OS codes: {exc}") from exc

        updates_raw = response.get("Updates", []) or []

        # Create the detector
        detector = UpdateDetector(
            pnp_devices=pnp_devices,
            sys_id=bios_info.get("sys_id", "") if bios_info else "",
            bios_rom_family=bios_info.get("bios_rom_family", "") if bios_info else "",
            bios_release_date=bios_info.get("bios_release_date") if bios_info else None,
        )

        # Validate each update
        needed_updates: list[dict] = []
        for update_raw in updates_raw:
            update = DetSUDFUpdate.from_dict(update_raw)
            status = detector.validate_sudf_update(update)
            result = dict(update_raw)
            result["detect_status"] = int(status)
            result["detect_status_name"] = InstallStatus(status).name
            if status == InstallStatus.UnInstalled:
                needed_updates.append(result)

        return needed_updates

    @staticmethod
    def classify_update(update_raw: dict, detector: object) -> tuple[int, str]:
        """Classify a single SUDF update using the detector.

        Returns (status_code, status_name).
        """
        from hpupdates.infrastructure.update_detector import (
            InstallStatus,
        )
        from hpupdates.infrastructure.update_detector import (
            SUDFUpdate as DetSUDFUpdate,
        )

        update = DetSUDFUpdate.from_dict(update_raw)
        status = detector.validate_sudf_update(update)  # type: ignore[attr-defined]
        return int(status), InstallStatus(status).name
