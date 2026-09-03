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

# Register all commands from submodules, preserving command names.
# Typer derives the name from the function when @app.command() is used without
# an explicit name, but that derived name is stored on the *sub-app's* command
# info, not propagated when manually copying registered_commands.  We re-derive
# the name from the callback function name (replacing _ with -) when the name
# is missing.
import re as _re

def _kebab(name: str) -> str:
    return name.replace("_", "-")

for _cmd_app in (_cat, _sys, _sudf, _dev, _web):
    for _info in _cmd_app.registered_commands:
        if not _info.name and _info.callback:
            _info.name = _kebab(_info.callback.__name__)
        # Skip duplicate commands — keep the canonical version.
        # doctor/endpoints: canonical in system_cmds (skip catalog_cmds copy).
        # os-code/pnp-devices: canonical in sudf_cmds (skip device_cmds copy).
        if _info.name and _info.callback:
            _mod = _info.callback.__module__
            if _info.name in {"doctor", "endpoints"} and _mod.endswith("catalog_cmds"):
                continue
            if _info.name in {"os-code", "pnp-devices"} and _mod.endswith("device_cmds"):
                continue
        app.registered_commands.append(_info)


def main() -> None:
    """Entry point for the hpupdates CLI."""
    app()


if __name__ == "__main__":
    main()
