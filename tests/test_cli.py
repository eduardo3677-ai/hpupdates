import json
from pathlib import Path
from unittest.mock import patch

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


def test_cli_exposes_professional_command_groups() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "identify",
        "inventory",
        "scan",
        "download",
        "install",
        "remove",
        "software",
        "doctor",
        "interactive",
    ):
        assert command in result.stdout


def test_scan_refreshes_catalog_before_using_offline_inventory(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            [
                {
                    "instance_id": "dev",
                    "name": "Device",
                    "hardware_ids": ["PCI\\VEN_1234&DEV_1"],
                    "current_version": "1.0",
                    "problem_code": 0,
                }
            ]
        ),
        encoding="utf-8",
    )

    def refresh(_provider: object, profile: MachineProfile, output: Path) -> Path:
        assert profile.system_id == "ABCD"
        catalog.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "packages": [
                        {
                            "id": "x",
                            "name": "Chipset",
                            "version": "2.0",
                            "vendor": "HP",
                            "category": "driver",
                            "download_url": "https://ftp.hp.com/x.exe",
                            "hardware_ids": ["PCI\\VEN_1234"],
                            "sha256": "a" * 64,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return catalog

    with (
        patch("hpupdates.cli.catalog_cmds.user_cache_path", return_value=tmp_path),
        patch("hpupdates.cli.catalog_cmds.WindowsDriverBackend.machine_profile", return_value=_profile()),
        patch("hpupdates.cli.catalog_cmds.HpImageAssistantCatalogProvider.refresh", new=refresh),
    ):
        result = runner.invoke(app, ["scan", "--inventory-file", str(inventory), "--json"])

    assert result.exit_code == 0
    assert '"status": "update_available"' in result.stdout


def test_remove_is_dry_run_by_default() -> None:
    result = runner.invoke(app, ["remove", "oem42.inf"])
    assert result.exit_code == 0
    assert "dry-run" in result.stdout.lower()
