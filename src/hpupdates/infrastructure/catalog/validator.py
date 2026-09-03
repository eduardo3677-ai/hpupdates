from __future__ import annotations

import json
import re
from pathlib import Path

from hpupdates.infrastructure.endpoints import EndpointPolicyError, require_softpaq_url
from hpupdates.models.models import DeviceRule, DriverPackage, SoftwareRule


class CatalogError(ValueError):
    pass


class JsonCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def packages(self) -> list[DriverPackage]:
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if document.get("schema_version") != 1:
            raise CatalogError("unsupported catalog schema")
        packages: list[DriverPackage] = []
        for item in document["packages"]:
            try:
                require_softpaq_url(item["download_url"])
            except EndpointPolicyError as exc:
                raise CatalogError(f"package {item['id']} has no approved URL: {exc}") from exc
            if not re.fullmatch(r"[0-9a-fA-F]{64}", item["sha256"]):
                raise CatalogError(f"package {item['id']} has an invalid SHA-256")
            packages.append(
                DriverPackage(
                    id=item["id"],
                    name=item["name"],
                    version=item["version"],
                    vendor=item["vendor"],
                    category=item["category"],
                    download_url=item["download_url"],
                    hardware_ids=tuple(item["hardware_ids"]),
                    sha256=item["sha256"],
                    silent_args=tuple(item.get("silent_args", ())),
                    release_date=item.get("release_date"),
                    architecture=item.get("architecture"),
                    device_rules=tuple(DeviceRule(**rule) for rule in item.get("device_rules", ())),
                    software_rules=tuple(
                        SoftwareRule(**rule) for rule in item.get("software_rules", ())
                    ),
                )
            )
        return packages
