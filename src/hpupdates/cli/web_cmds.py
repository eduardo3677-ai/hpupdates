"""Web CLI commands — warranty."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console

from hpupdates.cli._helpers import _build_windows_backend

app = typer.Typer()
console = Console()


@app.command("warranty")
def warranty(
    serial_number: Annotated[
        str, typer.Option("--serial", help="Device serial number.")
    ] = "",
    product_number: Annotated[
        str, typer.Option("--product", help="Device product number.")
    ] = "",
    device_id: Annotated[
        str, typer.Option("--device-id", help="Device identifier.")
    ] = "",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Check warranty status for the device."""
    from hpupdates.infrastructure.web import HpsaWebClient

    if not serial_number:
        try:
            backend = _build_windows_backend()
            profile = backend.machine_profile()
            serial_number = profile.serial_number
            if not product_number:
                product_number = profile.product_number
        except OSError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc

    if not device_id:
        device_id = serial_number or "local"

    client = HpsaWebClient()
    result = client.device_warranty(
        device_id=device_id,
        serial_number=serial_number,
        product_number=product_number,
    )

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    typer.echo(f"Serial Number: {result.get('SerialNumber', serial_number)}")
    typer.echo(f"Product Number: {result.get('ProductNumber', product_number)}")
    typer.echo(f"Warranty Start: {result.get('WarrantyStartDate', 'N/A')}")
    typer.echo(f"Warranty End: {result.get('WarrantyEndDate', 'N/A')}")
    typer.echo(f"Warranty Status: {'active' if result.get('WarrantyStatus') else 'unknown/expired'}")
