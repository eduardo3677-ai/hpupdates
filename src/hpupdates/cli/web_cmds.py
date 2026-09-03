"""Web CLI commands — warranty, settings, web-action, messages, solutions, launcher."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from platformdirs import user_cache_path
from rich.console import Console
from rich.table import Table
from hpupdates.cli._helpers import _build_sudf_client, _build_windows_backend

from hpupdates.core.services import DriverService, SoftwareService, SudfScanService
from hpupdates.models.models import Device
from hpupdates.infrastructure.catalog.validator import JsonCatalog
from hpupdates.infrastructure.catalog.bundle import HpCatalogBundleProvider
from hpupdates.infrastructure.downloader import Downloader
from hpupdates.infrastructure.endpoints import endpoint_inventory
from hpupdates.infrastructure.catalog.hp_catalog import HpCatalogError, HpImageAssistantCatalogProvider
from hpupdates.infrastructure.windows.backend import CommandRunner, WindowsDriverBackend

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


# ---------------------------------------------------------------------------
# Settings command
# ---------------------------------------------------------------------------


@app.command("settings")
def settings(
    set_json: Annotated[
        str | None,
        typer.Option(
            "--set",
            help="Set settings (JSON string, e.g. '{\"ShowWelcome\": false}').",
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Get or set HPSA settings."""
    from hpupdates.infrastructure.web import HpsaWebClient

    client = HpsaWebClient()

    if set_json:
        try:
            settings_data = json.loads(set_json)
        except json.JSONDecodeError as exc:
            typer.echo(f"invalid JSON: {exc}", err=True)
            raise typer.Exit(1) from exc
        result = client.set_settings(settings_data)
        typer.echo("settings updated." if result.get("FaultItemList", []) == [] else "settings update had errors.")
        return

    result = client.get_settings()
    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    table = Table("Setting", "Value")
    for key in sorted(k for k in result if k != "FaultItemList"):
        table.add_row(key, str(result[key]))
    console.print(table)


# ---------------------------------------------------------------------------
# Web action command
# ---------------------------------------------------------------------------


@app.command("web-action")
def web_action(
    action: Annotated[str, typer.Argument(help="Web action name (e.g. devices, getSettings).")],
    data_json: Annotated[
        str | None,
        typer.Option(
            "--data",
            help="Action data as JSON string (e.g. '{\"refresh\": true}').",
        ),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = True,
) -> None:
    """Dispatch any web action by name (mirrors HpsaCordovaProxy dispatch)."""
    from hpupdates.infrastructure.web import HpsaWebClient, dispatch_action, ACTION_DISPATCH

    if action not in ACTION_DISPATCH:
        available = ", ".join(sorted(ACTION_DISPATCH))
        typer.echo(f"unknown action: {action}\navailable: {available}", err=True)
        raise typer.Exit(1)

    client = HpsaWebClient()
    data = {}
    if data_json:
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError as exc:
            typer.echo(f"invalid JSON: {exc}", err=True)
            raise typer.Exit(1) from exc

    result = dispatch_action(client, action, data)
    typer.echo(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result))


# ---------------------------------------------------------------------------
# Messages command
# ---------------------------------------------------------------------------


@app.command("messages")
def messages(
    serial_number: Annotated[
        str, typer.Option("--serial", help="Device serial number.")
    ] = "",
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Refresh messages from SUDF before listing.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List cached messages (optionally refresh from SUDF GetMessages)."""
    from hpupdates.infrastructure.web import HpsaWebClient

    client = HpsaWebClient()

    if refresh:
        if not serial_number:
            try:
                backend = _build_windows_backend()
                profile = backend.machine_profile()
                serial_number = profile.serial_number
            except OSError as exc:
                typer.echo(f"error: {exc}", err=True)
                raise typer.Exit(1) from exc
        try:
            client.scan_messages(serial_number)
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"warning: refresh failed: {exc}", err=True)

    result = client.get_all_messages()
    msgs = result.get("Messages", [])

    if json_output:
        typer.echo(json.dumps(result, indent=2))
        return

    if not msgs:
        typer.echo("No messages.")
        return

    table = Table("ID", "Title", "Severity")
    for msg in msgs:
        table.add_row(
            str(msg.get("Id", msg.get("MessageId", ""))),
            str(msg.get("Title", "")),
            str(msg.get("Severity", "")),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Solutions command
# ---------------------------------------------------------------------------


@app.command("solutions")
def solutions(
    locale: Annotated[
        str, typer.Option("--locale", help="Locale folder (e.g. en-US).")
    ] = "en-US",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """List available solution HTML files from www/solutions/."""
    from importlib import resources as importlib_resources

    try:
        pkg_root = importlib_resources.files("hpupdates")
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"error: cannot locate package: {exc}", err=True)
        raise typer.Exit(1) from exc

    solutions_dir = pkg_root / "www" / "solutions" / locale
    html_files = sorted(
        f.name
        for f in solutions_dir.iterdir()
        if f.name.endswith(".html")
    ) if solutions_dir.is_dir() else []

    if json_output:
        typer.echo(json.dumps({"locale": locale, "files": html_files}, indent=2))
        return

    if not html_files:
        typer.echo(f"No solution files found for locale '{locale}'.")
        return

    table = Table("#", "File")
    for i, name in enumerate(html_files, 1):
        table.add_row(str(i), name)
    console.print(table)
    typer.echo(f"{len(html_files)} solution(s).")


# ---------------------------------------------------------------------------
# Launcher URL command
# ---------------------------------------------------------------------------


@app.command("launcher")
def launcher(
    action: Annotated[
        str,
        typer.Argument(help="Launcher action (e.g. LearnWin11) or a full URL to parse."),
    ] = "",
    serial_number: Annotated[
        str, typer.Option("--serial", help="Serial number to append.")
    ] = "",
    parse_url: Annotated[
        bool,
        typer.Option(
            "--parse/--build",
            help="Parse an existing URL instead of building one.",
        ),
    ] = False,
    list_actions: Annotated[
        bool,
        typer.Option("--list", help="List all known launcher actions."),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Parse or build hpsalauncher:// URLs."""
    from hpupdates.infrastructure.web import (
        parse_launcher_url,
        build_launcher_url,
        LAUNCHER_ACTIONS,
    )

    if list_actions:
        actions = sorted(LAUNCHER_ACTIONS)
        if json_output:
            typer.echo(json.dumps({"actions": actions}, indent=2))
            return
        table = Table("#", "Action")
        for i, a in enumerate(actions, 1):
            table.add_row(str(i), a)
        console.print(table)
        return

    if parse_url:
        parsed_action, params = parse_launcher_url(action)
        if not parsed_action:
            typer.echo("invalid launcher URL", err=True)
            raise typer.Exit(1)
        if json_output:
            typer.echo(
                json.dumps({"action": parsed_action, "params": params}, indent=2)
            )
            return
        typer.echo(f"action: {parsed_action}")
        for k, v in params.items():
            typer.echo(f"{k}: {v}")
        return

    if not action:
        typer.echo("error: action name required", err=True)
        raise typer.Exit(1)

    url = build_launcher_url(action, serial_number=serial_number)
    if json_output:
        typer.echo(json.dumps({"url": url}, indent=2))
        return
    typer.echo(url)


