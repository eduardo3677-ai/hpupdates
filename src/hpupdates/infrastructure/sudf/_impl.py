"""SUDF v3 interoperability client based on HP.SUDFClient.dll.

Reproduces the exact cryptographic logic extracted from the decompiled C# code:

  DecryptEmbededApiKey()
    loads Key_V4, Token_V4, IV from embedded .resources
    AesDecryptString(encryptedString=Token_V4, key=Key_V4, IV=IV)
      key = SHA256(Key_V4)            -> 32-byte AES key
      iv  = SHA256(IV) truncated[:16] -> 16-byte AES IV
      AES-256-CBC, PKCS7 padding (AesManaged defaults)
    wraps result in SecureString

  Sign()
    SigV4 scope: date / us-west-2 / apigateway / aws4_request
    secret = "" (empty string)
    GetSignatureKey("", date, "us-west-2", "apigateway")
    SignedHeaders = "content-type;host;x-amz-date;x-api-key"
    Credential = /<date>/<region>/<service>/aws4_request  (no access key)

  get_ServerHost()  [r2 @ 0x1000e410]
    Default: https://sudf-api.hpcloud.hp.com/v3
    Override: registry HKLM\\SOFTWARE\\Hewlett-Packard\\HPSATest\\SUDFServiceURL
    If the registry value is set and non-empty, it replaces the default.
    If it does not start with "https://", "https://" is prepended.

  get_ResourcesServerURL()  [r2 @ 0x1000e46c]
    Default: content.methone.hpcloud.hp.net/
    Override: registry HKLM\\SOFTWARE\\Hewlett-Packard\\HPSATest\\SUDFResourcesURL
    Same prepend/normalize logic.

  WebUtil..cctor()  [r2 @ 0x1000f144]
    webHost = get_ServerHost()
    ApiKeyRegName = "ApiKeySUDF"  (registry override for the API key)

API operations discovered in decompiled source:
    GetUpdatesBySysId   — search updates by HP system ID
    GetPrinterUpdates   — search printer driver updates by product number/model
    GetMessages         — retrieve notification messages/alerts

Resource endpoints:
    content.methone.hpcloud.hp.net  — SUDF resource files (DownloadedResource.json)
    messages/{id}/Message.cab        — message CAB files
    messages/{id}/Detections.cab      — message detection CAB files

Driver download:
    PrinterUpdate.HttpURL  — HTTP download URL for driver package
    PrinterUpdate.FtpURL   — FTP download URL (fallback)
    PrinterUpdate.InstallCmd / SilentInstallCmd — install commands

HPSATest registry overrides (from SharedCommon.cs + DebugLog.cs):
    HKLM\\SOFTWARE\\Hewlett-Packard\\HPSATest\\SUDFServiceURL      -> override API endpoint
    HKLM\\SOFTWARE\\Hewlett-Packard\\HPSATest\\SUDFResourcesURL   -> override resource server
    ForceDownloadMessages -> force message CAB re-download
    HKLM\\SOFTWARE\\Hewlett-Packard\\HPSATest\\ApiKeySUDF          -> override API key
    HKLM\\SOFTWARE\\Hewlett-Packard\\HPSATest\\debug               -> enable debug logging

Additional cryptographic operations (from DataProtectTool.cs, SharedCommon.cs):
    create_sha256_cache_id()  — SHA-256 uppercase hex cache ID (WebUtil.CreateSHA256)
    to_guid()                 — SHA-1-based deterministic GUID (SharedCommon.ToGuid)
    get_dpapi_entropy()       — DPAPI entropy bytes (DataProtectTool.GetRandomEntropy)

Debug mode (from DebugLog.cs):
    HPSATest\\debug = "True" enables verbose logging to
    %PROGRAMDATA%\\Hewlett-Packard\\HP Support Framework\\Debug\\*.log

WebPost retry logic (from WebUtil.cs:416):
    3 retries with 60s timeout per attempt
    SSL errors tracked via ServerCertificateValidationScope.ErrorType

Endpoint environments:
    production  : https://sudf-api.hpcloud.hp.com/v3  (verified, works)
    custom      : any URL supplied by the user (mirrors the SUDFServiceURL registry override)

  NOTE: integration endpoint (sudf-itg-api.hpcloud.hp.com) was found in the bundle
  but returns HTTP 403 (WAF blocked). Omitted from the tool as non-functional.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# ---------------------------------------------------------------------------
# Embedded resources extracted from HP.SUDFClient.dll .resources blob
# ---------------------------------------------------------------------------

_EMBEDDED_RESOURCES: dict[str, str] = {
    "Key_V4": "APIKEY_V4",
    "Key_V3": "APIKEY_V3",
    "Token_V4": "CPxTPoz2eaXcJ2S7Nry0RU/lRBhl9gOgONIS+9Na7CINgoC4kZ2YBPYoN0Yj9n74",
    "Token_V3": "kYhACI0tTVXSLoobGjgRYj9GHpQW1ryYSROKnBIvXSQvtVN2GcNpC9yw8IabdQNz",
    "IV": "APIKEYIV",
}

# Resource server (from SharedCommon.ResourcesServerURL @ r2 0x1000e46c)
_RESOURCES_SERVER_DEFAULT = "content.methone.hpcloud.hp.net"

# Alternative resource servers found in the bundle:
# - sudf-resources.hpcloud.hp.com (used by BingPopup, HPPerformanceTuneup, HPPrintSpooler)
# - sudf-itg-resources.hpcloud.hp.com (integration/test, used by same binaries)
# These serve the same content but through a different CDN/host.
_RESOURCES_SERVER_ALTERNATIVES = [
    "content.methone.hpcloud.hp.net",     # primary (from HP.SUDFClient.dll)
    "sudf-resources.hpcloud.hp.com",       # alternative (from BingPopup, HPPerformanceTuneup)
]

# HP Support Framework API endpoint (from HP.SupportFramework.Common.dll)
# Uses the same embedded API key (AES-ECB decrypted, same key as SUDF)
# and the same SigV4 signing logic.
_FRAMEWORK_API_DEFAULT = "api2-methone.hpcloud.hp.com/v4"

# FTP download fallback (from HPDIA.exe, HPPSDrPrinterHealthMonitor.exe)
_FTP_DOWNLOAD_HOST = "ftp.hp.com"

# Telemetry endpoints (from CDMDataEventHandler.dll)
# production: us1.api.ws-hp.com/clienttelemetry
# staging:    stage-us1.api.ws-hp.com/clienttelemetry
_TELEMETRY_ENDPOINTS = {
    "production": "https://us1.api.ws-hp.com/clienttelemetry",
    "staging": "https://stage-us1.api.ws-hp.com/clienttelemetry",
}

# HP eDiags endpoint (from SolutionFinder.exe, HPWPD.exe, HP.OCF.StateData.dll)
_EDIAGS_URL = "https://h20614.www2.hp.com/ediags"

# HP Support Framework API v4 (from HP.SupportFramework.Common.dll)
# Uses the same embedded API key + SigV4 signing as SUDF v3.
_FRAMEWORK_API_V4 = "https://api2-methone.hpcloud.hp.com/v4"

# HP Device Check State API (from HPDeviceCheck.exe)
# Endpoints: state/capabilities/v1, state/progress/v1, state/data/v1, state/action/v1
# Uses the same SigV4 signing + API key as SUDF.
_DEVICE_CHECK_API = "https://api2-methone.hpcloud.hp.com/svl"

# HP Warranty endpoints (from HPWarrantyChecker.exe, HPWSD.exe)
# POST devices/verify — serial validation (ClientId=HPSA9)
# POST devices/warranty/v2 — warranty status, max 10 devices (ClientId=HPWC)
_WARRANTY_API = "https://hpsa-redirectors.hpcloud.hp.com"

# HP Checksum service for SoftPaq integrity verification
# (from HP.SupportFramework.Common.dll: get_Md5ServiceURL)
# GET {cc}/{lang}/{itemid} — returns MD5 checksum for the SoftPaq
_CHECKSUM_SERVICE = "https://support.hp.com/hp-core-services/checksum"

# HPSATest registry paths (from SharedCommon.cs + DebugLog.cs)
_HPSATEST_REG_BASE = r"Hewlett-Packard\HPSATest"

# DPAPI entropy (from DataProtectTool.GetRandomEntropy)
# Original C# string "1g3i52FG7j02h3w90o$ " with digits 0,1,3,5,7 replaced
# by spaces, then all spaces removed -> "gi2FGj2hw9o$"
_DPAPI_ENTROPY = "gi2FGj2hw9o$"


# ---------------------------------------------------------------------------
# Endpoint environments (from r2 analysis of get_ServerHost @ 0x1000e410)
# ---------------------------------------------------------------------------

class SudfEnvironment(StrEnum):
    """SUDF endpoint environments.

    Reproduces the registry-override mechanism from get_ServerHost():
    production is the hardcoded default.
    custom mirrors what HP's HPSATest\\SUDFServiceURL registry key would be set to.

    NOTE: 'integration' was removed — the endpoint sudf-itg-api.hpcloud.hp.com
    returns HTTP 403 (WAF blocked) and is non-functional from outside HP's network.
    """

    production = "production"
    custom = "custom"


# Known endpoints extracted from the binary and HP bundle analysis.
# production  : SERVER_Host constant @ 0x1002969e in HP.SUDFClient.dll (r2 izz)
# integration : REMOVED — sudf-itg-api.hpcloud.hp.com returns HTTP 403 (WAF).
#               Found in BingPopup.exe and HPPerformanceTuneup.exe but non-functional.
_KNOWN_ENDPOINTS: dict[str, tuple[str, str]] = {
    "production": (
        "https://sudf-api.hpcloud.hp.com/v3",
        "sudf-api.hpcloud.hp.com",
    ),
}


def _resolve_endpoint(
    environment: SudfEnvironment | str,
    custom_url: str | None = None,
) -> tuple[str, str]:
    """Resolve (base_url, host) for the given environment.

    Mirrors get_ServerHost() logic:
    - production uses the hardcoded URL
    - custom uses the user-supplied URL (the DLL equivalent of setting
      SUDFServiceURL in the registry)
    - If the custom URL lacks "https://", it is prepended (matching the C#)
    """
    if isinstance(environment, str):
        environment = SudfEnvironment(environment)

    if environment == SudfEnvironment.production:
        return _KNOWN_ENDPOINTS["production"]

    # custom
    if not custom_url:
        raise SudfAuthenticationError(
            "a custom URL is required when environment='custom'"
        )
    url = custom_url.strip()
    if not url.startswith("https://"):
        url = "https://" + url.removeprefix("http://")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        raise SudfAuthenticationError(f"invalid custom URL: {custom_url!r}")
    if not url.rstrip("/").endswith("/v3"):
        url = url.rstrip("/") + "/v3"
    return url, host


# ---------------------------------------------------------------------------
# AES decryption logic (mirrors DataProtectTool.AesDecryptString)
# ---------------------------------------------------------------------------

def _sha256_bytes(data: str) -> bytes:
    """SHA256 over UTF-8 bytes of a string, matching C# GetSHA256Hash."""
    return hashlib.sha256(data.encode("utf-8")).digest()


def aes_decrypt_string(encrypted_string: str, key: str, iv: str) -> str:
    """AES-256-CBC decryption matching DataProtectTool.AesDecryptString.

    key = SHA256(key_string)          -> 32 bytes
    iv  = SHA256(iv_string)[:16]      -> 16 bytes (Array.Resize(ref, 16))
    padding = PKCS7 (AesManaged default)
    """
    cipher_bytes = base64.b64decode(encrypted_string)
    aes_key = _sha256_bytes(key)
    aes_iv = _sha256_bytes(iv)[:16]
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(cipher_bytes) + decryptor.finalize()
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("invalid PKCS7 padding")
    return padded[:-pad_len].decode("utf-8")


def decrypt_embedded_api_key(version: str = "V4") -> str:
    """Decrypt the embedded API key, matching WebUtil.DecryptEmbededApiKey.

    version="V4" (default, the only path used by the real DLL).
    version="V3" supported for completeness (resources exist but are unused).
    """
    key_name = f"Key_{version}"
    token_name = f"Token_{version}"
    return aes_decrypt_string(
        encrypted_string=_EMBEDDED_RESOURCES[token_name],
        key=_EMBEDDED_RESOURCES[key_name],
        iv=_EMBEDDED_RESOURCES["IV"],
    )


# ---------------------------------------------------------------------------
# Additional cryptographic operations (from DataProtectTool.cs, SharedCommon.cs)
# ---------------------------------------------------------------------------

def create_sha256_cache_id(cache_material: str) -> str:
    """Create a SHA-256 cache ID, matching WebUtil.CreateSHA256.

    The C# computes SHA256 over ASCII bytes and formats as uppercase hex.
    Used as the x-api-cacheId header value.
    """
    return hashlib.sha256(cache_material.encode("ascii")).hexdigest().upper()


def to_guid(string_to_guid: str) -> str:
    """Generate a deterministic GUID from a string, matching SharedCommon.ToGuid.

    Uses SHA-1 (first 16 bytes) to create a UUID, exactly as the C#:
      bytes = Encoding.UTF8.GetBytes(stringToGUID)
      hash = new SHA1CryptoServiceProvider().ComputeHash(bytes)
      Array.Resize(ref hash, 16)
      return new Guid(hash)
    """
    import uuid
    sha1 = hashlib.sha1(string_to_guid.encode("utf-8")).digest()
    return str(uuid.UUID(bytes=sha1[:16]))


def get_dpapi_entropy() -> bytes:
    """Return the DPAPI entropy bytes, matching DataProtectTool.GetRandomEntropy.

    The C# code obfuscates the entropy string by replacing digits 0,1,3,5,7
    with spaces, then removing all spaces. The result is "gi2FGj2hw9o$".
    """
    return _DPAPI_ENTROPY.encode("utf-8")


# ---------------------------------------------------------------------------
# AWS SigV4 signing (mirrors WebUtil.Sign + GetSignatureKey)
# ---------------------------------------------------------------------------

AWS_REGION = "us-west-2"
AWS_SERVICE = "apigateway"
AWS_ALGORITHM = "AWS4-HMAC-SHA256"
SIGNED_HEADERS = "content-type;host;x-amz-date;x-api-key"


def _hmac_sha256(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def get_signature_key(secret: str, date_stamp: str) -> bytes:
    """Derive the SigV4 signing key.

    Matches C# GetSignatureKey(key="", date, region, service):
      k_date    = HMAC-SHA256("AWS4" + key, date)
      k_region  = HMAC-SHA256(k_date, region)
      k_service = HMAC-SHA256(k_region, service)
      k_signing = HMAC-SHA256(k_service, "aws4_request")
    """
    k_date = _hmac_sha256(("AWS4" + secret).encode("utf-8"), date_stamp)
    k_region = _hmac_sha256(k_date, AWS_REGION)
    k_service = _hmac_sha256(k_region, AWS_SERVICE)
    return _hmac_sha256(k_service, "aws4_request")


def sign_request(
    method: str,
    api_name: str,
    host: str,
    payload_hash: str,
    content_length: str,
    amz_date: str,
    date_stamp: str,
    api_key: str,
    secret: str = "",
) -> str:
    """Build the AWS SigV4 Authorization header.

    Matches the C# Sign() method exactly:
    - Credential=/<date>/<region>/<service>/aws4_request  (no access key prefix)
    - Sorted canonical headers: content-length, content-type, host, x-amz-date, x-api-key
    - content-type = "application/json" (no charset)
    """
    scope = f"{date_stamp}/{AWS_REGION}/{AWS_SERVICE}/aws4_request"
    canonical_headers = (
        f"content-length:{content_length}\n"
        "content-type:application/json\n"
        f"host:{host}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-api-key:{api_key}\n"
    )
    canonical_request = (
        f"{method}\n{api_name}\n\n{canonical_headers}\n"
        f"{SIGNED_HEADERS}\n{payload_hash}"
    )
    canonical_request_hash = hashlib.sha256(
        canonical_request.encode("utf-8")
    ).hexdigest()
    string_to_sign = (
        f"{AWS_ALGORITHM}\n{amz_date}\n{scope}\n{canonical_request_hash}"
    )
    signing_key = get_signature_key(secret, date_stamp)
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        f"{AWS_ALGORITHM} Credential=/{scope}, "
        f"SignedHeaders={SIGNED_HEADERS}, Signature={signature}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SudfAuthenticationError(RuntimeError):
    """Raised when a SUDF request cannot be authenticated or executed."""


class SudfDownloadError(RuntimeError):
    """Raised when a driver or resource download fails."""


@dataclass(frozen=True, slots=True)
class SudfCredentials:
    """Credentials for SUDF API authentication.

    By default, uses the embedded API key extracted from HP.SUDFClient.dll
    (AES-256-CBC decryption of Token_V4 with Key_V4/IV resources).
    An explicit api_key can be supplied to override the embedded key
    (mirrors the ApiKeySUDF registry override from WebUtil..cctor).
    """

    api_key: str = field(default="")
    access_key: str = ""
    signing_key: str = ""

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise SudfAuthenticationError("an SUDF API key is required")


# --- Request models (from decompiled C# DataContract classes) ---

@dataclass(frozen=True, slots=True)
class SudfRequest:
    """Request for GetUpdatesBySysId (from GetUpdatesBySysIdRequest.cs).

    Fields match the C# [DataMember] names exactly.
    """

    use_case: str
    system_id: str
    country: str = ""
    language: str = ""
    os_code: str = ""
    automatic: bool = False

    def payload(self) -> dict[str, str]:
        return {
            "UseCase": self.use_case,
            "SysId": self.system_id.strip(),
            "Country": self.country.strip(),
            "Language": self.language.strip(),
            "OS": self.os_code,
            "Auto": "1" if self.automatic else "0",
        }

    def cache_material(self) -> str:
        data = self.payload()
        return "".join(
            data[key]
            for key in ("SysId", "UseCase", "OS", "Auto", "Country", "Language")
        )


@dataclass(frozen=True, slots=True)
class PrinterUpdatesRequest:
    """Request for GetPrinterUpdates (from GetPrinterUpdatesParams.cs).

    Fields match the C# [DataMember] names exactly.
    """

    product_number: str
    model_name: str = ""
    use_case: str = "HPSF"
    lc: str = "en"
    cc: str = "US"
    pid: str = ""
    os: str = ""
    os_code: str = ""
    ignore_locale: bool = False

    def payload(self) -> dict[str, Any]:
        # Mirror C# logic from DownloadPrinterUpdateTask.cs:49-60:
        #   OS = printerParams.OS
        #   OSCode = string.IsNullOrEmpty(OS) ? OSParamsCreator().Creat().FirstOrDefault() : OS
        # When OS is provided, OSCode = OS. When OS is empty, OSCode should be
        # a generated OS code (we default to "WT64_22H2" for Win10 x64 22H2).
        os_val = self.os.strip()
        os_code_val = self.os_code.strip()
        if not os_code_val:
            os_code_val = os_val if os_val else "WT64_22H2"
        if not os_val:
            os_val = os_code_val
        return {
            "productNumber": self.product_number.strip(),
            "modelName": self.model_name.strip(),
            "UseCase": self.use_case,
            "Lc": self.lc,
            "Cc": self.cc,
            "Pid": self.pid,
            "OS": os_val,
            "OSCode": os_code_val,
            "ignoreLocale": self.ignore_locale,
        }

    def cache_material(self) -> str:
        return (
            f".{self.product_number}.{self.model_name}.{self.lc}."
            f"{self.cc}.{self.pid}.{self.use_case}.{self.os}."
            f"{self.os_code}.{self.ignore_locale}"
        )


@dataclass(frozen=True, slots=True)
class MessagesRequest:
    """Request for GetMessages (from GetMessagesRequest.cs).

    Fields match the C# [DataMember] names exactly.
    """

    use_case: str = "HPSF"
    pl: str = "en"
    language: str = "en-US"
    country_cd: str = "US"

    def payload(self) -> dict[str, str]:
        return {
            "UseCase": self.use_case,
            "PL": self.pl,
            "Language": self.language,
            "CountryCd": self.country_cd,
        }

    def cache_material(self) -> str:
        data = self.payload()
        return "".join(data[k] for k in ("UseCase", "PL", "Language", "CountryCd"))


# --- SUDF API operations (from WebUtil.CallWebAPI + DownloadSUDFUpdateTask.cs) ---

# Operations discovered in decompiled source:
#   GetUpdatesBySysId  (DownloadSUDFUpdateTask.cs:73)
#   GetPrinterUpdates   (DownloadPrinterUpdateTask.cs:64)
#   GetMessages         (from GetMessagesRequest.cs / GetMessagesResponse.cs)
_VALID_OPERATIONS = frozenset({
    "GetUpdatesBySysId",
    "GetPrinterUpdates",
    "GetMessages",
})


class SudfClient:
    """SUDF v3 interoperability client.

    Uses the embedded HP API key by default (decrypted from the DLL resources),
    with the exact same AWS SigV4 signing logic as the original C# WebUtil.

    Endpoint selection mirrors get_ServerHost() from the DLL:
    - environment='production' (default) uses the hardcoded production endpoint
    - environment='custom' with custom_url=<url> mirrors the SUDFServiceURL
      registry override, allowing any endpoint to be specified

    API operations (matching the C# CallWebAPI):
    - get_updates_by_sysid(request) -> search updates by HP system ID
    - get_printer_updates(request) -> search printer driver updates
    - get_messages(request) -> retrieve notification messages/alerts

    Driver download:
    - download_driver(update, dest_dir) -> download from HttpURL/FtpURL

    Resource download:
    - download_resource(path, dest_dir) -> download from content.methone.hpcloud.hp.net
    """

    def __init__(
        self,
        credentials: SudfCredentials | None = None,
        *,
        environment: SudfEnvironment | str = SudfEnvironment.production,
        custom_url: str | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] | None = None,
        max_response_bytes: int = 32 * 1024 * 1024,
        resources_server: str | None = None,
    ) -> None:
        self.credentials = credentials or self._default_credentials()
        self.environment = (
            environment if isinstance(environment, SudfEnvironment)
            else SudfEnvironment(environment)
        )
        self.base_url, self.host = _resolve_endpoint(
            self.environment, custom_url
        )
        self.client = client or httpx.Client(follow_redirects=False, timeout=60.0)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_response_bytes = max_response_bytes
        # Resource server override mirrors SUDFResourcesURL registry key
        self.resources_server = resources_server or _RESOURCES_SERVER_DEFAULT

    @staticmethod
    def _default_credentials() -> SudfCredentials:
        """Create credentials using the embedded API key from HP.SUDFClient.dll."""
        return SudfCredentials(api_key=decrypt_embedded_api_key("V4"))

    @staticmethod
    def embedded_api_key(version: str = "V4") -> str:
        """Return the decrypted embedded API key without making any request."""
        return decrypt_embedded_api_key(version)

    # -- API operations --

    def get_updates_by_sysid(self, request: SudfRequest) -> Mapping[str, Any]:
        """Search for updates by HP system ID.

        Calls POST {base_url}/GetUpdatesBySysId with the request payload.
        Returns the parsed JSON response (UpdatesMeta.json content).
        """
        return self._post(
            "GetUpdatesBySysId", request.payload(), request.cache_material()
        )

    def get_printer_updates(
        self, request: PrinterUpdatesRequest
    ) -> Mapping[str, Any]:
        """Search for printer driver updates.

        Calls POST {base_url}/GetPrinterUpdates with the request payload.
        Returns the parsed JSON response with PrinterUpdate entries.
        Each entry has FtpURL, HttpURL, InstallCmd, SilentInstallCmd, etc.
        """
        return self._post(
            "GetPrinterUpdates", request.payload(), request.cache_material()
        )

    def get_messages(self, request: MessagesRequest) -> Mapping[str, Any]:
        """Retrieve notification messages/alerts.

        Calls POST {base_url}/GetMessages with the request payload.
        Returns the parsed JSON response with message entries.
        """
        return self._post(
            "GetMessages", request.payload(), request.cache_material()
        )

    # -- Driver download --

    @staticmethod
    def _force_https(url: str) -> str:
        """Force HTTPS on a download URL.

        Mirrors HP.UpdateClient.dll: HTTP scheme is replaced with HTTPS
        before any download attempt.
        """
        if url.startswith("http://"):
            return "https://" + url[len("http://"):]
        return url

    def download_driver(
        self,
        update: Mapping[str, Any],
        dest_dir: str | Path,
        *,
        prefer_http: bool = True,
    ) -> Path:
        """Download a driver package from an update entry.

        Uses HttpURL (preferred) or FtpURL from the PrinterUpdate response.
        The file is saved to dest_dir using the filename from the URL.
        HTTP URLs are forced to HTTPS (mirrors HP.UpdateClient.dll logic).

        Raises SudfDownloadError if no URL is available or the download fails.
        """
        url = ""
        if prefer_http:
            url = update.get("HttpURL", "") or update.get("httpURL", "")
        if not url:
            url = update.get("FtpURL", "") or update.get("ftpURL", "")
        if not url:
            raise SudfDownloadError(
                "update entry has no HttpURL or FtpURL"
            )

        # Force HTTPS (from HP.UpdateClient.dll)
        url = self._force_https(url)

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Extract filename from URL
        filename = url.rsplit("/", 1)[-1] if "/" in url else "driver_download"
        if not filename or "." not in filename:
            filename = f"driver_{update.get('SoftwareId', 'unknown')}.exe"
        dest_path = dest_dir / filename

        if url.startswith("ftp://"):
            # FTP download — use urllib
            import urllib.request
            try:
                urllib.request.urlretrieve(url, dest_path)
            except Exception as exc:
                raise SudfDownloadError(
                    f"FTP download failed: {exc}"
                ) from exc
        else:
            # HTTP/HTTPS download with streaming
            try:
                with self.client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
            except httpx.HTTPError as exc:
                raise SudfDownloadError(
                    f"HTTP download failed: {exc}"
                ) from exc

        return dest_path

    # -- Resource download (from content.methone.hpcloud.hp.net) --

    def download_resource(
        self,
        path: str,
        dest_dir: str | Path,
    ) -> Path:
        """Download a SUDF resource file from the resources server.

        The resources server defaults to content.methone.hpcloud.hp.net
        (from SharedCommon.ResourcesServerURL). Can be overridden via the
        resources_server constructor parameter.

        Args:
            path: resource path (e.g. "DownloadedResource.json")
            dest_dir: destination directory

        Returns the path to the downloaded file.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        dest_path = dest_dir / filename

        url = f"https://{self.resources_server}/{path.lstrip('/')}"
        try:
            with self.client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in resp.iter_bytes(chunk_size=65536):
                        f.write(chunk)
        except httpx.HTTPError as exc:
            raise SudfDownloadError(
                f"resource download failed: {exc}"
            ) from exc

        return dest_path

    def download_resource_from_alternatives(
        self,
        path: str,
        dest_dir: str | Path,
    ) -> Path:
        """Download a resource trying multiple resource servers.

        Tries each server in _RESOURCES_SERVER_ALTERNATIVES in order
        (content.methone.hpcloud.hp.net first, then sudf-resources.hpcloud.hp.com).
        Returns the path to the first successful download.

        Raises SudfDownloadError if all servers fail.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        dest_path = dest_dir / filename

        errors = []
        for server in _RESOURCES_SERVER_ALTERNATIVES:
            url = f"https://{server}/{path.lstrip('/')}"
            try:
                with self.client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                return dest_path
            except httpx.HTTPError as exc:
                errors.append(f"{server}: {exc}")
                continue

        raise SudfDownloadError(
            f"all resource servers failed for {path}: {'; '.join(errors)}"
        )

    def download_message_cab(
        self,
        message_id: str,
        dest_dir: str | Path,
        *,
        include_detections: bool = True,
    ) -> list[Path]:
        """Download message CAB files for a specific message ID.

        Downloads from the resources server:
        - messages/{id}/Message.cab
        - messages/{id}/Detections.cab (if include_detections=True)

        Mirrors the C# DownloadMessageCabParams logic.

        Returns list of downloaded file paths.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        results: list[Path] = []

        files = [f"messages/{message_id}/Message.cab"]
        if include_detections:
            files.append(f"messages/{message_id}/Detections.cab")

        for rel_path in files:
            url = f"https://{self.resources_server}/{rel_path}"
            filename = rel_path.rsplit("/", 1)[-1]
            dest_path = dest_dir / filename
            try:
                with self.client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(dest_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                results.append(dest_path)
            except httpx.HTTPError as exc:
                raise SudfDownloadError(
                    f"message CAB download failed for {rel_path}: {exc}"
                ) from exc

        return results

    # -- SoftPaq integrity verification (from HP.SupportFramework.Common.dll) --

    def verify_softpaq_checksum(
        self,
        softpaq_id: str,
        local_file: str | Path,
        *,
        cc: str = "us",
        lang: str = "en",
    ) -> bool:
        """Verify a downloaded SoftPaq against the HP checksum service.

        Queries GET support.hp.com/hp-core-services/checksum/{cc}/{lang}/{id}
        (from HP.SupportFrameCommon.dll: get_Md5ServiceURL) and compares
        the returned MD5 against the local file's MD5.

        Returns True if checksums match, False otherwise.
        """
        local_path = Path(local_file)
        if not local_path.is_file():
            raise SudfDownloadError(f"local file not found: {local_file}")

        url = f"{_CHECKSUM_SERVICE}/{cc}/{lang}/{softpaq_id}"
        try:
            resp = self.client.get(url, timeout=30.0)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SudfDownloadError(
                f"checksum service request failed: {exc}"
            ) from exc

        expected = resp.text.strip().lower()
        if not expected:
            raise SudfDownloadError(
                f"checksum service returned empty response for {softpaq_id}"
            )

        local_md5 = hashlib.md5(local_path.read_bytes()).hexdigest()
        return local_md5 == expected

    def download_driver_with_signature(
        self,
        update: Mapping[str, Any],
        dest_dir: str | Path,
        *,
        prefer_http: bool = True,
        verify_checksum: bool = True,
    ) -> tuple[Path, Path | None]:
        """Download a driver package and its .hpsign CASL signature file.

        Mirrors HP.UpdateClient.dll behavior: downloads the driver package
        and attempts to download the corresponding .hpsign file (CASL
        signature used for integrity verification).

        If verify_checksum=True, also verifies the SoftPaq MD5 against
        the HP checksum service.

        Returns (driver_path, signature_path_or_none).
        """
        driver_path = self.download_driver(
            update, dest_dir, prefer_http=prefer_http
        )

        # Attempt to download the .hpsign signature file
        sig_path = None
        softpaq_id = update.get("SoftPaqId", "") or update.get("SoftwareId", "")
        if softpaq_id and str(softpaq_id).lower().startswith("sp"):
            # .hpsign files are located at the same URL with .hpsign extension
            url = ""
            if prefer_http:
                url = update.get("HttpURL", "") or update.get("httpURL", "")
            if not url:
                url = update.get("FtpURL", "") or update.get("ftpURL", "")
            if url:
                url = self._force_https(url)
                sig_url = url + ".hpsign"
                dest_dir_path = Path(dest_dir)
                sig_filename = driver_path.stem + ".hpsign"
                sig_dest = dest_dir_path / sig_filename
                try:
                    with self.client.stream("GET", sig_url) as resp:
                        if resp.status_code == 200:
                            with open(sig_dest, "wb") as f:
                                for chunk in resp.iter_bytes(chunk_size=65536):
                                    f.write(chunk)
                            sig_path = sig_dest
                except httpx.HTTPError:
                    pass  # .hpsign is optional, ignore failures

        # Optional checksum verification
        if verify_checksum and softpaq_id:
            try:
                if not self.verify_softpaq_checksum(
                    str(softpaq_id), driver_path
                ):
                    raise SudfDownloadError(
                        f"checksum mismatch for {softpaq_id}"
                    )
            except SudfDownloadError:
                raise
            except Exception:
                pass  # Don't fail if checksum service is unavailable

        return driver_path, sig_path

    # -- Core request method (mirrors WebUtil.WebPost) --

    def _post(
        self, operation: str, payload: Mapping[str, Any], cache_material: str
    ) -> Mapping[str, Any]:
        if operation not in _VALID_OPERATIONS:
            raise SudfAuthenticationError(
                f"unknown SUDF operation: {operation!r}. "
                f"Valid: {', '.join(sorted(_VALID_OPERATIONS))}"
            )
        body = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        now = self.clock().astimezone(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        body_hash = hashlib.sha256(body).hexdigest()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "x-amz-date": amz_date,
            "x-api-key": self.credentials.api_key,
            "x-amz-content-sha256": body_hash,
            "x-api-cacheId": create_sha256_cache_id(cache_material),
            "user-agent": "HP Support Assistant/9.0.0",
        }
        headers["Authorization"] = sign_request(
            method="POST",
            api_name=operation,
            host=self.host,
            payload_hash=body_hash,
            content_length=str(len(body)),
            amz_date=amz_date,
            date_stamp=date_stamp,
            api_key=self.credentials.api_key,
            secret=self.credentials.signing_key,
        )
        response = self.client.post(
            f"{self.base_url}/{operation}", content=body, headers=headers
        )
        if response.is_redirect:
            raise SudfAuthenticationError("SUDF redirects are not allowed")
        try:
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SudfAuthenticationError(
                f"SUDF request failed: HTTP {response.status_code} "
                f"{response.text[:500]}"
            ) from exc
        if len(response.content) > self.max_response_bytes:
            raise SudfAuthenticationError(
                "SUDF response exceeds the configured size limit"
            )
        try:
            result = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SudfAuthenticationError("SUDF returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise SudfAuthenticationError(
                "SUDF returned an unexpected response shape"
            )
        # Handle None or missing response body
        if result is None:
            raise SudfAuthenticationError("SUDF returned null response")
        faults = result.get("FaultItemList")
        if faults:
            fault_details = "; ".join(
                f"{f.get('ReturnCode', '?')}({f.get('FieldName', '?')})"
                for f in faults
            )
            raise SudfAuthenticationError(
                f"SUDF returned a fault response: {fault_details}"
            )
        return result
