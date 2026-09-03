"""hpupdates CLI — main app and entry point.

8 commands, all auto-detected — no manual IDs required:
  info             Show complete device info + available updates
  scan             Scan for updates via SUDF API
  update           Download and install all needed updates
  download-all     Download all updates to a folder (no install)
  softpaq-download Download a single SoftPaq by SP number
  softpaq-install  Download and install a single SoftPaq
  bios-check       Check if a BIOS update is available
  warranty         Check warranty status for the device
"""

from __future__ import annotations

import typer
from rich.console import Console

from hpupdates.cli.sudf_cmds import app as _sudf
from hpupdates.cli.web_cmds import app as _web

app = typer.Typer(
    name="hpupdates",
    help="Open-source HP driver/software update CLI — reverse-engineered from HP Support Assistant.",
    no_args_is_help=True,
)

console = Console()

# Only register the 8 essential commands by name.
_KEEP = {
    "info", "scan", "update", "download-all",
    "softpaq-download", "softpaq-install",
    "bios-check", "warranty",
}


def _kebab(name: str) -> str:
    return name.replace("_", "-")


for _cmd_app in (_sudf, _web):
    for _info in _cmd_app.registered_commands:
        if not _info.name and _info.callback:
            _info.name = _kebab(_info.callback.__name__)
        if _info.name in _KEEP:
            app.registered_commands.append(_info)


def main() -> None:
    """Entry point for the hpupdates CLI."""
    app()


if __name__ == "__main__":
    main()
