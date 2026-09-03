"""System CLI commands — doctor, endpoints."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from platformdirs import user_cache_path
from rich.console import Console
from rich.table import Table

from hpupdates.infrastructure.catalog.hp_catalog import (
    HpCatalogError,
    HpImageAssistantCatalogProvider,
)
from hpupdates.infrastructure.endpoints import endpoint_inventory
from hpupdates.infrastructure.windows.backend import WindowsDriverBackend
from hpupdates.models.models import Device

app = typer.Typer(help="Open-source, auditable Windows HP driver maintenance CLI.")
console = Console()


def _load_inventory(path: Path) -> list[Device]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Device(**{**row, "hardware_ids": tuple(row.get("hardware_ids", ()))}) for row in rows]


def _fresh_catalog(backend: WindowsDriverBackend) -> Path:
    """Always fetch HP's latest public reference before catalog-backed operations."""
    try:
        profile = backend.machine_profile()
    except (OSError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    path = user_cache_path("hp-driverctl") / "hp-image-assistant-catalog.json"
    try:
        return HpImageAssistantCatalogProvider().refresh(profile, path)
    except HpCatalogError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _inventory(backend: WindowsDriverBackend, inventory_file: Path | None) -> list[Device]:
    return _load_inventory(inventory_file) if inventory_file else backend.inventory()


@app.command()
def doctor() -> None:
    """Check runtime prerequisites."""
    import shutil
    import sys

    typer.echo(
        json.dumps(
            {
                "platform": sys.platform,
                "powershell": shutil.which("powershell.exe"),
                "pnputil": shutil.which("pnputil.exe"),
                "tar": shutil.which("tar.exe"),
            },
            indent=2,
        )
    )


@app.command("endpoints")
def endpoints_command(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List extracted HP endpoints and their enforced network policy."""
    endpoints = endpoint_inventory()
    if json_output:
        typer.echo(json.dumps([asdict(item) for item in endpoints], indent=2))
        return
    table = Table("Name", "Environment", "Region", "Category", "Network", "URL")
    for item in endpoints:
        table.add_row(
            item.name,
            item.environment,
            item.region or "-",
            item.category,
            "allowed" if item.operational else "blocked",
            item.url,
        )
    console.print(table)
