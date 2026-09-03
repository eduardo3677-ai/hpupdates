# hpupdates

Open-source HP driver/software update CLI — reverse-engineered from HP Support Assistant (SP174448).

## Features

- **Auto-detects everything**: SysID, serial, product number, OS code, PnP devices, BIOS — no manual IDs required
- **SUDF v3 client**: AES-256-CBC API key decryption, AWS SigV4 signing, GetUpdatesBySysId
- **Update detection**: device matching, file version comparison, BIOS date checking, store package verification
- **Download engine**: BITS-style download, MD5/HPSignCheck verification, Authenticode
- **Install engine**: silent (ScheduledTask) and loud (CreateProcessAsUser) install modes
- **Web layer**: 53 HpsaCordovaProxy actions, device management, messages, warranty, health
- **31 CLI commands**: info, update, download-all, scan, install, bios-check, etc.

## Install

```
pip install -r requirements.txt
pip install .
```

## Quick start

```
# Show complete device info + missing drivers + available updates
hpupdates info

# Download and install all needed updates (dry-run first, then --apply)
hpupdates update
hpupdates update --apply

# Download all drivers/software to a folder (no install)
hpupdates download-all ./drivers

# Check for BIOS updates
hpupdates bios-check

# Scan via SUDF API
hpupdates sudf-scan

# Show auto-detected OS code
hpupdates os-code
```

## Commands

| Command | Description |
|---------|-------------|
| `info` | Show complete device info, missing drivers, software, updates |
| `update` | Download + install all needed updates (dry-run / `--apply`) |
| `download-all [dir]` | Download all drivers/software to a folder (no install) |
| `sudf-scan` | Scan for updates via SUDF API (auto-detected) |
| `sudf-scan-json` | Same scan, JSON output |
| `bios-check` | Check if BIOS update available |
| `os-code` | Show auto-detected OS code (WT64_22H2, W11_23H2) |
| `pnp-devices` | List all PnP hardware IDs |
| `softpaq-download SPxxxx` | Download a single SoftPaq |
| `softpaq-install SPxxxx` | Download + install a single SoftPaq |
| `inventory` | Collect PnP hardware and installed driver versions |
| `identify` | Show SMBIOS identity |
| `scan` | Download HPIA catalog and find driver updates |
| `download SPxxxx` | Download from HPIA catalog |
| `install driver.inf` | Install a staged INF |
| `remove oemNN.inf` | Remove an OEM driver |
| `software` | List optional software |
| `interactive` | Guided package selection |
| `sync-catalogs` | Download all HPIA catalog families |
| `doctor` | Check prerequisites |
| `endpoints` | List HP endpoints |
| `health-check` | Battery, storage, cooling scan |
| `warranty` | Warranty status |
| `settings` | Get/set HPSA settings |
| `messages` | List cached messages |
| `solutions` | List solution HTML files |
| `launcher` | Parse/build hpsalauncher:// URLs |

## Architecture

```
src/hpupdates/
├── api/              # Public API (HpupdatesClient)
├── cli/              # CLI commands (modular)
│   ├── app.py        # Main Typer app
│   ├── autodetect.py # Auto-detection of device profile
│   ├── catalog_cmds  # inventory, scan, download, install, software
│   ├── sudf_cmds     # sudf-scan, update, download-all, info, bios-check
│   ├── device_cmds   # os-code, pnp-devices, health-check
│   ├── web_cmds      # warranty, settings, messages, launcher
│   └── system_cmds   # doctor, endpoints
├── core/             # Application services
├── models/           # Domain models
├── utils/            # Shared utilities
├── data/             # Embedded data (osparams.json)
└── infrastructure/
    ├── catalog/      # HPIA catalog download/validation
    ├── windows/      # WMI, PnP, BIOS, pnputil
    ├── sudf/         # SUDF v3 client
    ├── installer/    # SoftPaq download/install engine
    ├── web/          # HpsaCordovaProxy web layer
    ├── endpoints.py  # HP endpoint registry
    ├── os_params.py  # OS code generation
    └── update_detector.py
```

## Requirements

- Windows 10/11 on an HP device
- Python 3.11+
- PowerShell 5.1+
- See `requirements.txt`

## Testing

```
pytest
ruff check src/
```

## License

Apache-2.0. HP, HP Support Assistant, HP Image Assistant and SoftPaq are trademarks of their respective owners. This project is not affiliated with or endorsed by HP.
