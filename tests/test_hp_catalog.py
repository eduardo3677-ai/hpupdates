from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from hpupdates.infrastructure.catalog.hp_catalog import (
    HpCatalogError,
    HpImageAssistantCatalogProvider,
)
from hpupdates.models.models import MachineProfile

PLATFORM_XML = """<?xml version="1.0" encoding="utf-8"?>
<ImagePal>
  <DateLastModified>Thursday, September 3, 2026</DateLastModified>
  <Platform>
    <ProductName Id="1">HP Test Laptop</ProductName>
    <OS>
      <OSVersion>10.0</OSVersion>
      <OSArchitecture>64</OSArchitecture>
      <OSReleaseIdFilename>22h2</OSReleaseIdFilename>
      <OSBuildId>19045</OSBuildId>
      <OSDescription>Microsoft Windows 10</OSDescription>
      <OSReleaseId>22H2</OSReleaseId>
      <IsWindows11>false</IsWindows11>
    </OS>
    <SystemFamily>103C_TEST</SystemFamily>
    <SystemID>ABCD</SystemID>
  </Platform>
</ImagePal>
"""

REFERENCE_XML = """<?xml version="1.0" encoding="utf-8"?>
<ImagePal>
  <SystemInfo><System>
    <ProductName>HP Test Laptop</ProductName><SystemID>ABCD</SystemID>
  </System>
  <SoftwareInstalled>
    <Software>
      <Name>HP Utility</Name><Version>2.0</Version><Vendor>HP Inc.</Vendor>
      <UpgradeCode>{{PRODUCT-CODE}}</UpgradeCode><Is64>true</Is64>
      <Solutions><UpdateInfo IdRef="sp123457" /></Solutions>
    </Software>
  </SoftwareInstalled>
  </SystemInfo>
  <Devices>
    <Device>
      <FriendlyName>Test Audio</FriendlyName>
      <DeviceId>PCI\\VEN_1234&amp;DEV_5678</DeviceId>
      <HardwareId>PCI\\VEN_1234&amp;DEV_5678&amp;SUBSYS_0001</HardwareId>
      <ClassGuid>{{CLASS}}</ClassGuid><DriverProvider>HP</DriverProvider>
      <DriverDate>20260101</DriverDate><DriverVersion>2.0.0.0</DriverVersion>
      <Solutions><UpdateInfo IdRef="sp123456" /></Solutions>
    </Device>
  </Devices>
  <Solutions>
    <UpdateInfo>
      <Id>sp123456</Id><Name>HP Audio Driver</Name><Category>Driver - Audio</Category>
      <Version>2.3.4.5</Version><Vendor>HP</Vendor>
      <SilentInstall>/s</SilentInstall>
      <Url>ftp.hp.com/pub/softpaq/sp123501-124000/sp123456.exe</Url>
      <SHA256>{sha}</SHA256><DateReleased>2026-09-01</DateReleased>
    </UpdateInfo>
    <UpdateInfo>
      <Id>sp123457</Id><Name>HP Utility</Name><Category>Utility - Tools</Category>
      <Version>2.0</Version><Vendor>HP</Vendor>
      <Url>ftp.hp.com/pub/softpaq/sp123501-124000/sp123457.exe</Url>
      <SHA256>{sha}</SHA256><DateReleased>2026-09-01</DateReleased>
    </UpdateInfo>
  </Solutions>
</ImagePal>
"""


def _profile() -> MachineProfile:
    return MachineProfile(
        manufacturer="HP",
        model="HP Test Laptop",
        product_number="TEST123#ABA",
        serial_number="SERIAL",
        system_id="ABCD",
        system_family="103C_TEST",
        os_caption="Microsoft Windows 10 Pro",
        os_version="10.0.19045",
        os_build="19045",
        os_architecture="64",
        edition_id="Professional",
        display_version="22H2",
    )


def test_hp_provider_always_downloads_platform_and_reference_catalog(tmp_path: Path) -> None:
    calls: list[str] = []
    package_sha = hashlib.sha256(b"package").hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("/platformList.cab"):
            return httpx.Response(200, content=PLATFORM_XML.encode())
        assert request.url.path.endswith("/abcd/abcd_64_10.0.22h2.cab")
        return httpx.Response(200, content=REFERENCE_XML.format(sha=package_sha).encode())

    provider = HpImageAssistantCatalogProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cab_extractor=lambda cab: cab,
    )
    output = tmp_path / "catalog.json"

    provider.refresh(_profile(), output)
    provider.refresh(_profile(), output)

    assert len(calls) == 4
    assert calls[0] == "https://hpia.hpcloud.hp.com/ref/platformList.cab"
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["source"]["provider"] == "HP Image Assistant"
    assert document["source"]["system_id"] == "ABCD"
    assert document["packages"][0]["id"] == "sp123456"
    assert document["packages"][0]["download_url"].startswith("https://ftp.hp.com/")
    assert document["packages"][0]["hardware_ids"] == [
        "PCI\\VEN_1234&DEV_5678",
        "PCI\\VEN_1234&DEV_5678&SUBSYS_0001",
    ]
    driver = next(item for item in document["packages"] if item["id"] == "sp123456")
    assert driver["device_rules"][0]["driver_version"] == "2.0.0.0"
    software = next(item for item in document["packages"] if item["id"] == "sp123457")
    assert software["software_rules"][0]["upgrade_code"] == "{PRODUCT-CODE}"
    assert software["software_rules"][0]["version"] == "2.0"


def test_hp_provider_does_not_reuse_cache_when_refresh_fails(tmp_path: Path) -> None:
    output = tmp_path / "catalog.json"
    output.write_text('{"schema_version": 1, "packages": []}', encoding="utf-8")
    provider = HpImageAssistantCatalogProvider(
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))),
        retries=1,
        cab_extractor=lambda cab: cab,
    )

    with pytest.raises(HpCatalogError, match="latest HP catalog"):
        provider.refresh(_profile(), output)

    assert output.read_text(encoding="utf-8") == '{"schema_version": 1, "packages": []}'


def test_hp_provider_rejects_non_hp_and_unsupported_platform(tmp_path: Path) -> None:
    provider = HpImageAssistantCatalogProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=PLATFORM_XML.encode())
            )
        ),
        cab_extractor=lambda cab: cab,
    )
    non_hp = replace(_profile(), manufacturer="Other")
    with pytest.raises(HpCatalogError, match="HP-manufactured"):
        provider.refresh(non_hp, tmp_path / "x.json")

    unsupported = replace(_profile(), system_id="FFFF")
    with pytest.raises(HpCatalogError, match="not present"):
        provider.refresh(unsupported, tmp_path / "x.json")

    fake_hp = replace(_profile(), manufacturer="HPEvil")
    with pytest.raises(HpCatalogError, match="HP-manufactured"):
        provider.refresh(fake_hp, tmp_path / "x.json")


def test_hp_provider_rejects_catalog_for_wrong_architecture(tmp_path: Path) -> None:
    provider = HpImageAssistantCatalogProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=PLATFORM_XML.encode())
            )
        ),
        cab_extractor=lambda cab: cab,
    )
    with pytest.raises(HpCatalogError, match="architecture"):
        provider.refresh(replace(_profile(), os_architecture="32"), tmp_path / "x.json")


def test_hp_url_normalization_preserves_validated_ext_hostname() -> None:
    assert (
        HpImageAssistantCatalogProvider._normalize_hp_url("ftp.ext.hp.com/pub/softpaq/sp1.exe")
        == "https://ftp.ext.hp.com/pub/softpaq/sp1.exe"
    )


def test_hp_provider_rejects_oversized_catalog_download() -> None:
    provider = HpImageAssistantCatalogProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 33))
        ),
        max_download_bytes=32,
    )
    with pytest.raises(HpCatalogError, match="size limit"):
        provider._download(provider.PLATFORM_URL)


def test_real_catalog_mapping_ignores_packages_without_sha256(tmp_path: Path) -> None:
    xml = REFERENCE_XML.format(sha="")
    provider = HpImageAssistantCatalogProvider(cab_extractor=lambda cab: cab)
    document = provider.map_reference_xml(xml.encode(), _profile())
    assert document["packages"] == []
