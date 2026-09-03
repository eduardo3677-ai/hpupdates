from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from hpupdates.cli import app
from hpupdates.models.models import MachineProfile
from hpupdates.infrastructure.catalog.bundle import (
    CatalogBundleManifest,
    HpCatalogBundleProvider,
)

PLATFORM_XML = b"""<ImagePal>
<Platform><ProductName>HP Test</ProductName><SystemFamily>X</SystemFamily>
<SystemID>0266</SystemID><OS><OSVersion>10.0</OSVersion>
<OSArchitecture>64</OSArchitecture><OSReleaseIdFilename>1803</OSReleaseIdFilename>
<OSReleaseId>1803</OSReleaseId><OSReleaseIdDisplay>1803</OSReleaseIdDisplay>
<OSBuildId>17134</OSBuildId><IsWindows11>false</IsWindows11>
</OS></Platform></ImagePal>"""
PRODUCT_ZIP = b"product-catalog"
PRODUCT_MD5 = hashlib.md5(PRODUCT_ZIP, usedforsecurity=False).hexdigest().upper()
PRODUCT_UPDATE = f"""<NewDataSet>
<ProductCatalog CatalogVersion="1" SchemaVersion="1" ToolVersion="1"
 DateReleased="2026-09-03">
<CatalogUrl>http://hpia.hpcloud.hp.com/productcatalog/ProductCatalog.zip</CatalogUrl>
<CatalogMd5>{PRODUCT_MD5}</CatalogMd5>
<ToolUrl>http://hpia.hpcloud.hp.com/productcatalog/tool.exe</ToolUrl>
<DelSPList /></ProductCatalog></NewDataSet>""".encode()


def _profile() -> MachineProfile:
    return MachineProfile(
        manufacturer="HP",
        model="HP Test",
        product_number="X",
        serial_number="Y",
        system_id="0266",
        system_family="X",
        os_caption="Windows 10",
        os_version="10.0.17134",
        os_build="17134",
        os_architecture="64",
        edition_id="Professional",
        display_version="1803",
    )


def test_sync_downloads_all_public_catalog_types_and_records_optional_missing(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url.endswith("platformList.cab"):
            return httpx.Response(200, content=PLATFORM_XML)
        if url.endswith("0266_64_10.0.1803.cab") and "/ref/" in url:
            return httpx.Response(200, content=b"reference")
        if url.endswith("0266_cds.cab"):
            return httpx.Response(200, content=b"cds")
        if url.endswith("/kb/common/latest.cab"):
            return httpx.Response(200, content=b"common-kb")
        if "/kb/sysids/" in url:
            return httpx.Response(404)
        if url.endswith("ProductCatalogUpdate.xml"):
            return httpx.Response(200, content=PRODUCT_UPDATE)
        if url.endswith("ProductCatalog.zip"):
            return httpx.Response(200, content=PRODUCT_ZIP)
        raise AssertionError(url)

    provider = HpCatalogBundleProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cab_extractor=lambda content: content,
    )
    manifest = provider.sync(_profile(), tmp_path)

    assert {item.kind for item in manifest.artifacts} == {
        "platform-list",
        "reference",
        "cds",
        "knowledge-base-common",
        "product-catalog-index",
        "product-catalog",
    }
    assert manifest.optional_missing == ("knowledge-base-platform",)
    assert (tmp_path / "ProductCatalog.zip").read_bytes() == PRODUCT_ZIP
    assert all(url.startswith("https://hpia.hpcloud.hp.com/") for url in calls)
    assert any(url.endswith("/kb/sysids/0266/0266_64_10.0.1803.cab") for url in calls)


def test_product_catalog_checksum_mismatch_fails_closed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("ProductCatalogUpdate.xml"):
            return httpx.Response(200, content=PRODUCT_UPDATE)
        return httpx.Response(200, content=b"tampered")

    provider = HpCatalogBundleProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cab_extractor=lambda content: PLATFORM_XML,
    )

    try:
        provider.sync(_profile(), tmp_path)
    except Exception as exc:
        assert "checksum" in str(exc).casefold()
    else:
        raise AssertionError("tampered product catalog was accepted")


def test_product_catalog_url_must_remain_on_public_hpia_host(tmp_path: Path) -> None:
    malicious = PRODUCT_UPDATE.replace(
        b"http://hpia.hpcloud.hp.com/productcatalog/ProductCatalog.zip",
        b"https://internal.example/ProductCatalog.zip",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("platformList.cab"):
            return httpx.Response(200, content=PLATFORM_XML)
        if url.endswith("ProductCatalogUpdate.xml"):
            return httpx.Response(200, content=malicious)
        if "/kb/sysids/" in url:
            return httpx.Response(404)
        return httpx.Response(200, content=b"catalog")

    provider = HpCatalogBundleProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cab_extractor=lambda content: content,
    )

    try:
        provider.sync(_profile(), tmp_path)
    except Exception as exc:
        assert "host" in str(exc).casefold()
    else:
        raise AssertionError("unexpected catalog host was accepted")


def test_sync_preserves_previous_bundle_when_late_download_fails(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    (output / "manifest.json").write_text("old", encoding="utf-8")
    (output / "platformList.cab").write_bytes(b"old-platform")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("platformList.cab"):
            return httpx.Response(200, content=PLATFORM_XML)
        if url.endswith("ProductCatalog.zip"):
            return httpx.Response(200, content=b"tampered")
        if url.endswith("ProductCatalogUpdate.xml"):
            return httpx.Response(200, content=PRODUCT_UPDATE)
        if "/kb/sysids/" in url:
            return httpx.Response(404)
        return httpx.Response(200, content=b"catalog")

    provider = HpCatalogBundleProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cab_extractor=lambda content: content,
    )
    with pytest.raises(Exception, match="checksum"):
        provider.sync(_profile(), output)

    assert (output / "manifest.json").read_text(encoding="utf-8") == "old"
    assert (output / "platformList.cab").read_bytes() == b"old-platform"


def test_cds_not_found_is_optional_and_common_kb_uses_previous(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("platformList.cab"):
            return httpx.Response(200, content=PLATFORM_XML)
        if url.endswith("0266_cds.cab") or url.endswith("/kb/common/latest.cab"):
            return httpx.Response(404)
        if "/kb/sysids/" in url:
            return httpx.Response(404)
        if url.endswith("ProductCatalogUpdate.xml"):
            return httpx.Response(200, content=PRODUCT_UPDATE)
        if url.endswith("ProductCatalog.zip"):
            return httpx.Response(200, content=PRODUCT_ZIP)
        return httpx.Response(200, content=b"catalog")

    provider = HpCatalogBundleProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cab_extractor=lambda content: content,
    )
    manifest = provider.sync(_profile(), tmp_path / "bundle")

    assert set(manifest.optional_missing) == {"cds", "knowledge-base-platform"}
    common = next(item for item in manifest.artifacts if item.kind == "knowledge-base-common")
    assert common.url.endswith("/kb/common/previous.cab")


def test_platform_kb_server_failure_is_not_hidden_as_optional(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("platformList.cab"):
            return httpx.Response(200, content=PLATFORM_XML)
        if "/kb/sysids/" in url:
            return httpx.Response(503)
        return httpx.Response(200, content=b"catalog")

    provider = HpCatalogBundleProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cab_extractor=lambda content: content,
    )
    with pytest.raises(Exception, match="latest HP catalog"):
        provider.sync(_profile(), tmp_path / "bundle")
