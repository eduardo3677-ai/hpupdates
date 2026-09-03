"""Catalog subpackage — HPIA catalog download, validation, and multi-catalog sync."""

from hpupdates.infrastructure.catalog.hp_catalog import (
    HpCatalogError,
    HpCatalogNotFoundError,
    HpPlatform,
    HpImageAssistantCatalogProvider,
)
from hpupdates.infrastructure.catalog.validator import JsonCatalog, CatalogError
from hpupdates.infrastructure.catalog.bundle import (
    CatalogArtifact,
    CatalogBundleManifest,
    HpCatalogBundleProvider,
)

__all__ = [
    "HpCatalogError",
    "HpCatalogNotFoundError",
    "HpPlatform",
    "HpImageAssistantCatalogProvider",
    "JsonCatalog",
    "CatalogError",
    "CatalogArtifact",
    "CatalogBundleManifest",
    "HpCatalogBundleProvider",
]
