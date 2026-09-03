from typer.testing import CliRunner

from hpupdates.cli import app


def test_catalog_modes_and_local_catalog_options_are_removed() -> None:
    runner = CliRunner()

    root = runner.invoke(app, ["--help"])
    scan = runner.invoke(app, ["scan", "--help"])
    software = runner.invoke(app, ["software", "--help"])
    interactive = runner.invoke(app, ["interactive", "--help"])

    assert root.exit_code == 0
    assert "fetch-catalog" not in root.stdout
    for result in (scan, software, interactive):
        assert result.exit_code == 0
        assert "--catalog" not in result.stdout
        assert "--automatic" not in result.stdout
        assert "--manual" not in result.stdout


def test_scan_rejects_removed_catalog_and_mode_arguments() -> None:
    result = CliRunner().invoke(app, ["scan", "--catalog", "old.json", "--automatic"])
    assert result.exit_code != 0
