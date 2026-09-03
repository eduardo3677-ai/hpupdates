import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from hpupdates.cli import app
from hpupdates.models.models import MachineProfile


def _profile() -> MachineProfile:
    return MachineProfile(
        "HP",
        "HP Test",
        "P",
        "S",
        "ABCD",
        "F",
        "Windows 11",
        "10.0.26100",
        "26100",
        "64",
        "Professional",
        "24H2",
    )


def _refresh_with(document: dict[str, object], catalog: Path):
    def refresh(_provider: object, profile: MachineProfile, output: Path) -> Path:
        assert profile.system_id == "ABCD"
        catalog.write_text(json.dumps(document), encoding="utf-8")
        return catalog

    return refresh


def test_interactive_prompts_before_download(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    inventory = tmp_path / "inventory.json"
    document = {
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

    with (
        patch("hpupdates.cli.catalog_cmds.user_cache_path", return_value=tmp_path),
        patch("hpupdates.cli.catalog_cmds.WindowsDriverBackend.machine_profile", return_value=_profile()),
        patch("hpupdates.cli.catalog_cmds.WindowsDriverBackend.installed_software", return_value=[]),
        patch(
            "hpupdates.cli.catalog_cmds.HpImageAssistantCatalogProvider.refresh",
            new=_refresh_with(document, catalog),
        ),
    ):
        result = CliRunner().invoke(
            app,
            ["interactive", "--inventory-file", str(inventory)],
            input="n\n",
        )

    assert result.exit_code == 0
    assert "Chipset" in result.stdout
    assert "Download recommended packages?" in result.stdout
    assert "No changes were made" in result.stdout


def test_software_lists_only_software_packages(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    base = {
        "version": "1.0",
        "vendor": "HP",
        "download_url": "https://ftp.hp.com/pkg.exe",
        "hardware_ids": ["SYSTEM\\HP"],
        "sha256": "b" * 64,
    }
    document = {
        "schema_version": 1,
        "packages": [
            {
                "id": "app",
                "name": "Support app",
                "category": "software",
                "software_rules": [{"name": "Support app", "version": "1.0", "vendor": "HP"}],
                **base,
            },
            {"id": "drv", "name": "Driver", "category": "driver", **base},
        ],
    }

    with (
        patch("hpupdates.cli.catalog_cmds.user_cache_path", return_value=tmp_path),
        patch("hpupdates.cli.catalog_cmds.WindowsDriverBackend.machine_profile", return_value=_profile()),
        patch("hpupdates.cli.catalog_cmds.WindowsDriverBackend.installed_software", return_value=[]),
        patch(
            "hpupdates.cli.catalog_cmds.HpImageAssistantCatalogProvider.refresh",
            new=_refresh_with(document, catalog),
        ),
    ):
        result = CliRunner().invoke(app, ["software", "--json"])

    assert result.exit_code == 0
    assert '"id": "app"' in result.stdout
    assert '"id": "drv"' not in result.stdout
