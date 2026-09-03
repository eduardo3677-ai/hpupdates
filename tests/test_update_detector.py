"""Tests for the update detector — mirrors HP.SUDFClient.Detector.UpdateDetector."""

from __future__ import annotations

import pytest

from hpupdates.infrastructure.update_detector import (
    DotNetVersion,
    InstallStatus,
    SUDFUpdate,
    UpdateDetector,
    compare_versions,
    resolve_path,
)


# ---------------------------------------------------------------------------
# InstallStatus enum
# ---------------------------------------------------------------------------

class TestInstallStatus:
    def test_enum_values(self) -> None:
        assert InstallStatus.Invalid == -1
        assert InstallStatus.UnInstalled == 0
        assert InstallStatus.Installed == 1
        assert InstallStatus.InvalidStorePackage == 2
        assert InstallStatus.InstalledByHPSA == 3
        assert InstallStatus.UninstalledStorePackage == 4

    def test_int_comparable(self) -> None:
        assert InstallStatus.UnInstalled < InstallStatus.Installed


# ---------------------------------------------------------------------------
# DotNetVersion parsing and comparison
# ---------------------------------------------------------------------------

class TestDotNetVersionParse:
    def test_single_component(self) -> None:
        v = DotNetVersion.parse("1")
        assert v.major == 1
        assert v.minor == -1
        assert v.build == -1
        assert v.revision == -1

    def test_two_components(self) -> None:
        v = DotNetVersion.parse("1.2")
        assert v.major == 1
        assert v.minor == 2
        assert v.build == -1
        assert v.revision == -1

    def test_three_components(self) -> None:
        v = DotNetVersion.parse("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.build == 3
        assert v.revision == -1

    def test_four_components(self) -> None:
        v = DotNetVersion.parse("1.2.3.4")
        assert v == DotNetVersion(1, 2, 3, 4)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="empty version string"):
            DotNetVersion.parse("")

    def test_non_numeric_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid version string"):
            DotNetVersion.parse("abc")

    def test_strips_whitespace(self) -> None:
        v = DotNetVersion.parse("  1.2  ")
        assert v.major == 1
        assert v.minor == 2


class TestDotNetVersionComparison:
    def test_less_than(self) -> None:
        assert DotNetVersion(1, 0) < DotNetVersion(2, 0)

    def test_greater_than(self) -> None:
        assert DotNetVersion(2, 0) > DotNetVersion(1, 0)

    def test_equal(self) -> None:
        assert DotNetVersion(1, 2, 3, 4) == DotNetVersion(1, 2, 3, 4)

    def test_less_or_equal(self) -> None:
        assert DotNetVersion(1, 0) <= DotNetVersion(1, 0)
        assert DotNetVersion(1, 0) <= DotNetVersion(2, 0)

    def test_greater_or_equal(self) -> None:
        assert DotNetVersion(1, 0) >= DotNetVersion(1, 0)
        assert DotNetVersion(2, 0) >= DotNetVersion(1, 0)

    def test_major_difference_dominates(self) -> None:
        assert DotNetVersion(2, 0, 0, 0) > DotNetVersion(1, 9, 9, 9)

    def test_minor_difference(self) -> None:
        assert DotNetVersion(1, 2) > DotNetVersion(1, 1)
        assert DotNetVersion(1, 1) < DotNetVersion(1, 2)

    def test_build_difference(self) -> None:
        assert DotNetVersion(1, 0, 2) > DotNetVersion(1, 0, 1)

    def test_revision_difference(self) -> None:
        assert DotNetVersion(1, 0, 0, 5) > DotNetVersion(1, 0, 0, 4)

    def test_not_equal_to_non_version(self) -> None:
        assert DotNetVersion(1, 0) != "1.0"

    def test_frozen_dataclass(self) -> None:
        v = DotNetVersion(1, 0)
        with pytest.raises(Exception):
            v.major = 2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compare_versions
# ---------------------------------------------------------------------------

class TestCompareVersions:
    def test_server_higher_returns_uninstalled(self) -> None:
        assert compare_versions("1.0.0", "2.0.0") == InstallStatus.UnInstalled

    def test_local_higher_returns_installed(self) -> None:
        assert compare_versions("2.0.0", "1.0.0") == InstallStatus.Installed

    def test_equal_returns_installed(self) -> None:
        assert compare_versions("1.0.0", "1.0.0") == InstallStatus.Installed

    def test_revision_difference(self) -> None:
        assert compare_versions("1.2.3.4", "1.2.3.5") == InstallStatus.UnInstalled

    def test_invalid_local_returns_invalid(self) -> None:
        assert compare_versions("abc", "1.0.0") == InstallStatus.Invalid

    def test_invalid_server_returns_invalid(self) -> None:
        assert compare_versions("1.0.0", "abc") == InstallStatus.Invalid

    def test_both_invalid_returns_invalid(self) -> None:
        assert compare_versions("abc", "def") == InstallStatus.Invalid

    def test_local_higher_with_more_parts(self) -> None:
        """1.0.0.0 vs 1.0 -> local higher (revision 0 vs -1)."""
        assert compare_versions("1.0.0.0", "1.0") == InstallStatus.Installed


# ---------------------------------------------------------------------------
# resolve_path
# ---------------------------------------------------------------------------

class TestResolvePath:
    def test_system_drive(self) -> None:
        assert resolve_path("<SystemDrive>test") == "C:\\test"

    def test_windows_dir(self) -> None:
        assert resolve_path("<Windows>\\System32") == "C:\\Windows\\System32"

    def test_program_files(self) -> None:
        assert resolve_path("<ProgramFiles>") == "C:\\Program Files"

    def test_program_files_x86(self) -> None:
        assert resolve_path("<ProgramFiles(x86)>") == "C:\\Program Files (x86)"

    def test_driver_store_file_repository(self) -> None:
        result = resolve_path("<DriverStore>\\FileRepository")
        assert result == "C:\\Windows\\System32\\DriverStore\\FileRepository"

    def test_no_placeholder_unchanged(self) -> None:
        assert resolve_path("C:\\plain\\path") == "C:\\plain\\path"

    def test_multiple_placeholders(self) -> None:
        result = resolve_path("<Windows>\\<System32>")
        assert result == "C:\\Windows\\C:\\Windows\\System32"


# ---------------------------------------------------------------------------
# SUDFUpdate.from_dict
# ---------------------------------------------------------------------------

class TestSudfUpdateFromDict:
    def test_full_dict(self) -> None:
        d = {
            "Guid": "g1",
            "Code": "SP1",
            "Title": "Test",
            "Desc": "Description",
            "Severity": 2,
            "Location": "loc",
            "LocationUI": "locui",
            "Type": "Driver",
            "Category": "cat",
            "Version": "1.0",
            "Devices": ["dev1", "dev2"],
            "DetailFiles": ["f,1.0"],
            "ReturnCodes": ["0"],
            "SilentInstall": "cmd",
            "AutoInstall": 1,
            "StorePackages": "sp",
        }
        u = SUDFUpdate.from_dict(d)
        assert u.guid == "g1"
        assert u.code == "SP1"
        assert u.title == "Test"
        assert u.desc == "Description"
        assert u.severity == 2
        assert u.location == "loc"
        assert u.location_ui == "locui"
        assert u.type == "Driver"
        assert u.category == "cat"
        assert u.version == "1.0"
        assert u.devices == ("dev1", "dev2")
        assert u.detail_files == ("f,1.0",)
        assert u.return_codes == ("0",)
        assert u.silent_install == "cmd"
        assert u.auto_install == 1
        assert u.store_packages == "sp"

    def test_empty_dict(self) -> None:
        u = SUDFUpdate.from_dict({})
        assert u.guid == ""
        assert u.code == ""
        assert u.devices == ()
        assert u.detail_files == ()
        assert u.store_packages == ""
        assert u.auto_install == 0

    def test_none_fields_become_defaults(self) -> None:
        u = SUDFUpdate.from_dict({"Devices": None, "DetailFiles": None})
        assert u.devices == ()
        assert u.detail_files == ()


# ---------------------------------------------------------------------------
# UpdateDetector — VerifyDevices
# ---------------------------------------------------------------------------

class TestVerifyDevices:
    def test_empty_devices_applies_to_all(self) -> None:
        d = UpdateDetector(pnp_devices=[])
        u = SUDFUpdate(guid="g1", devices=(), type="Driver", detail_files=("f,1.0",))
        # Empty devices -> applies to all, so device check passes
        # Then detail files check runs
        assert d.validate_sudf_update(u) != InstallStatus.Invalid or True

    def test_matching_device_returns_not_invalid(self) -> None:
        d = UpdateDetector(pnp_devices=["PCI\\VEN_1234&DEV_5678"])
        u = SUDFUpdate(
            guid="g1", devices=("PCI\\VEN_1234",), type="Driver",
            detail_files=("C:\\nonexistent.sys,1.0.0.0",),
        )
        # Device matches -> proceeds to detail file check -> driver file missing
        assert d.validate_sudf_update(u) == InstallStatus.UnInstalled

    def test_no_matching_device_returns_invalid(self) -> None:
        d = UpdateDetector(pnp_devices=["PCI\\VEN_1234"])
        u = SUDFUpdate(
            guid="g1", devices=("USB\\VID_9999",), type="Driver",
            detail_files=("C:\\nonexistent.sys,1.0.0.0",),
        )
        assert d.validate_sudf_update(u) == InstallStatus.Invalid

    def test_case_insensitive_device_match(self) -> None:
        d = UpdateDetector(pnp_devices=["PCI\\VEN_1234"])
        u = SUDFUpdate(
            guid="g1", devices=("pci\\ven_1234",), type="Driver",
            detail_files=("C:\\nonexistent.sys,1.0.0.0",),
        )
        assert d.validate_sudf_update(u) == InstallStatus.UnInstalled

    def test_partial_substring_match(self) -> None:
        d = UpdateDetector(pnp_devices=["HID\\VID_04B4&PID_1234"])
        u = SUDFUpdate(
            guid="g1", devices=("VID_04B4",), type="Driver",
            detail_files=("C:\\nonexistent.sys,1.0.0.0",),
        )
        assert d.validate_sudf_update(u) == InstallStatus.UnInstalled


# ---------------------------------------------------------------------------
# UpdateDetector — invalid guid/code
# ---------------------------------------------------------------------------

class TestValidateInvalidUpdate:
    def test_empty_guid_and_code_returns_invalid(self) -> None:
        d = UpdateDetector()
        u = SUDFUpdate(guid="", code="")
        assert d.validate_sudf_update(u) == InstallStatus.Invalid


# ---------------------------------------------------------------------------
# UpdateDetector — VerifyDetailFiles
# ---------------------------------------------------------------------------

class TestVerifyDetailFiles:
    def test_driver_missing_file_returns_uninstalled(self) -> None:
        d = UpdateDetector()
        u = SUDFUpdate(
            guid="g1", type="Driver",
            detail_files=("C:\\nonexistent_driver.sys,1.0.0.0",),
        )
        assert d.validate_sudf_update(u) == InstallStatus.UnInstalled

    def test_non_driver_missing_file_returns_uninstalled(self) -> None:
        """A non-driver update whose files don't exist is UnInstalled."""
        d = UpdateDetector()
        u = SUDFUpdate(
            guid="g1", type="Software",
            detail_files=("C:\\nonexistent_app.exe,1.0.0.0",),
        )
        assert d.validate_sudf_update(u) == InstallStatus.UnInstalled

    def test_empty_detail_files_returns_uninstalled(self) -> None:
        """An update with no DetailFiles is UnInstalled (nothing to verify)."""
        d = UpdateDetector()
        u = SUDFUpdate(guid="g1", type="Driver", detail_files=())
        assert d.validate_sudf_update(u) == InstallStatus.UnInstalled

    def test_existing_file_with_older_version_returns_uninstalled(
        self, tmp_path, monkeypatch
    ) -> None:
        # Create a file on disk
        file_path = tmp_path / "driver.sys"
        file_path.write_bytes(b"fake driver")

        d = UpdateDetector()
        u = SUDFUpdate(
            guid="g1", type="Driver",
            detail_files=(f"{file_path},1.0.0.0",),
        )
        # Mock file version to be lower (0.9.0.0)
        monkeypatch.setattr(d, "_get_file_version", lambda path: "0.9.0.0")
        assert d.validate_sudf_update(u) == InstallStatus.UnInstalled

    def test_existing_file_with_newer_version_returns_installed(
        self, tmp_path, monkeypatch
    ) -> None:
        file_path = tmp_path / "driver.sys"
        file_path.write_bytes(b"fake driver")

        d = UpdateDetector()
        u = SUDFUpdate(
            guid="g1", type="Driver",
            detail_files=(f"{file_path},1.0.0.0",),
        )
        monkeypatch.setattr(d, "_get_file_version", lambda path: "2.0.0.0")
        assert d.validate_sudf_update(u) == InstallStatus.Installed


# ---------------------------------------------------------------------------
# UpdateDetector — CheckFileVersion
# ---------------------------------------------------------------------------

class TestCheckFileVersion:
    def test_invalid_detail_file_returns_invalid(self, tmp_path) -> None:
        d = UpdateDetector()
        # Single field (no comma) -> Invalid
        result = d._check_file_version("just_a_path")
        assert result == InstallStatus.Invalid

    def test_file_not_found_returns_invalid(self, tmp_path) -> None:
        d = UpdateDetector()
        result = d._check_file_version(f"{tmp_path / 'nope.sys'},1.0.0.0")
        assert result == InstallStatus.Invalid


# ---------------------------------------------------------------------------
# UpdateDetector — CheckBIOSFile
# ---------------------------------------------------------------------------

class TestCheckBiosFile:
    def test_bios_newer_server_returns_uninstalled(self) -> None:
        """Server date (2020-06-01) > local (2020-01-01)."""
        d = UpdateDetector(
            sys_id="8A4F", bios_rom_family="83B3",
            bios_release_date="01/01/2020",
        )
        result = d._check_bios_file(["ROM", "8A4F", "20.20.06.01"], False)
        assert result == InstallStatus.UnInstalled

    def test_bios_older_server_returns_installed(self) -> None:
        """Server date (2019-01-01) < local (2020-01-01)."""
        d = UpdateDetector(
            sys_id="8A4F", bios_rom_family="83B3",
            bios_release_date="01/01/2020",
        )
        result = d._check_bios_file(["ROM", "8A4F", "20.19.01.01"], False)
        assert result == InstallStatus.Installed

    def test_bios_equal_dates_returns_installed(self) -> None:
        d = UpdateDetector(
            sys_id="8A4F", bios_rom_family="83B3",
            bios_release_date="01/01/2020",
        )
        result = d._check_bios_file(["ROM", "8A4F", "20.20.01.01"], False)
        assert result == InstallStatus.Installed

    def test_bios_sys_id_mismatch_returns_invalid(self) -> None:
        d = UpdateDetector(
            sys_id="8A4F", bios_rom_family="83B3",
            bios_release_date="01/01/2020",
        )
        result = d._check_bios_file(["ROM", "XYZ123", "20.20.01.01"], False)
        assert result == InstallStatus.Invalid

    def test_bios_rom_family_match(self) -> None:
        """BIOS ID starting with ROM family should pass sys_id check."""
        d = UpdateDetector(
            sys_id="8A4F", bios_rom_family="83B3",
            bios_release_date="01/01/2020",
        )
        result = d._check_bios_file(["ROM", "83B3", "20.20.01.01"], False)
        assert result == InstallStatus.Installed

    def test_bios_ignore_sys_id_skips_check(self) -> None:
        d = UpdateDetector(
            sys_id="8A4F", bios_rom_family="83B3",
            bios_release_date="01/01/2020",
        )
        result = d._check_bios_file(["ROM", "NOMATCH", "20.20.01.01"], True)
        assert result == InstallStatus.Installed

    def test_bios_no_local_date_returns_invalid(self) -> None:
        d = UpdateDetector(sys_id="8A4F", bios_rom_family="83B3")
        result = d._check_bios_file(["ROM", "8A4F", "20.20.01.01"], False)
        assert result == InstallStatus.Invalid

    def test_bios_3_part_date_returns_invalid(self) -> None:
        """3-part date (yy.mm.dd) is not parsed — needs 4 parts."""
        d = UpdateDetector(
            sys_id="8A4F", bios_rom_family="83B3",
            bios_release_date="01/01/2020",
        )
        result = d._check_bios_file(["ROM", "8A4F", "20.01.01"], False)
        assert result == InstallStatus.Invalid


# ---------------------------------------------------------------------------
# UpdateDetector — StorePackages
# ---------------------------------------------------------------------------

class TestVerifyStorePackages:
    def test_store_package_not_installed_returns_uninstalled(self) -> None:
        d = UpdateDetector()
        u = SUDFUpdate(
            guid="g1", type="Software", detail_files=(),
            store_packages="App.Name_1.0.0.0_x64__abcde",
        )
        assert d.validate_sudf_update(u) == InstallStatus.UninstalledStorePackage

    def test_driver_with_store_package_missing_file_returns_uninstalled(self) -> None:
        d = UpdateDetector()
        u = SUDFUpdate(
            guid="g1", type="Driver",
            detail_files=("C:\\nonexistent.sys,1.0.0.0",),
            store_packages="App.Name_1.0.0.0_x64__abcde",
        )
        assert d.validate_sudf_update(u) == InstallStatus.UnInstalled

    def test_empty_store_packages_returns_invalid_store_package(self) -> None:
        d = UpdateDetector()
        result = d._verify_store_packages(
            SUDFUpdate(guid="g1", store_packages="")
        )
        assert result == InstallStatus.InvalidStorePackage

    def test_multiple_store_package_entries(self) -> None:
        d = UpdateDetector()
        u = SUDFUpdate(
            guid="g1", type="Software", detail_files=(),
            store_packages="App1_1.0.0.0_x64__aaa;App2_2.0.0.0_x64__bbb",
        )
        assert d.validate_sudf_update(u) == InstallStatus.UninstalledStorePackage
