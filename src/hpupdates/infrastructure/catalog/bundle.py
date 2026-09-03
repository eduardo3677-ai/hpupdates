from __future__ import annotations


import hashlib
import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from hpupdates.models.models import MachineProfile
from hpupdates.infrastructure.endpoints import require_operational_endpoint
from hpupdates.infrastructure.catalog.hp_catalog import (
    HpCatalogError,
    HpCatalogNotFoundError,
    HpImageAssistantCatalogProvider,
    HpPlatform,
)


@dataclass(frozen=True, slots=True)
class CatalogArtifact:
    kind: str
    url: str
    path: str
    required: bool
    checksum: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogBundleManifest:
    system_id: str
    architecture: str
    os_build: str
    artifacts: tuple[CatalogArtifact, ...]
    optional_missing: tuple[str, ...]


class HpCatalogBundleProvider:
    """Synchronize the distinct public catalog families used by HPIA."""


    PRODUCT_UPDATE_URL = (
        require_operational_endpoint("hpia_product_catalog").url + "ProductCatalogUpdate.xml"
    )
    COMMON_KB_URL = require_operational_endpoint("hpia_knowledge_base").url + "common/latest.cab"
    PREVIOUS_KB_URL = (
        require_operational_endpoint("hpia_knowledge_base").url + "common/previous.cab"
    )

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cab_extractor: object | None = None,
        max_download_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        self.catalog = HpImageAssistantCatalogProvider(
            client=client,
            cab_extractor=cab_extractor,  # type: ignore[arg-type]
            max_download_bytes=max_download_bytes,
        )

    def sync(self, profile: MachineProfile, output_dir: Path) -> CatalogBundleManifest:
        if profile.manufacturer.strip().casefold() not in self.catalog.HP_MANUFACTURERS:
            raise HpCatalogError("official HP catalogs require an HP-manufactured computer")
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=output_dir.name + ".staging-", dir=output_dir.parent
        ) as directory:
            staging = Path(directory) / "bundle"
            staging.mkdir()
            manifest = self._sync_to(profile, staging)
            backup = output_dir.with_name(output_dir.name + ".previous")
            if backup.exists():
                shutil.rmtree(backup)
            if output_dir.exists():
                output_dir.replace(backup)
            try:
                staging.replace(output_dir)
            except Exception:
                if backup.exists() and not output_dir.exists():
                    backup.replace(output_dir)
                raise
            if backup.exists():
                shutil.rmtree(backup)
            return self._relocate_manifest(manifest, output_dir)

    def _sync_to(self, profile: MachineProfile, output_dir: Path) -> CatalogBundleManifest:
        artifacts: list[CatalogArtifact] = []
        optional_missing: list[str] = []

        platform_cab = self.catalog._download(self.catalog.PLATFORM_URL)
        platform_xml = self.catalog.cab_extractor(platform_cab)
        platform = self.catalog._select_platform(platform_xml, profile)
        platform_path = output_dir / "platformList.cab"
        self._save(platform_path, platform_cab)
        artifacts.append(
            self._artifact("platform-list", self.catalog.PLATFORM_URL, platform_path, True)
        )

        reference_url = self.catalog._reference_url(platform)
        reference_cab = self.catalog._download(reference_url)
        reference_path = output_dir / Path(urlparse(reference_url).path).name
        self._save(reference_path, reference_cab)
        artifacts.append(self._artifact("reference", reference_url, reference_path, True))

        cds_url = self._cds_url(platform)
        try:
            cds_cab = self.catalog._download(cds_url)
        except HpCatalogNotFoundError:
            optional_missing.append("cds")
        else:
            cds_path = output_dir / Path(urlparse(cds_url).path).name
            self._save(cds_path, cds_cab)
            artifacts.append(self._artifact("cds", cds_url, cds_path, False))

        common_kb_url = self.COMMON_KB_URL
        try:
            common_kb = self.catalog._download(common_kb_url)
        except HpCatalogNotFoundError:
            common_kb_url = self.PREVIOUS_KB_URL
            common_kb = self.catalog._download(common_kb_url)
        common_kb_path = output_dir / "kb-common.cab"
        self._save(common_kb_path, common_kb)
        artifacts.append(
            self._artifact("knowledge-base-common", common_kb_url, common_kb_path, True)
        )

        platform_kb_url = self._platform_kb_url(platform)
        try:
            platform_kb = self.catalog._download(platform_kb_url)
        except HpCatalogNotFoundError:
            optional_missing.append("knowledge-base-platform")
        else:
            platform_kb_path = output_dir / "kb-platform.cab"
            self._save(platform_kb_path, platform_kb)
            artifacts.append(
                self._artifact("knowledge-base-platform", platform_kb_url, platform_kb_path, False)
            )

        update_xml = self.catalog._download(self.PRODUCT_UPDATE_URL)
        update_path = output_dir / "ProductCatalogUpdate.xml"
        self._save(update_path, update_xml)
        artifacts.append(
            self._artifact("product-catalog-index", self.PRODUCT_UPDATE_URL, update_path, True)
        )
        product_url, expected_md5 = self._parse_product_update(update_xml)
        product_zip = self.catalog._download(product_url)
        actual_md5 = hashlib.md5(product_zip, usedforsecurity=False).hexdigest()
        if actual_md5.casefold() != expected_md5.casefold():
            raise HpCatalogError("HP product catalog checksum verification failed")
        product_path = output_dir / "ProductCatalog.zip"
        self._save(product_path, product_zip)
        artifacts.append(
            self._artifact("product-catalog", product_url, product_path, True, actual_md5)
        )

        manifest = CatalogBundleManifest(
            system_id=profile.system_id,
            architecture=profile.os_architecture,
            os_build=profile.os_build,
            artifacts=tuple(artifacts),
            optional_missing=tuple(optional_missing),
        )
        self._save(output_dir / "manifest.json", json.dumps(asdict(manifest), indent=2).encode())
        return manifest

    @staticmethod
    def _relocate_manifest(
        manifest: CatalogBundleManifest, output_dir: Path
    ) -> CatalogBundleManifest:
        return CatalogBundleManifest(
            manifest.system_id,
            manifest.architecture,
            manifest.os_build,
            tuple(
                CatalogArtifact(
                    item.kind,
                    item.url,
                    str(output_dir / Path(item.path).name),
                    item.required,
                    item.checksum,
                )
                for item in manifest.artifacts
            ),
            manifest.optional_missing,
        )

    @staticmethod
    def _artifact(
        kind: str, url: str, path: Path, required: bool, checksum: str | None = None
    ) -> CatalogArtifact:
        return CatalogArtifact(kind, url, str(path), required, checksum)

    @classmethod
    def _cds_url(cls, platform: HpPlatform) -> str:
        system_id = platform.system_id.lower()
        return f"{cls.catalog_base()}ref/{system_id}/{system_id}_cds.cab"

    @classmethod
    def _platform_kb_url(cls, platform: HpPlatform) -> str:
        reference_path = urlparse(
            HpImageAssistantCatalogProvider._reference_url(platform)
        ).path.removeprefix("/ref/")
        kb_base = require_operational_endpoint("hpia_knowledge_base").url
        return f"{kb_base}sysids/{reference_path}"

    @staticmethod
    def catalog_base() -> str:
        return HpImageAssistantCatalogProvider.BASE_URL.removesuffix("ref/")

    @classmethod
    def _parse_product_update(cls, xml_bytes: bytes) -> tuple[str, str]:
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise HpCatalogError("HP product catalog index is invalid XML") from exc
        catalog = root.find("ProductCatalog")
        if catalog is None:
            raise HpCatalogError("HP product catalog index has no ProductCatalog")
        raw_url = (catalog.findtext("CatalogUrl") or "").strip()
        checksum = (catalog.findtext("CatalogMd5") or "").strip()
        parsed = urlparse(raw_url)
        if parsed.hostname != "hpia.hpcloud.hp.com":
            raise HpCatalogError("HP product catalog URL points to an unexpected host")
        if not parsed.path.startswith("/productcatalog/"):
            raise HpCatalogError("HP product catalog URL has an unexpected path")
        if len(checksum) != 32 or any(
            character not in "0123456789abcdefABCDEF" for character in checksum
        ):
            raise HpCatalogError("HP product catalog index has an invalid checksum")
        return "https://hpia.hpcloud.hp.com" + parsed.path, checksum

    @staticmethod
    def _save(path: Path, content: bytes) -> None:
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(content)
        temporary.replace(path)
