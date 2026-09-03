from typer.testing import CliRunner

from hpupdates.cli import app


def test_only_essential_commands_are_present() -> None:
    runner = CliRunner()
    root = runner.invoke(app, ["--help"])

    assert root.exit_code == 0

    # Get registered command names directly from the app.
    cmd_names = {cmd.name for cmd in app.registered_commands if cmd.name}

    # The 8 essential commands must be present.
    expected = {
        "info",
        "scan",
        "update",
        "download-all",
        "softpaq-download",
        "softpaq-install",
        "bios-check",
        "warranty",
    }
    assert expected <= cmd_names, f"Missing commands: {expected - cmd_names}"

    # Removed commands must NOT be present as command names.
    removed = {
        "inventory",
        "identify",
        "sync-catalogs",
        "software",
        "interactive",
        "doctor",
        "endpoints",
        "remove",
        "settings",
        "web-action",
        "solutions",
        "launcher",
        "os-code",
        "pnp-devices",
        "health-check",
        "messages",
        "sudf-scan",
        "sudf-scan-json",
    }
    assert not (removed & cmd_names), f"Removed commands still present: {removed & cmd_names}"


def test_scan_rejects_removed_catalog_and_mode_arguments() -> None:
    result = CliRunner().invoke(app, ["scan", "--catalog", "old.json", "--automatic"])
    assert result.exit_code != 0
