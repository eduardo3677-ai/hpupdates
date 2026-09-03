from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


class EndpointPolicyError(RuntimeError):
    """Raised when code attempts to use an unapproved HP endpoint."""


@dataclass(frozen=True, slots=True)
class HpEndpoint:
    name: str
    url: str
    category: str
    environment: str
    region: str | None
    operational: bool
    source: str
    offset: str | None = None
    credentials: str = "not-included"
    note: str = ""


# This registry is intentionally static and auditable. Presence in an HP binary does not
# imply authorization to contact a service. Only the public HPIA and SoftPaq endpoints
# needed by hp-driverctl are operational; everything else is evidence-only metadata.
_ENDPOINTS: tuple[HpEndpoint, ...] = (
    HpEndpoint(
        "hpia_catalog",
        "https://hpia.hpcloud.hp.com/ref/",
        "catalog",
        "production",
        None,
        True,
        "HPImageAssistant.dll / TRKey and HPRefDownloadJob",
    ),
    HpEndpoint(
        "hpia_knowledge_base",
        "https://hpia.hpcloud.hp.com/kb/",
        "knowledge-base-catalog",
        "production",
        None,
        True,
        "HPImageAssistant.dll / HPKBDownloadJob",
        "0x1400448fd",
    ),
    HpEndpoint(
        "hpia_product_catalog",
        "https://hpia.hpcloud.hp.com/productcatalog/",
        "product-catalog",
        "production",
        None,
        True,
        "HPImageAssistant.dll / GetProductCatalogUpdateJob",
        "0x140041c60",
    ),
    HpEndpoint(
        "softpaq_primary",
        "https://ftp.hp.com/",
        "download",
        "production",
        "global",
        True,
        "HP public reference catalogs",
    ),
    HpEndpoint(
        "softpaq_extended",
        "https://ftp.ext.hp.com/",
        "download",
        "production",
        "global",
        True,
        "HP public reference catalogs",
    ),
    HpEndpoint(
        "analytics_us_west_2",
        "https://us-west-2.kinesis.hpanalytics.net/windows/",
        "telemetry",
        "production",
        "us-west-2",
        False,
        "AnalyticsService.dll",
        "0x6df30",
        note="Telemetry is outside hp-driverctl's update function.",
    ),
    HpEndpoint(
        "analytics_stable_us_west_2",
        "https://stable-us-west-2.kinesis.hpanalytics.net/windows/u",
        "telemetry",
        "production",
        "us-west-2",
        False,
        "AnalyticsService.dll",
        "0x7ba90",
    ),
    HpEndpoint(
        "analytics_config",
        "https://downloads.hpanalytics.net/ta-client",
        "telemetry",
        "production",
        None,
        False,
        "AnalyticsService.dll",
        "0x7afb0",
    ),
    HpEndpoint(
        "hpdaas_discovery",
        "https://discovery.hpdaas.com/v2/services",
        "service-discovery",
        "production",
        None,
        False,
        "AnalyticsService.dll",
        "0x6df30",
    ),
    HpEndpoint(
        "sudf_api",
        "https://sudf-api.hpcloud.hp.com/v3",
        "private-api",
        "production",
        "us-west-2",
        False,
        "HP.SUDFClient.dll",
        "0x2789e",
        note=(
            "Requires an HP-authorized x-api-key. The interoperable signer exists, but "
            "the embedded HP credential is neither extracted nor distributed."
        ),
    ),
    HpEndpoint(
        "sudf_resources",
        "https://content.methone.hpcloud.hp.net/",
        "support-content",
        "production",
        None,
        False,
        "HP.SUDFClient.dll / HPSA9ObjectsCommonScript.js",
        note="SUDF resource files, message CABs, solution HTML content.",
    ),
    HpEndpoint(
        "hpsa_redirectors",
        "https://hpsa-redirectors.hpcloud.hp.com/",
        "redirector",
        "production",
        None,
        False,
        "HPSA AppX HPSA9ObjectsCommonScript.js",
        note="PSG redirector and video NPC redirector.",
    ),
    HpEndpoint(
        "methone_production",
        "https://api2-methone.hpcloud.hp.com/v4",
        "support-content",
        "production",
        None,
        False,
        "HP.SupportFramework.Common.dll",
        "0x2b2a0",
    ),
    HpEndpoint(
        "client_telemetry_us1",
        "https://us1.api.ws-hp.com/clienttelemetry",
        "telemetry",
        "production",
        "us1",
        False,
        "CDMDataEventHandler.dll",
        "0x1229a",
    ),
    HpEndpoint(
        "client_telemetry_preintegration_us1",
        "https://pie-us1.api.ws-hp.com/clienttelemetry",
        "telemetry",
        "testing",
        "us1",
        False,
        "CDMDataEventHandler.dll",
        "0x121c8",
    ),
    HpEndpoint(
        "client_telemetry_stage_us1",
        "https://stage-us1.api.ws-hp.com/clienttelemetry",
        "telemetry",
        "staging",
        "us1",
        False,
        "CDMDataEventHandler.dll",
        "0x12224",
    ),
    HpEndpoint(
        "methone_integration",
        "https://api2-itg-methone.hpcloud.hp.com/stg-v4/",
        "support-content",
        "integration",
        None,
        False,
        "HPSA AppX hpsaSources.min.js",
    ),
    HpEndpoint(
        "methone_sandbox",
        "https://content-sbx.methone.hpcloud.hp.net/messages/HpsaSpos",
        "support-content",
        "sandbox",
        None,
        False,
        "HPSA AppX HPSAObjectsScripts_v9.js",
    ),
    HpEndpoint(
        "identity_staging",
        "https://directory.stg.cd.id.hp.com/directory/v1/oauth/authorize",
        "identity",
        "staging",
        None,
        False,
        "HPSA AppX hpsaSources.min.js",
        note="OAuth parameters and credentials are deliberately excluded.",
    ),
    HpEndpoint(
        "mastiff_integration",
        "https://mastiff-itg.ext.hp.com/",
        "support-content",
        "integration",
        None,
        False,
        "HPSA AppX Solution_PolyDay1Message.html",
    ),
    HpEndpoint(
        "sudf_resources_integration",
        "https://sudf-itg-resources.hpcloud.hp.com/",
        "support-content",
        "integration",
        None,
        False,
        "HPPerformanceTuneup.exe / BingPopup.exe",
        "0x2fd?",
    ),
    HpEndpoint(
        "virtual_agent_dev",
        "https://virtualagent-dev.hpcloud.hp.com/",
        "virtual-agent",
        "development",
        None,
        False,
        "HPSA AppX externalLibs.min.js",
    ),
    HpEndpoint(
        "houston_internal",
        "http://hub-web-pro.houston.hp.com/",
        "corporate-internal",
        "internal",
        None,
        False,
        "HPSA AppX HpsaCordovaProxy.js",
        note="Corporate/internal HTTP endpoint; never contacted.",
    ),
    HpEndpoint(
        "local_sysinfo_rpc",
        "HPSysInfoRpcEndpoint",
        "local-ipc",
        "local",
        None,
        False,
        "SysInfoCap.exe and Fusion components",
    ),
    HpEndpoint(
        "local_network_rpc",
        "HPNetworkRpcEndpoint",
        "local-ipc",
        "local",
        None,
        False,
        "NetworkCap.exe and Fusion components",
    ),
)


def endpoint_inventory() -> tuple[HpEndpoint, ...]:
    return _ENDPOINTS


def require_operational_endpoint(name: str) -> HpEndpoint:
    endpoint = next((item for item in _ENDPOINTS if item.name == name), None)
    if endpoint is None:
        raise EndpointPolicyError(f"unknown HP endpoint: {name}")
    if not endpoint.operational:
        raise EndpointPolicyError(
            f"HP endpoint {name!r} is evidence-only and not authorized for network use"
        )
    return endpoint


def require_softpaq_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.casefold() != "https":
        raise EndpointPolicyError("only HTTPS package downloads are approved")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise EndpointPolicyError("package URL contains unapproved authority data")
    allowed_hosts = {
        urlparse(require_operational_endpoint(name).url).hostname
        for name in ("softpaq_primary", "softpaq_extended")
    }
    if (parsed.hostname or "").casefold() not in allowed_hosts:
        raise EndpointPolicyError("package URL does not use an approved HP download host")
    return url
