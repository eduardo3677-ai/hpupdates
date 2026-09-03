"""hpupdates CLI — main app and entry point.

All commands are defined in submodules and registered here:
  catalog_cmds  — inventory, identify, sync-catalogs, scan, download, install, remove, software, interactive
  system_cmds   — doctor, endpoints
  sudf_cmds     — sudf-scan, sudf-scan-json, softpaq-download, softpaq-install, bios-check
  device_cmds   — os-code, pnp-devices, health-check
  web_cmds      — warranty, settings, web-action, messages, solutions, launcher
"""

from __future__ import annotations

import typer
from rich.console import Console

from hpupdates.cli.catalog_cmds import app as _cat
from hpupdates.cli.system_cmds import app as _sys
from hpupdates.cli.sudf_cmds import app as _sudf
from hpupdates.cli.device_cmds import app as _dev
from hpupdates.cli.web_cmds import app as _web

app = typer.Typer(
    name="hpupdates",
    help="Open-source HP driver/software update CLI — reverse-engineered from HP Support Assistant.",
    no_args_is_help=True,
)

console = Console()

# Register all commands from submodules
for _cmd_app in (_cat, _sys, _sudf, _dev, _web):
    for _info in _cmd_app.registered_commands:
        app.registered_commands.append(_info)


def main() -> None:
    """Entry point for the hpupdates CLI."""
    app()


if __name__ == "__main__":
    main()
