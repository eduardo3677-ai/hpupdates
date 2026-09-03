"""Catalog-based CLI commands — inventory, identify, sync-catalogs, scan, download, install, remove, software, interactive."""

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
from hpupdates.infrastructure.os_params import create_os_code
from hpupdates.infrastructure.windows.backend import CommandRunner, WindowsDriverBackend

app = typer.Typer(help="Open-source, auditable Windows HP driver maintenance CLI.")
console = Console()


def _load_inventory(path: Path) -> list[Device]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Device(**{**row, "hardware_ids": tuple(row.get("hardware_ids", ()))}) for row in rows]


def _fresh_catalog(backend: WindowsDriverBackend) -> Path:
    """Always fetch HP's latest public reference before catalog-backed operations.

    Tries HPIA catalog first.  Falls back to SUDF when the platform is not in
    the HPIA platform list (some HP systems are in SUDF but not HPIA).
    """
    try:
        profile = backend.machine_profile()
    except (OSError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    path = user_cache_path("hp-driverctl") / "hp-image-assistant-catalog.json"
    try:
        return HpImageAssistantCatalogProvider().refresh(profile, path)
    except HpCatalogError as exc:
        # If the system ID is not in the HPIA platform list, fall back to SUDF.
        if "is not present in the current HP platform list" in str(exc):
            return _sudf_catalog_fallback(profile, path)
        raise typer.BadParameter(str(exc)) from exc


def _sudf_catalog_fallback(profile, path: Path) -> Path:
    """Build a catalog JSON from the SUDF API when HPIA doesn't know the platform."""
    from hpupdates.cli._helpers import _build_sudf_client
    from hpupdates.infrastructure.sudf import SudfRequest

    console = Console()
    console.print(
        "[yellow]Platform not in HPIA catalog — using SUDF API instead.[/]"
    )
    client = _build_sudf_client()

    os_code = create_os_code(
        profile.os_caption,
        profile.os_version,
        profile.os_architecture,
        profile.display_version or profile.edition_id,
    )
    request = SudfRequest(
        use_case="HPSF",
        system_id=profile.system_id,
        country="us",
        language="en-US",
        os_code=os_code,
        automatic=False,
    )
    result = client.get_updates_by_sysid(request)
    updates = result.get("Updates", []) or []
    packages = []
    for u in updates:
        packages.append({
            "id": str(u.get("Code", "")).lower(),
            "name": u.get("Title", u.get("Code", "")),
            "version": str(u.get("Version", "0")),
            "vendor": "HP",
            "category": _sudf_category(u.get("Type", "")),
            "download_url": u.get("Url", ""),
            "hardware_ids": [],
            "sha256": "",
            "silent_args": [],
            "release_date": u.get("DateReleased"),
            "architecture": profile.os_architecture,
            "device_rules": [],
            "software_rules": [],
        })
    document = {
        "schema_version": 1,
        "packages": packages,
        "source": {
            "provider": "SUDF fallback",
            "system_id": profile.system_id,
            "os_code": os_code,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def _sudf_category(update_type: str) -> str:
    t = update_type.lower()
    if "bios" in t or "firmware" in t:
        return "firmware"
    if "driver" in t:
        return "driver"
    return "software"


def _inventory(backend: WindowsDriverBackend, inventory_file: Path | None) -> list[Device]:
    return _load_inventory(inventory_file) if inventory_file else backend.inventory()



@app.command()
def inventory(
    output: Annotated[Path | None, typer.Option(help="Write inventory JSON to this file.")] = None,
) -> None:
    """Collect Windows PnP hardware and installed driver versions."""
    devices = WindowsDriverBackend(CommandRunner()).inventory()
    text = json.dumps([asdict(device) for device in devices], indent=2)
    if output:
        output.write_text(text, encoding="utf-8")
    else:
        typer.echo(text)


@app.command()
def identify() -> None:
    """Show the Windows/SMBIOS identity used to select HP's catalog."""
    try:
        profile = WindowsDriverBackend(CommandRunner()).machine_profile()
    except (OSError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(asdict(profile), indent=2))


@app.command("sync-catalogs")
def sync_catalogs(
    output: Annotated[
        Path | None,
        typer.Option(help="Directory for the official catalog bundle."),
    ] = None,
) -> None:
    """Download all current public HPIA catalog families for this HP system.

    If the platform is not in the HPIA list, falls back to SUDF.
    """
    backend = WindowsDriverBackend(CommandRunner())
    try:
        profile = backend.machine_profile()
        destination = output or user_cache_path("hp-driverctl") / "catalogs"
        try:
            manifest = HpCatalogBundleProvider().sync(profile, destination)
        except HpCatalogError as exc:
            if "is not present in the current HP platform list" in str(exc):
                console.print(
                    "[yellow]Platform not in HPIA catalog — using SUDF API instead.[/]"
                )
                catalog = _sudf_catalog_fallback(
                    profile,
                    user_cache_path("hp-driverctl") / "hp-image-assistant-catalog.json",
                )
                typer.echo(json.dumps({
                    "source": "SUDF fallback",
                    "catalog": str(catalog),
                    "system_id": profile.system_id,
                }, indent=2))
                return
            raise typer.BadParameter(str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(asdict(manifest), indent=2))


@app.command()
def scan(
    inventory_file: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Download the latest official HP catalog and find driver updates.

    Uses the HPIA catalog when available, or falls back to the SUDF API.
    """
    backend = WindowsDriverBackend(CommandRunner())
    catalog = _fresh_catalog(backend)

    # Check if this is a SUDF fallback catalog (no hardware_ids to match).
    catalog_doc = json.loads(catalog.read_text(encoding="utf-8"))
    source = catalog_doc.get("source", {}).get("provider", "")
    if source == "SUDF fallback":
        # SUDF updates don't have hardware_ids for device matching.
        # List all available updates directly.
        packages = catalog_doc.get("packages", [])
        if json_output:
            typer.echo(json.dumps(packages, indent=2))
            return
        table = Table("Code", "Title", "Version", "Type")
        for pkg in packages:
            table.add_row(
                pkg.get("id", ""),
                pkg.get("name", "")[:50],
                pkg.get("version", ""),
                pkg.get("category", ""),
            )
        console.print(table)
        return

    recommendations = DriverService(JsonCatalog(catalog)).scan(_inventory(backend, inventory_file))
    if json_output:
        typer.echo(json.dumps([asdict(item) for item in recommendations], indent=2))
        return
    table = Table("Status", "Device", "Package", "Installed", "Available")
    for item in recommendations:
        table.add_row(
            item.status,
            item.device.name,
            item.package.name,
            item.device.current_version or "missing",
            item.available_version,
        )
    console.print(table)


@app.command()
def download(
    package_id: str,
    destination: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Refresh HP's catalog, then download and verify one current package."""
    backend = WindowsDriverBackend(CommandRunner())
    catalog = _fresh_catalog(backend)
    packages = {item.id: item for item in JsonCatalog(catalog).packages()}
    normalized_id = package_id.lower()
    if normalized_id not in packages:
        raise typer.BadParameter(f"unknown package for this system: {package_id}")
    package = packages[normalized_id]
    target = destination or user_cache_path("hp-driverctl") / Path(package.download_url).name
    Downloader().download(package.download_url, target, package.sha256)
    typer.echo(str(target))


@app.command()
def install(
    path: Path,
    apply: Annotated[
        bool, typer.Option("--apply", help="Perform the privileged operation.")
    ] = False,
    skip_signature_check: Annotated[
        bool,
        typer.Option("--skip-signature-check", help="Allow unsigned packages (unsafe)."),
    ] = False,
    restore_point: Annotated[
        bool,
        typer.Option("--restore-point/--no-restore-point", help="Create a restore point first."),
    ] = True,
) -> None:
    """Install a staged INF package. Defaults to dry-run."""
    if not apply:
        typer.echo(
            f"dry-run: verify signature; create restore point; pnputil /add-driver {path} /install"
        )
        return
    backend = WindowsDriverBackend(CommandRunner())
    if not skip_signature_check and not backend.verify_authenticode(str(path)):
        raise typer.BadParameter(
            "Authenticode signature is not valid; use --skip-signature-check "
            "only if independently trusted"
        )
    if restore_point:
        backend.create_restore_point("hp-driverctl before driver installation")
    backend.install_inf(str(path))


@app.command()
def remove(
    published_name: str,
    apply: Annotated[bool, typer.Option("--apply", help="Perform driver removal.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm destructive removal.")] = False,
) -> None:
    """Remove an OEM driver package. Defaults to dry-run and requires confirmation."""
    if not apply:
        typer.echo(f"dry-run: pnputil /delete-driver {published_name} /uninstall")
        return
    confirmed = yes or typer.confirm(f"Remove {published_name}? This may disconnect hardware")
    WindowsDriverBackend(CommandRunner()).remove_driver(published_name, confirmed=confirmed)


@app.command()
def software(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
) -> None:
    """Download HP's latest catalog and list current optional software."""
    backend = WindowsDriverBackend(CommandRunner())
    recommendations = SoftwareService(JsonCatalog(_fresh_catalog(backend))).scan(
        backend.installed_software()
    )
    if json_output:
        typer.echo(json.dumps([asdict(item) for item in recommendations], indent=2))
        return
    table = Table("Status", "ID", "Software", "Installed", "Available", "Vendor")
    for item in recommendations:
        table.add_row(
            item.status,
            item.package.id,
            item.package.name,
            item.installed_version or "missing",
            item.available_version or item.package.version,
            item.package.vendor,
        )
    console.print(table)


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


@app.command()
def interactive(
    inventory_file: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    cache_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Refresh HP's catalog and guide package selection and verified download."""
    backend = WindowsDriverBackend(CommandRunner())
    catalog = _fresh_catalog(backend)
    recommendations = [
        item
        for item in DriverService(JsonCatalog(catalog)).scan(_inventory(backend, inventory_file))
        if item.status in {"missing_driver", "update_available"}
    ]
    if not recommendations:
        console.print("No missing or outdated drivers were found.")
        return
    table = Table("#", "Status", "Device", "Package", "Version")
    for number, item in enumerate(recommendations, 1):
        table.add_row(
            str(number),
            item.status,
            item.device.name,
            item.package.name,
            item.available_version,
        )
    console.print(table)
    if not typer.confirm("Download recommended packages?", default=False):
        typer.echo("No changes were made.")
        return
    destination = cache_dir or user_cache_path("hp-driverctl")
    selected = typer.prompt("Package numbers (comma separated, or 'all')", default="all").strip()
    indexes = (
        range(len(recommendations))
        if selected.lower() == "all"
        else [int(value.strip()) - 1 for value in selected.split(",")]
    )
    for index in indexes:
        item = recommendations[index]
        target = destination / Path(item.package.download_url).name
        Downloader().download(item.package.download_url, target, item.package.sha256)
        typer.echo(f"verified: {target}")
    typer.echo("Downloads completed. Review signatures before using install --apply.")


# ---------------------------------------------------------------------------
# SUDF scan commands
# ---------------------------------------------------------------------------




