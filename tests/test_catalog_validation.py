import json
from pathlib import Path

import pytest

from hpupdates.infrastructure.catalog.validator import CatalogError, JsonCatalog


def test_catalog_rejects_insecure_download_url(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "packages": [
                    {
                        "id": "x",
                        "name": "X",
                        "version": "1",
                        "vendor": "HP",
                        "category": "driver",
                        "download_url": "http://example.test/x.exe",
                        "hardware_ids": ["PCI\\VEN_1"],
                        "sha256": "a" * 64,
                    }
                ],
            }
        )
    )
    with pytest.raises(CatalogError, match="HTTPS"):
        JsonCatalog(path).packages()


def test_catalog_rejects_invalid_sha256(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "packages": [
                    {
                        "id": "x",
                        "name": "X",
                        "version": "1",
                        "vendor": "HP",
                        "category": "driver",
                        "download_url": "https://ftp.hp.com/x.exe",
                        "hardware_ids": ["PCI\\VEN_1"],
                        "sha256": "unknown",
                    }
                ],
            }
        )
    )
    with pytest.raises(CatalogError, match="SHA-256"):
        JsonCatalog(path).packages()


def test_catalog_rejects_unapproved_https_package_host(tmp_path: Path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "packages": [
                    {
                        "id": "x",
                        "name": "X",
                        "version": "1",
                        "vendor": "HP",
                        "category": "driver",
                        "download_url": "https://sudf-api.hpcloud.hp.com/x.exe",
                        "hardware_ids": ["PCI\\VEN_1"],
                        "sha256": "a" * 64,
                    }
                ],
            }
        )
    )
    with pytest.raises(CatalogError, match="approved"):
        JsonCatalog(path).packages()
