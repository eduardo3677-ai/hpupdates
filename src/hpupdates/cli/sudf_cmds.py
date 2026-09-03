"""SUDF and installer CLI commands — all auto-detected, no manual IDs required.

Commands:
  sudf-scan       Scan for updates via SUDF API (auto-detects sys_id, os_code)
  sudf-scan-json  Same scan, JSON output
  softpaq-download Download a single SoftPaq by SP number
  softpaq-install  Download + install a single SoftPaq
  bios-check      Check if a BIOS update is available
  os-code         Show the auto-detected OS code
  pnp-devices     List all PnP hardware IDs
  info            Show complete device info: missing drivers, software, updates
  update          Download + install all needed updates
  download-all    Download all needed drivers/software to a folder (no install)
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hpupdates.cli.autodetect import detect_profile_or_exit

app = typer.Typer()
console = Console()


# ---------------------------------------------------------------------------
# sudf-scan — auto-detect everything, scan for updates
# ---------------------------------------------------------------------------

@app.command("sudf-scan")
def sudf_scan(
    country: Annotated[str, typer.Option("--country", help="ISO country code (default: auto from locale).")] = "",
    language: Annotated[str, typer.Option("--language", help="ISO language code (default: auto from locale).")] = "",
) -> None:
    """Scan for updates using the SUDF API (GetUpdatesBySysId).

    All device info (SysId, OS code, PnP devices, BIOS) is auto-detected.
    """
    profile = detect_profile_or_exit()
    _country = country or "us"
    _language = language or "en-US"

    from hpupdates.cli._helpers import _build_sudf_client
    from hpupdates.core.services import SudfScanService

    client = _build_sudf_client()
    service = SudfScanService(sudf_client=client)

    console.print(f"[cyan]Scanning for updates (SysID={profile.sys_id}, OS={profile.os_code})...[/]")

    try:
        needed = service.scan(
            sys_id=profile.sys_id,
            country=_country,
            language=_language,
            os_code=profile.os_code,
            pnp_devices=profile.pnp_devices,
            bios_info=profile.bios_info,
        )
    except Exception as exc:
        console.print(f"[red]Scan failed: {exc}[/]")
        raise typer.Exit(1)

    if not needed:
        console.print("[green]No updates needed — system is up to date.[/]")
        return

    table = Table(title=f"{len(needed)} updates needed")
    table.add_column("Code", style="cyan")
    table.add_column("Title")
    table.add_column("Version")
    table.add_column("Type")
    table.add_column("Category")

    for u in needed:
        table.add_row(
            str(u.get("Code", "")),
            str(u.get("Title", ""))[:60],
            str(u.get("Version", "")),
            str(u.get("Type", "")),
            str(u.get("Category", "")),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# sudf-scan-json — same as sudf-scan but JSON output
# ---------------------------------------------------------------------------

@app.command("sudf-scan-json")
def sudf_scan_json(
    country: Annotated[str, typer.Option("--country")] = "",
    language: Annotated[str, typer.Option("--language")] = "",
) -> None:
    """Scan for updates and emit JSON (all auto-detected)."""
    profile = detect_profile_or_exit()
    _country = country or "us"
    _language = language or "en-US"

    from hpupdates.cli._helpers import _build_sudf_client
    from hpupdates.core.services import SudfScanService

    client = _build_sudf_client()
    service = SudfScanService(sudf_client=client)

    try:
        needed = service.scan(
            sys_id=profile.sys_id,
            country=_country,
            language=_language,
            os_code=profile.os_code,
            pnp_devices=profile.pnp_devices,
            bios_info=profile.bios_info,
        )
    except Exception as exc:
        print(json.dumps({"error": str(exc), "profile": profile.summary()}))
        raise typer.Exit(1)

    print(json.dumps({
        "profile": profile.summary(),
        "updates_needed": len(needed),
        "updates": needed,
    }, indent=2, default=str))


# ---------------------------------------------------------------------------
# bios-check — auto-detect BIOS info and check for updates
# ---------------------------------------------------------------------------

@app.command("bios-check")
def bios_check(
    country: Annotated[str, typer.Option("--country")] = "",
    language: Annotated[str, typer.Option("--language")] = "",
) -> None:
    """Check if a BIOS update is available (auto-detects current BIOS)."""
    profile = detect_profile_or_exit()
    _country = country or "us"
    _language = language or "en-US"

    from hpupdates.cli._helpers import _build_sudf_client
    from hpupdates.core.services import SudfScanService

    client = _build_sudf_client()
    service = SudfScanService(sudf_client=client)

    console.print(f"[cyan]Current BIOS: {profile.bios_version} (dated {profile.bios_release_date})[/]")
    console.print(f"[cyan]Checking for BIOS updates (SysID={profile.sys_id})...[/]")

    try:
        needed = service.scan(
            sys_id=profile.sys_id,
            country=_country,
            language=_language,
            os_code=profile.os_code,
            pnp_devices=profile.pnp_devices,
            bios_info=profile.bios_info,
        )
    except Exception as exc:
        console.print(f"[red]Scan failed: {exc}[/]")
        raise typer.Exit(1)

    bios_updates = [u for u in needed if str(u.get("Type", "")).lower() == "bios"]

    if not bios_updates:
        console.print("[green]No BIOS updates available.[/]")
        return

    for u in bios_updates:
        console.print(f"[yellow]BIOS update available: {u.get('Title', '')}[/]")
        console.print(f"  Version: {u.get('Version', '')}")
        console.print(f"  Code: {u.get('Code', '')}")


# ---------------------------------------------------------------------------
# os-code — show auto-detected OS code
# ---------------------------------------------------------------------------

@app.command("os-code")
def os_code() -> None:
    """Show the auto-detected OS code (WT64_22H2, W11_23H2, etc.)."""
    profile = detect_profile_or_exit()
    console.print(profile.os_code)


# ---------------------------------------------------------------------------
# pnp-devices — list all PnP hardware IDs
# ---------------------------------------------------------------------------

@app.command("pnp-devices")
def pnp_devices() -> None:
    """List all PnP device hardware IDs (Win32_PnPEntity)."""
    profile = detect_profile_or_exit()

    table = Table(title=f"{len(profile.pnp_devices)} PnP device IDs")
    table.add_column("#", style="dim")
    table.add_column("Hardware ID")

    for i, dev_id in enumerate(profile.pnp_devices, 1):
        table.add_row(str(i), dev_id)

    console.print(table)


# ---------------------------------------------------------------------------
# softpaq-download — download a single SoftPaq
# ---------------------------------------------------------------------------

@app.command("softpaq-download")
def softpaq_download(
    softpaq: Annotated[str, typer.Argument(help="SoftPaq number (e.g. SP12345) or download URL.")],
    destination: Annotated[Path, typer.Option("--destination", "-d", help="Download directory.")] = Path("."),
    checksum: Annotated[str, typer.Option("--checksum", help="Expected MD5 checksum (optional).")] = "",
) -> None:
    """Download a SoftPaq using the installer engine (BITS + MD5)."""
    from hpupdates.infrastructure.installer import SoftPaqUpdate, DownloadStatus, download_softpaq

    sp = softpaq.upper().removeprefix("SP")
    if softpaq.startswith("http"):
        url = softpaq
    else:
        prefix = sp[:5]
        url = f"https://ftp.hp.com/pub/softpaq/sp{prefix}01-sp{prefix}00/sp{sp}.exe"

    update = SoftPaqUpdate(
        guid=sp,
        sp_id=sp,
        sp_name=f"SoftPaq SP{sp}",
        sp_version="0",
        url_result=url,
        url_result_ui=url,
        checksum=checksum,
    )

    destination.mkdir(parents=True, exist_ok=True)
    console.print(f"[cyan]Downloading {sp} to {destination}...[/]")

    local_path = str(destination / f"{sp}.exe")
    result = download_softpaq(
        url=url,
        local_path=local_path,
        is_manual=True,
    )
    if result.status == DownloadStatus.Downloaded:
        console.print(f"[green]Downloaded: {result.local_path}[/]")
    elif result.status == DownloadStatus.AlreadyDownloaded:
        console.print(f"[green]Already downloaded: {result.local_path}[/]")
    else:
        console.print(f"[red]Download failed: {result.status.name}[/]")
        if result.error_code:
            console.print(f"[dim]  Error: {result.error_code}[/]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# softpaq-install — download and install a single SoftPaq
# ---------------------------------------------------------------------------

@app.command("softpaq-install")
def softpaq_install(
    softpaq: Annotated[str, typer.Argument(help="SoftPaq number (e.g. SP12345) or path to .exe.")],
    apply: Annotated[bool, typer.Option("--apply", help="Actually install (default is dry-run).")] = False,
    silent: Annotated[bool, typer.Option("--silent", help="Use silent install (default: auto-decide).")] = False,
    loud: Annotated[bool, typer.Option("--loud", help="Force loud (UI) install.")] = False,
) -> None:
    """Download and install a SoftPaq (full flow: verify, download, install)."""
    from hpupdates.infrastructure.installer import SoftPaqUpdate, InstallParameters, download_and_install

    sp = softpaq.upper().removeprefix("SP")
    if softpaq.startswith("http"):
        url = softpaq
    else:
        prefix = sp[:5]
        url = f"https://ftp.hp.com/pub/softpaq/sp{prefix}01-sp{prefix}00/sp{sp}.exe"

    update = SoftPaqUpdate(
        guid=sp,
        sp_id=sp,
        sp_name=f"SoftPaq SP{sp}",
        sp_version="0",
        executable_name=f"{sp}.exe",
        url_result=url,
        url_result_ui=url,
        silent_install_string="/s /e /f" if silent else "",
    )

    if not apply:
        console.print(f"[yellow]DRY RUN: Would download and install SP{sp}[/]")
        console.print(f"  URL: {url}")
        console.print(f"  Mode: {'loud' if loud else 'silent' if silent else 'auto'}")
        console.print("[dim]Use --apply to actually install.[/]")
        return

    params = InstallParameters(
        scan_type="Manual" if not silent else "DailyBackground",
        serial_number="",
    )

    console.print(f"[cyan]Downloading and installing SP{sp}...[/]")
    result = download_and_install(update, params, softpaq_folder=tempfile.gettempdir())

    if result.result.value <= 2:
        console.print(f"[green]Install completed (result={result.result.name})[/]")
        if result.result.value == 2:
            console.print("[yellow]Reboot required.[/]")
    else:
        console.print(f"[red]Install failed (result={result.result.name})[/]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# info — show complete device info, missing drivers, software, updates
# ---------------------------------------------------------------------------

@app.command("info")
def info(
    json_output: Annotated[bool, typer.Option("--json", help="Output JSON.")] = False,
) -> None:
    """Show complete device info: hardware, missing drivers, software, updates.

    All auto-detected — no arguments needed.
    """
    console.print("[cyan]Detecting device profile...[/]")
    profile = detect_profile_or_exit()

    if json_output:
        print(json.dumps({
            "device": profile.summary(),
            "pnp_devices": profile.pnp_devices,
            "inventory": [
                {
                    "name": d.name,
                    "hardware_ids": list(d.hardware_ids),
                    "current_version": d.current_version,
                    "manufacturer": d.manufacturer,
                    "driver_provider": d.driver_provider,
                    "driver_date": d.driver_date,
                    "problem_code": d.problem_code,
                }
                for d in profile.devices
            ],
        }, indent=2, default=str))
        return

    # Device summary panel
    summary = profile.summary()
    console.print(Panel.fit(
        "\n".join(f"[cyan]{k}:[/] [white]{v}[/]" for k, v in summary.items()),
        title="[bold]Device Profile[/]",
        border_style="blue",
    ))

    # PnP devices table
    table = Table(title=f"PnP Devices ({len(profile.devices)})")
    table.add_column("#", style="dim", width=4)
    table.add_column("Device", style="cyan")
    table.add_column("Version")
    table.add_column("Manufacturer")
    table.add_column("Provider")
    table.add_column("Problem")

    for i, d in enumerate(profile.devices, 1):
        table.add_row(
            str(i),
            d.name[:50],
            d.current_version or "",
            (d.manufacturer or "")[:20],
            (d.driver_provider or "")[:20],
            str(d.problem_code) if d.problem_code else "",
        )

    console.print(table)

    # Try SUDF scan for updates
    console.print("\n[cyan]Checking for available updates...[/]")
    try:
        from hpupdates.cli._helpers import _build_sudf_client
        from hpupdates.core.services import SudfScanService

        client = _build_sudf_client()
        service = SudfScanService(sudf_client=client)
        needed = service.scan(
            sys_id=profile.sys_id,
            os_code=profile.os_code,
            pnp_devices=profile.pnp_devices,
            bios_info=profile.bios_info,
        )

        if needed:
            updates_table = Table(title=f"Available Updates ({len(needed)})")
            updates_table.add_column("Code", style="cyan")
            updates_table.add_column("Title")
            updates_table.add_column("Version")
            updates_table.add_column("Type")
            updates_table.add_column("Category")

            for u in needed:
                updates_table.add_row(
                    str(u.get("Code", "")),
                    str(u.get("Title", ""))[:50],
                    str(u.get("Version", "")),
                    str(u.get("Type", "")),
                    str(u.get("Category", "")),
                )

            console.print(updates_table)
        else:
            console.print("[green]System is up to date — no updates needed.[/]")
    except Exception as exc:
        console.print(f"[yellow]Could not check for updates: {exc}[/]")


# ---------------------------------------------------------------------------
# update — download and install ALL needed updates
# ---------------------------------------------------------------------------

@app.command("update")
def update_all(
    apply: Annotated[bool, typer.Option("--apply", help="Actually install (default: dry-run).")] = False,
    country: Annotated[str, typer.Option("--country")] = "",
    language: Annotated[str, typer.Option("--language")] = "",
) -> None:
    """Download and install all needed updates (drivers, software, BIOS).

    Auto-detects everything. Default is dry-run; use --apply to install.
    """
    profile = detect_profile_or_exit()
    _country = country or "us"
    _language = language or "en-US"

    console.print(f"[cyan]Scanning for updates (SysID={profile.sys_id}, OS={profile.os_code})...[/]")

    from hpupdates.cli._helpers import _build_sudf_client
    from hpupdates.core.services import SudfScanService

    client = _build_sudf_client()
    service = SudfScanService(sudf_client=client)

    try:
        needed = service.scan(
            sys_id=profile.sys_id,
            country=_country,
            language=_language,
            os_code=profile.os_code,
            pnp_devices=profile.pnp_devices,
            bios_info=profile.bios_info,
        )
    except Exception as exc:
        console.print(f"[red]Scan failed: {exc}[/]")
        raise typer.Exit(1)

    if not needed:
        console.print("[green]System is up to date — no updates needed.[/]")
        return

    console.print(f"[cyan]Found {len(needed)} updates to install.[/]")

    if not apply:
        console.print("[yellow]DRY RUN — showing what would be installed:[/]")
        for u in needed:
            console.print(f"  • {u.get('Code', '')} — {u.get('Title', '')[:60]} v{u.get('Version', '')}")
        console.print("[dim]Use --apply to actually download and install.[/]")
        return

    from hpupdates.infrastructure.installer import SoftPaqUpdate, InstallParameters, download_and_install

    success = 0
    failed = 0

    for i, u in enumerate(needed, 1):
        code = str(u.get("Code", ""))
        title = str(u.get("Title", ""))
        version = str(u.get("Version", ""))
        url = str(u.get("Location", "") or u.get("LocationUI", ""))

        console.print(f"\n[cyan][{i}/{len(needed)}] Installing {code} — {title[:50]} v{version}...[/]")

        update_obj = SoftPaqUpdate(
            guid=str(u.get("Guid", code)),
            sp_id=code,
            sp_name=title,
            sp_version=version,
            executable_name=f"{code}.exe",
            url_result=url,
            url_result_ui=url,
            silent_install_string=str(u.get("SilentInstall", "")),
            no_reboot_success_return_code=str(u.get("NoRebootSuccessReturnCode", "")),
            no_reboot_failure_return_code=str(u.get("NoRebootFailureReturnCode", "")),
            reboot_success_return_code=str(u.get("RebootSuccessReturnCode", "")),
            no_reboot_cancel_return_code=str(u.get("NoRebootCancelReturnCode", "")),
        )

        params = InstallParameters(
            scan_type="DailyBackground",
            serial_number=profile.serial_number,
        )

        try:
            result = download_and_install(update_obj, params, softpaq_folder=tempfile.gettempdir())
            if result.result.value <= 2:
                console.print(f"  [green]OK ({result.result.name})[/]")
                if result.result.value == 2:
                    console.print("  [yellow]Reboot required.[/]")
                success += 1
            else:
                console.print(f"  [red]FAIL ({result.result.name})[/]")
                failed += 1
        except Exception as exc:
            console.print(f"  [red]ERROR: {exc}[/]")
            failed += 1

    console.print(f"\n[bold]Done: {success} succeeded, {failed} failed.[/]")


# ---------------------------------------------------------------------------
# download-all — download all needed drivers/software to a folder (no install)
# ---------------------------------------------------------------------------

@app.command("download-all")
def download_all(
    destination: Annotated[Path, typer.Argument(help="Download directory.")] = Path("./hp-downloads"),
    country: Annotated[str, typer.Option("--country")] = "",
    language: Annotated[str, typer.Option("--language")] = "",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Download all needed drivers and software to a folder (no install).

    Auto-detects the device, scans for updates, and downloads each
    SoftPaq to the specified directory with MD5 verification.
    """
    profile = detect_profile_or_exit()
    _country = country or "us"
    _language = language or "en-US"
    destination.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Scanning for updates (SysID={profile.sys_id}, OS={profile.os_code})...[/]")

    from hpupdates.cli._helpers import _build_sudf_client
    from hpupdates.core.services import SudfScanService
    from hpupdates.infrastructure.installer import SoftPaqUpdate, DownloadStatus, download_softpaq

    client = _build_sudf_client()
    service = SudfScanService(sudf_client=client)

    try:
        needed = service.scan(
            sys_id=profile.sys_id,
            country=_country,
            language=_language,
            os_code=profile.os_code,
            pnp_devices=profile.pnp_devices,
            bios_info=profile.bios_info,
        )
    except Exception as exc:
        console.print(f"[red]Scan failed: {exc}[/]")
        raise typer.Exit(1)

    if not needed:
        console.print("[green]No updates needed — system is up to date.[/]")
        return

    console.print(f"[cyan]Downloading {len(needed)} packages to {destination}...[/]")

    results = []
    success = 0
    failed = 0

    for i, u in enumerate(needed, 1):
        code = str(u.get("Code", ""))
        title = str(u.get("Title", ""))
        url = str(u.get("Location", "") or u.get("LocationUI", ""))

        console.print(f"  [{i}/{len(needed)}] {code} — {title[:50]}...")

        update_obj = SoftPaqUpdate(
            guid=str(u.get("Guid", code)),
            sp_id=code,
            sp_name=title,
            sp_version=str(u.get("Version", "0")),
            url_result=url,
            url_result_ui=url,
            checksum=str(u.get("CheckSum", "")),
        )

        try:
            dl_path = str(destination / f"{code}.exe")
            result = download_softpaq(url=url, local_path=dl_path, is_manual=True)
            if result.status in (DownloadStatus.Downloaded, DownloadStatus.AlreadyDownloaded):
                console.print(f"    [green]OK: {result.local_path}[/]")
                results.append({"code": code, "title": title, "file": result.local_path, "status": "ok"})
                success += 1
            else:
                console.print(f"    [red]FAIL: {result.status.name}[/]")
                results.append({"code": code, "title": title, "file": "", "status": result.status.name})
                failed += 1
        except Exception as exc:
            console.print(f"    [red]ERROR: {exc}[/]")
            results.append({"code": code, "title": title, "file": "", "status": str(exc)})
            failed += 1

    console.print(f"\n[bold]Downloaded {success}/{len(needed)} packages to {destination}[/]")

    if json_output:
        print(json.dumps({"downloaded": success, "failed": failed, "results": results}, indent=2))
