"""Device CLI commands — os-code, pnp-devices, health-check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from platformdirs import user_cache_path
from rich.console import Console
from rich.table import Table
from hpupdates.cli._helpers import _build_windows_backend

from hpupdates.models.models import Device
from hpupdates.infrastructure.catalog.hp_catalog import HpCatalogError, HpImageAssistantCatalogProvider
from hpupdates.infrastructure.windows.backend import WindowsDriverBackend

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



@app.command("os-code")
def os_code(
    os_product_name: Annotated[
        str | None,
        typer.Option("--os-name", help="Override OS product name."),
    ] = None,
    os_version: Annotated[
        str | None,
        typer.Option("--os-version", help="Override OS version name (e.g. 'Windows 11')."),
    ] = None,
    architecture: Annotated[
        str | None,
        typer.Option("--arch", help="Override architecture (32 or 64)."),
    ] = None,
    release_id: Annotated[
        str | None,
        typer.Option("--release-id", help="Override release ID (e.g. 23H2)."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Show the computed OS code (WT64_22H2, W11_23H2, etc.)."""
    from hpupdates.infrastructure.os_params import create_os_codes

    # Gather from the local system if not overridden
    if os_product_name is None or os_version is None or architecture is None or release_id is None:
        try:
            backend = _build_windows_backend()
            info = backend.get_os_info()
            if os_product_name is None:
                os_product_name = info["os_product_name"]
            if os_version is None:
                os_version = info["os_version_name"]
            if architecture is None:
                architecture = info["architecture"]
            if release_id is None:
                release_id = info["release_id"]
        except OSError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc

    codes = create_os_codes(
        os_product_name or "",
        os_version or "",
        architecture or "64",
        release_id or "",
    )

    if not codes:
        typer.echo("could not determine OS code", err=True)
        raise typer.Exit(1)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "os_code": codes[0],
                    "all_codes": codes,
                    "os_product_name": os_product_name,
                    "os_version_name": os_version,
                    "architecture": architecture,
                    "release_id": release_id,
                },
                indent=2,
            )
        )
    else:
        typer.echo(codes[0])
        if len(codes) > 1:
            typer.echo(f"all candidates: {', '.join(codes)}")


# ---------------------------------------------------------------------------
# PnP devices command
# ---------------------------------------------------------------------------


@app.command("pnp-devices")
def pnp_devices(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List all PnP device hardware IDs (Win32_PnPEntity)."""
    backend = _build_windows_backend()
    try:
        device_ids = backend.get_pnp_device_ids()
    except OSError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(device_ids, indent=2))
        return

    if not device_ids:
        typer.echo("No PnP devices found.")
        return

    table = Table("#", "Hardware ID")
    for i, dev_id in enumerate(device_ids, 1):
        table.add_row(str(i), dev_id)
    console.print(table)
    typer.echo(f"{len(device_ids)} device(s).")


# ---------------------------------------------------------------------------
# Health check command
# ---------------------------------------------------------------------------


@app.command("health-check")
def health_check(
    device_id: Annotated[
        str, typer.Option("--device-id", help="Device identifier.")
    ] = "",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Run health scan (battery, storage, cooling) via web client."""
    from hpupdates.infrastructure.web import HpsaWebClient

    client = HpsaWebClient()
    results = {
        "battery": client.device_health_battery(device_id),
        "storage": client.device_health_storage(device_id),
        "cooling": client.device_health_cooling(device_id),
    }

    if json_output:
        typer.echo(json.dumps(results, indent=2))
        return

    table = Table("Check", "Status")
    for check, data in results.items():
        status = data.get("Status", "unknown")
        table.add_row(check.capitalize(), status)
    console.print(table)


# ---------------------------------------------------------------------------
# Warranty command
# ---------------------------------------------------------------------------


