import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from hpupdates.cli import app
from hpupdates.models.models import MachineProfile

runner = CliRunner()


def _profile() -> MachineProfile:
    return MachineProfile(
        manufacturer="HP",
        model="HP Test",
        product_number="P",
        serial_number="S",
        system_id="ABCD",
        system_family="F",
        os_caption="Windows 11",
        os_version="10.0.26100",
        os_build="26100",
        os_architecture="64",
        edition_id="Professional",
        display_version="24H2",
    )


def test_cli_exposes_essential_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "info",
        "scan",
        "update",
        "download-all",
        "softpaq-download",
        "softpaq-install",
        "bios-check",
        "warranty",
    ):
        assert command in result.stdout, f"{command!r} missing from --help"


def test_scan_uses_sudf_and_returns_updates() -> None:
    """scan should auto-detect the device and query the SUDF API."""
    mock_updates = [
        {
            "Code": "sp12345",
            "Title": "Test Driver",
            "Version": "1.0",
            "Type": "Driver",
            "Category": "driver",
            "Location": "https://ftp.hp.com/pub/softpaq/sp12301-sp12400/sp12345.exe",
            "Guid": "test-guid",
            "Devices": [],
            "DetailFiles": [],
        }
    ]

    with (
        patch(
            "hpupdates.cli.sudf_cmds.detect_profile_or_exit",
            return_value=MagicMock(
                sys_id="85F0",
                serial_number="S",
                os_code="W11_24H2",
                pnp_devices=[],
                bios_info={},
                devices=[],
                summary=lambda: {"SysID": "85F0"},
            ),
        ),
        patch(
            "hpupdates.cli.sudf_cmds._scan_updates",
            return_value=mock_updates,
        ),
    ):
        result = runner.invoke(app, ["scan", "--json"])

    assert result.exit_code == 0
    # The CLI prints Rich output before the JSON; extract the JSON part.
    json_start = result.stdout.index("{")
    data = json.loads(result.stdout[json_start:])
    assert data["updates_needed"] == 1
    assert data["updates"][0]["Code"] == "sp12345"


def test_softpaq_download_help() -> None:
    """softpaq-download should accept a SoftPaq number."""
    result = runner.invoke(app, ["softpaq-download", "--help"])
    assert result.exit_code == 0
    assert "SOFTPAQ" in result.stdout.upper()


def test_bios_check_help() -> None:
    """bios-check should be available."""
    result = runner.invoke(app, ["bios-check", "--help"])
    assert result.exit_code == 0
