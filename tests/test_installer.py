"""Tests for the installer module — mirrors HP.UpdateClient download/install logic."""

from __future__ import annotations

import hashlib
import os
import zipfile

import pytest

from hpupdates.infrastructure.installer import (
    DownloadResult,
    DownloadStatus,
    InstallParameters,
    IssueResult,
    SoftPaqUpdate,
    _verify_file_extension,
    check_downloaded_file_version,
    determine_loud_mode,
    extract_softpaq,
    force_https,
    get_download_url,
    has_enough_disk_space,
    is_valid_url,
    map_installation_result,
    parse_command,
    validate_md5,
)


# ---------------------------------------------------------------------------
# IssueResult enum
# ---------------------------------------------------------------------------

class TestIssueResult:
    def test_enum_values(self) -> None:
        assert IssueResult.NewDetected == 0
        assert IssueResult.SuccessNoReboot == 1
        assert IssueResult.SuccessReboot == 2
        assert IssueResult.FailDownload == 3
        assert IssueResult.FailInstall == 13
        assert IssueResult.FailUnknownReturnCode == 14
        assert IssueResult.ExtractError == 19

    def test_int_comparable(self) -> None:
        assert IssueResult.NewDetected < IssueResult.SuccessNoReboot


# ---------------------------------------------------------------------------
# DownloadStatus enum
# ---------------------------------------------------------------------------

class TestDownloadStatus:
    def test_enum_values(self) -> None:
        assert DownloadStatus.Initializing == 0
        assert DownloadStatus.NoEnoughDiskSpace == 1
        assert DownloadStatus.Downloading == 2
        assert DownloadStatus.Cancelled == 3
        assert DownloadStatus.Failed == 4
        assert DownloadStatus.Downloaded == 7
        assert DownloadStatus.AlreadyDownloaded == 8

    def test_downloaded_greater_than_downloading(self) -> None:
        assert DownloadStatus.Downloaded > DownloadStatus.Downloading


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

class TestIsValidUrl:
    def test_valid_hp_com(self) -> None:
        assert is_valid_url("https://ftp.hp.com/pub/file.exe") is True

    def test_valid_hpicorp_net(self) -> None:
        assert is_valid_url("https://server.hpicorp.net/file.exe") is True

    def test_valid_hpicloud_net(self) -> None:
        assert is_valid_url("https://cdn.hpicloud.net/file.exe") is True

    def test_invalid_example_com(self) -> None:
        assert is_valid_url("https://example.com/file.exe") is False

    def test_invalid_with_query_string(self) -> None:
        assert is_valid_url("https://ftp.hp.com/file.exe?foo=bar") is False

    def test_subdomain_hp_com(self) -> None:
        assert is_valid_url("https://sub.ftp.hp.com/file.exe") is True

    def test_not_hp_com_suffix(self) -> None:
        assert is_valid_url("https://nothp.com/file.exe") is False

    def test_malformed_url(self) -> None:
        assert is_valid_url("not a url") is False


# ---------------------------------------------------------------------------
# force_https
# ---------------------------------------------------------------------------

class TestForceHttps:
    def test_replaces_http(self) -> None:
        assert force_https("http://ftp.hp.com/file.exe") == "https://ftp.hp.com/file.exe"

    def test_keeps_https(self) -> None:
        assert force_https("https://ftp.hp.com/file.exe") == "https://ftp.hp.com/file.exe"

    def test_other_scheme_unchanged(self) -> None:
        assert force_https("ftp://ftp.hp.com/file.exe") == "ftp://ftp.hp.com/file.exe"


# ---------------------------------------------------------------------------
# get_download_url
# ---------------------------------------------------------------------------

class TestGetDownloadUrl:
    def test_auto_uses_url_result(self) -> None:
        u = SoftPaqUpdate(
            url_result="http://ftp.hp.com/pub/sp12345.exe",
            action_type="Update",
        )
        url = get_download_url(u, is_manual=False)
        assert url == "https://ftp.hp.com/pub/sp12345.exe"

    def test_manual_uses_url_result_ui(self) -> None:
        u = SoftPaqUpdate(
            url_result="http://ftp.hp.com/auto.exe",
            url_result_ui="http://ftp.hp.com/manual.exe",
            action_type="Update",
        )
        url = get_download_url(u, is_manual=True)
        assert url == "https://ftp.hp.com/manual.exe"

    def test_manual_without_ui_falls_back_to_url_result(self) -> None:
        u = SoftPaqUpdate(
            url_result="http://ftp.hp.com/auto.exe",
            url_result_ui="",
            action_type="Update",
        )
        url = get_download_url(u, is_manual=True)
        assert url == "https://ftp.hp.com/auto.exe"

    def test_forces_https(self) -> None:
        u = SoftPaqUpdate(url_result="http://ftp.hp.com/file.exe", action_type="Update")
        url = get_download_url(u, is_manual=False)
        assert url.startswith("https://")

    def test_invalid_url_returns_empty(self) -> None:
        u = SoftPaqUpdate(url_result="http://example.com/file.exe", action_type="Update")
        assert get_download_url(u, is_manual=False) == ""

    def test_printer_driver_with_cdn(self) -> None:
        u = SoftPaqUpdate(
            url_result="http://ftp.hp.com/file.exe",
            cdn="https://cdn.hp.com",
            action_type="PrinterDriver",
        )
        url = get_download_url(u, is_manual=False)
        assert url.startswith("https://")
        assert "cdn.hp.com" in url


# ---------------------------------------------------------------------------
# validate_md5
# ---------------------------------------------------------------------------

class TestValidateMd5:
    def test_matching_checksum(self, tmp_path) -> None:
        content = b"driver binary content"
        f = tmp_path / "file.exe"
        f.write_bytes(content)
        expected = hashlib.md5(content).hexdigest()
        assert validate_md5(str(f), expected) is True
        assert f.exists()

    def test_mismatching_checksum_deletes_file(self, tmp_path) -> None:
        f = tmp_path / "file.exe"
        f.write_bytes(b"actual content")
        assert validate_md5(str(f), "0" * 32) is False
        assert not f.exists()

    def test_nonexistent_file_returns_true(self, tmp_path) -> None:
        assert validate_md5(str(tmp_path / "nope.exe"), "0" * 32) is True

    def test_case_insensitive_comparison(self, tmp_path) -> None:
        content = b"test"
        f = tmp_path / "file.exe"
        f.write_bytes(content)
        expected = hashlib.md5(content).hexdigest()
        assert validate_md5(str(f), expected.upper()) is True

    def test_uppercase_actual_matches_uppercase_expected(self, tmp_path) -> None:
        content = b"test"
        f = tmp_path / "file.exe"
        f.write_bytes(content)
        expected = hashlib.md5(content).hexdigest()
        assert validate_md5(str(f), expected.upper()) is True


# ---------------------------------------------------------------------------
# check_downloaded_file_version
# ---------------------------------------------------------------------------

class TestCheckDownloadedFileVersion:
    def test_non_sp_file_passes(self, tmp_path) -> None:
        f = tmp_path / "regular.exe"
        f.write_bytes(b"x")
        assert check_downloaded_file_version(str(f)) is True

    def test_sp_file_passes_on_non_windows(self, tmp_path) -> None:
        f = tmp_path / "sp12345.exe"
        f.write_bytes(b"x")
        assert check_downloaded_file_version(str(f)) is True


# ---------------------------------------------------------------------------
# map_installation_result (exit code mapping)
# ---------------------------------------------------------------------------

class TestMapInstallationResult:
    def test_explicit_success_code(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="0", no_reboot_success_return_code="0")
        map_installation_result(u)
        assert u.result == IssueResult.SuccessNoReboot

    def test_explicit_failure_code(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="1", no_reboot_failure_return_code="1")
        map_installation_result(u)
        assert u.result == IssueResult.FailInstall

    def test_explicit_cancel_code(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="2", no_reboot_cancel_return_code="2")
        map_installation_result(u)
        assert u.result == IssueResult.Cancel

    def test_explicit_reboot_code(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="3", reboot_success_return_code="3")
        map_installation_result(u)
        assert u.result == IssueResult.SuccessReboot

    def test_default_success_zero(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="0")
        map_installation_result(u)
        assert u.result == IssueResult.SuccessNoReboot

    def test_default_cancel_1602(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="1602")
        map_installation_result(u)
        assert u.result == IssueResult.Cancel

    def test_default_cancel_1223(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="1223")
        map_installation_result(u)
        assert u.result == IssueResult.Cancel

    def test_default_reboot_3010(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="3010")
        map_installation_result(u)
        assert u.result == IssueResult.SuccessReboot

    def test_default_unknown_code(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="999")
        map_installation_result(u)
        assert u.result == IssueResult.FailUnknownReturnCode

    def test_empty_exit_code_returns_unknown(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="")
        map_installation_result(u)
        assert u.result == IssueResult.FailUnknownReturnCode

    def test_semicolon_list(self) -> None:
        u = SoftPaqUpdate(sp_exit_code="5", no_reboot_success_return_code="4;5;6")
        map_installation_result(u)
        assert u.result == IssueResult.SuccessNoReboot

    def test_priority_success_over_fail(self) -> None:
        u = SoftPaqUpdate(
            sp_exit_code="0",
            no_reboot_success_return_code="0",
            no_reboot_failure_return_code="0",
        )
        map_installation_result(u)
        assert u.result == IssueResult.SuccessNoReboot

    def test_priority_fail_over_cancel(self) -> None:
        u = SoftPaqUpdate(
            sp_exit_code="1",
            no_reboot_failure_return_code="1",
            no_reboot_cancel_return_code="1",
        )
        map_installation_result(u)
        assert u.result == IssueResult.FailInstall

    def test_priority_cancel_over_reboot(self) -> None:
        u = SoftPaqUpdate(
            sp_exit_code="2",
            no_reboot_cancel_return_code="2",
            reboot_success_return_code="2",
        )
        map_installation_result(u)
        assert u.result == IssueResult.Cancel


# ---------------------------------------------------------------------------
# determine_loud_mode
# ---------------------------------------------------------------------------

class TestDetermineLoudMode:
    def test_no_silent_string_is_loud(self) -> None:
        u = SoftPaqUpdate(silent_install_string="")
        p = InstallParameters()
        assert determine_loud_mode(u, p) is True

    def test_has_silent_string_is_silent(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s /e")
        p = InstallParameters()
        assert determine_loud_mode(u, p) is False

    def test_is_manual_loud(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s")
        p = InstallParameters(is_manual_loud=True)
        assert determine_loud_mode(u, p) is True

    def test_bios_exception_manual(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s", is_bios_exception=True)
        p = InstallParameters(scan_type="Manual")
        assert determine_loud_mode(u, p) is True

    def test_bios_exception_not_manual(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s", is_bios_exception=True)
        p = InstallParameters(scan_type="DailyBackground")
        assert determine_loud_mode(u, p) is False

    def test_silent_fail_count_2_non_weekly(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s", silent_fail_count=2)
        p = InstallParameters()
        assert determine_loud_mode(u, p) is True

    def test_silent_fail_count_2_weekly_not_loud(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s", silent_fail_count=2)
        p = InstallParameters(scan_type="Weekly")
        assert determine_loud_mode(u, p) is False

    def test_silent_fail_count_3_weekly(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s", silent_fail_count=3)
        p = InstallParameters(scan_type="Weekly")
        assert determine_loud_mode(u, p) is True

    def test_cancel_count_3(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s", cancel_count=3)
        p = InstallParameters()
        assert determine_loud_mode(u, p) is True

    def test_loud_fail_count_3(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s", loud_fail_count=3)
        p = InstallParameters()
        assert determine_loud_mode(u, p) is True

    def test_timeout_count_3(self) -> None:
        u = SoftPaqUpdate(silent_install_string="/s", timeout_count=3)
        p = InstallParameters()
        assert determine_loud_mode(u, p) is True

    def test_below_thresholds_not_loud(self) -> None:
        u = SoftPaqUpdate(
            silent_install_string="/s",
            silent_fail_count=1, cancel_count=2, loud_fail_count=2, timeout_count=2,
        )
        p = InstallParameters()
        assert determine_loud_mode(u, p) is False


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------

class TestParseCommand:
    def test_update_wraps_command(self) -> None:
        u = SoftPaqUpdate(executable_name="sp12345.exe", action_type="Update")
        result = parse_command(u, "/tmp", "/s /e")
        assert result == '"sp12345.exe" /s /e cmd.exe /a /c "/s /e"'

    def test_printer_driver_returns_command_unchanged(self) -> None:
        u = SoftPaqUpdate(action_type="PrinterDriver")
        result = parse_command(u, "/tmp", "cmd /s")
        assert result == "cmd /s"


# ---------------------------------------------------------------------------
# _verify_file_extension
# ---------------------------------------------------------------------------

class TestVerifyFileExtension:
    @pytest.mark.parametrize("ext", [".exe", ".msi", ".msp", ".msu"])
    def test_valid_extensions(self, ext: str) -> None:
        assert _verify_file_extension(f"file{ext}") is True

    @pytest.mark.parametrize("ext", [".txt", ".dll", ".inf", ".bat", ""])
    def test_invalid_extensions(self, ext: str) -> None:
        assert _verify_file_extension(f"file{ext}") is False

    def test_case_insensitive(self) -> None:
        assert _verify_file_extension("file.EXE") is True
        assert _verify_file_extension("file.MSI") is True


# ---------------------------------------------------------------------------
# extract_softpaq
# ---------------------------------------------------------------------------

class TestExtractSoftpaq:
    def test_self_type_returns_true(self) -> None:
        assert extract_softpaq("f.exe", "/tmp", "/tmp/out", "self") is True

    def test_empty_type_returns_true(self) -> None:
        assert extract_softpaq("f.exe", "/tmp", "/tmp/out", "") is True

    def test_missing_file_returns_false(self) -> None:
        assert extract_softpaq("nofile.exe", "/tmp", "/tmp/out", "7zip") is False

    def test_zip_extraction(self, tmp_path) -> None:
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("inner.txt", "hello")
        out_dir = tmp_path / "out"
        assert extract_softpaq(
            "test.zip", str(tmp_path), str(out_dir), "zip"
        ) is True
        assert (out_dir / "inner.txt").read_text() == "hello"

    def test_zip_invalid_file_returns_false(self, tmp_path) -> None:
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"not a zip")
        assert extract_softpaq(
            "bad.zip", str(tmp_path), str(tmp_path / "out"), "zip"
        ) is False

    def test_unknown_type_returns_false(self, tmp_path) -> None:
        f = tmp_path / "f.exe"
        f.write_bytes(b"x")
        assert extract_softpaq(
            "f.exe", str(tmp_path), str(tmp_path / "out"), "unknown"
        ) is False


# ---------------------------------------------------------------------------
# has_enough_disk_space
# ---------------------------------------------------------------------------

class TestHasEnoughDiskSpace:
    def test_small_size_returns_true(self) -> None:
        assert has_enough_disk_space(1) is True

    def test_huge_size_returns_true_on_error(self) -> None:
        # Invalid drive should default to True
        assert has_enough_disk_space(0, "/nonexistent_drive_xyz") is True


# ---------------------------------------------------------------------------
# SoftPaqUpdate defaults
# ---------------------------------------------------------------------------

class TestSoftPaqUpdate:
    def test_defaults(self) -> None:
        u = SoftPaqUpdate()
        assert u.guid == ""
        assert u.action_type == "Update"
        assert u.result == IssueResult.NewDetected
        assert u.loud_fail_count == 0
        assert u.is_applicable is True

    def test_custom_values(self) -> None:
        u = SoftPaqUpdate(
            guid="g1",
            sp_id="SP12345",
            action_type="PrinterDriver",
            externally_signed=True,
        )
        assert u.guid == "g1"
        assert u.sp_id == "SP12345"
        assert u.action_type == "PrinterDriver"
        assert u.externally_signed is True


# ---------------------------------------------------------------------------
# InstallParameters defaults
# ---------------------------------------------------------------------------

class TestInstallParameters:
    def test_defaults(self) -> None:
        p = InstallParameters()
        assert p.scan_type == "Manual"
        assert p.client == "HPSA9"
        assert p.locale == "en"
        assert p.is_manual_loud is False
        assert p.guids == []

    def test_custom_values(self) -> None:
        p = InstallParameters(
            scan_type="Weekly", is_manual_loud=True, locale="de",
            guids=["g1", "g2"],
        )
        assert p.scan_type == "Weekly"
        assert p.is_manual_loud is True
        assert p.locale == "de"
        assert p.guids == ["g1", "g2"]


# ---------------------------------------------------------------------------
# DownloadResult dataclass
# ---------------------------------------------------------------------------

class TestDownloadResult:
    def test_defaults(self) -> None:
        r = DownloadResult(DownloadStatus.Downloaded)
        assert r.status == DownloadStatus.Downloaded
        assert r.local_path == ""
        assert r.error_code == ""
        assert r.bytes_transferred == 0

    def test_with_values(self) -> None:
        r = DownloadResult(
            DownloadStatus.Downloaded,
            local_path="/tmp/file.exe",
            bytes_transferred=1024,
        )
        assert r.local_path == "/tmp/file.exe"
        assert r.bytes_transferred == 1024
