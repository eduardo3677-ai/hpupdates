"""OS code generation — mirrors HP.SUDFClient.ScanParams.OSParamsCreator.

Reproduces the exact algorithm from OSParamsCreator.Creat():

  1. Load embedded OSParams JSON resource (OSProfixs, OSProductNames, OSRules)
  2. Gather local inputs: OSProductName, OSVersionName, Architecture, ReleaseID
  3. Match local OS name against OSProfixs[].Value → get OSProfix name (W7/W8/WT/W11)
  4. Match local OS product name against OSProductNames[].Value → get OSType (PR/EN/HP…)
     Uses GetTheSameWordCount() — word-by-word case-insensitive overlap
  5. For each OSRule whose Name == OSProfix:
     - Concatenate the mapping tokens (OSProfix, OSType, OSArchitecture, OSVersion, or literal "_")
     - Append the result to the output list
  6. Return the list of OS code strings; the first one is used as the primary OS code

Source references:
  OSParamsCreator.cs (decompiled_sudf/HP.SUDFClient.ScanParams/OSParamsCreator.cs)
  OSInformation.cs (decompiled_sudf/HP.SUDFClient.Common/OSInformation.cs)
  OSParams resource extracted from HP.SUDFClient.dll .resources blob
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any


@dataclass(frozen=True, slots=True)
class OSProfix:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class OSProductName:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class OSRule:
    name: str
    mapping: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OSParams:
    profixs: tuple[OSProfix, ...]
    product_names: tuple[OSProductName, ...]
    rules: tuple[OSRule, ...]


def _load_osparams() -> OSParams:
    """Load the embedded OSParams.json extracted from HP.SUDFClient.dll."""
    try:
        raw = resources.files("hpupdates.data").joinpath("osparams.json").read_text("utf-8")
    except Exception:
        raw = None
    if not raw:
        # Fallback inline copy of the extracted resource
        raw = _FALLBACK_OSPARAMS_JSON
    doc: dict[str, Any] = json.loads(raw)
    return OSParams(
        profixs=tuple(
            OSProfix(name=item["Name"], value=item["Value"]) for item in doc.get("OSProfixs", [])
        ),
        product_names=tuple(
            OSProductName(name=item["Name"], value=item["Value"])
            for item in doc.get("OSProductNames", [])
        ),
        rules=tuple(
            OSRule(name=item["Name"], mapping=tuple(item.get("Mapping", ())))
            for item in doc.get("OSRules", [])
        ),
    )


_FALLBACK_OSPARAMS_JSON = json.dumps(
    {
        "OSProductNames": [
            {"Name": "PR", "Value": "Professional"},
            {"Name": "ML", "Value": "Multi-Language"},
            {"Name": "EN", "Value": "Enterprise"},
            {"Name": "EM", "Value": "Emerging Markets"},
            {"Name": "EM", "Value": "Single Language (Emerging Markets)"},
            {"Name": "CH", "Value": "Chinese Market"},
            {"Name": "CH", "Value": "Country Specific (China)"},
            {"Name": "UL", "Value": "Ultimate"},
            {"Name": "HP", "Value": "Home Premium"},
            {"Name": "HB", "Value": "Home Basic"},
            {"Name": "SE", "Value": "Starter Edition"},
        ],
        "OSProfixs": [
            {"Name": "W7", "Value": "Windows 7"},
            {"Name": "W8", "Value": "Windows 8"},
            {"Name": "W8.1", "Value": "Windows 8.1"},
            {"Name": "WT", "Value": "Windows 10"},
            {"Name": "W11", "Value": "Windows 11"},
        ],
        "OSRules": [
            {"Name": "W7", "Mapping": ["OSProfix", "OSArchitecture", "OSType"]},
            {"Name": "W8", "Mapping": ["OSProfix", "OSArchitecture", "OSType"]},
            {"Name": "W8.1", "Mapping": ["OSProfix", "OSArchitecture", "OSType"]},
            {"Name": "WT", "Mapping": ["OSProfix", "OSArchitecture", "_", "OSVersion"]},
            {"Name": "WT", "Mapping": ["OSProfix", "OSArchitecture"]},
            {"Name": "W8", "Mapping": ["OSProfix", "OSArchitecture"]},
            {"Name": "W8.1", "Mapping": ["OSProfix", "OSArchitecture"]},
            {"Name": "W11", "Mapping": ["OSProfix", "_", "OSVersion"]},
        ],
    }
)


# ---------------------------------------------------------------------------
# OSInformation — mirrors HP.SUDFClient.Common.OSInformation
# ---------------------------------------------------------------------------


def is_win11(major: int, minor: int, build: int) -> bool:
    """Mirrors OSInformation.IsWin11():
    Major > 10, or Major == 10 and Build >= 22000.
    """
    return major > 10 or (major == 10 and build >= 22000)


def os_version_name(major: int, minor: int, build: int) -> str:
    """Mirrors OSInformation.OSVersionName getter.

    Maps major.minor to friendly name:
      <6           -> "Windows XP"
      6.0          -> "Windows Vista"
      6.1          -> "Windows 7"
      6.2          -> "Windows 8"
      6.3          -> "Windows 8.1"
      10.x         -> "Windows 10" (or "Windows 11" if build >= 22000)
      >10          -> "Windows 11"
    """
    if major < 6:
        return "Windows XP"
    if major == 6:
        if minor == 0:
            return "Windows Vista"
        if minor == 1:
            return "Windows 7"
        if minor == 2:
            return "Windows 8"
        if minor == 3:
            return "Windows 8.1"
        return "Windows 8.1"
    if major == 10:
        if is_win11(major, minor, build):
            return "Windows 11"
        return "Windows 10"
    return "Windows 11"


def report_os_version(
    os_sku: str,
    os_architecture: str,
    version: str,
    display_version: str,
) -> str:
    """Mirrors OSInformation.ReportOSVersion().

    Returns the compact OS string used internally:
      {num}.{sku}.{arch2}.{displayVersion}
    where num = major - 5 (8 for Win11), arch2 = first 2 chars of arch.
    """
    parts = version.split(".")
    major = int(parts[0]) if parts else 10
    minor = int(parts[1]) if len(parts) > 1 else 0
    build = int(parts[2]) if len(parts) > 2 else 0
    num = major - 5
    try:
        if is_win11(major, minor, build):
            num = 8
    except Exception:
        pass
    arch2 = os_architecture[:2] if os_architecture and len(os_architecture) > 2 else os_architecture
    return f"{num}.{os_sku}.{arch2}.{display_version}"


# ---------------------------------------------------------------------------
# OSParamsCreator — mirrors HP.SUDFClient.ScanParams.OSParamsCreator.Creat()
# ---------------------------------------------------------------------------


def _get_same_word_count(pattern: str, content: str) -> int:
    """Mirrors OSParamsCreator.GetTheSameWordCount().

    Splits both strings by space, counts case-insensitive word matches.
    """
    if not pattern:
        return 0
    words1 = pattern.split(" ")
    words2 = content.split(" ")
    count = 0
    for w1 in words1:
        for w2 in words2:
            if w1.lower() == w2.lower():
                count += 1
    return count


def create_os_codes(
    os_product_name: str,
    os_version_name_str: str,
    architecture: str,
    release_id: str,
) -> list[str]:
    """Mirrors OSParamsCreator.Creat() exactly.

    Args:
      os_product_name: the Windows product name (e.g. "Windows 10 Pro")
      os_version_name_str: the friendly OS name (e.g. "Windows 10", "Windows 11")
      architecture: "32" or "64"
      release_id: the DisplayVersion/ReleaseId (e.g. "22H2", "2009")

    Returns a list of OS code strings. The first one is used as the primary
    OS code for SUDF GetUpdatesBySysId requests.
    """
    params = _load_osparams()

    # SetInput() returns [OSProductName, OSVersionName]
    inputs = [os_product_name, os_version_name_str]

    # Find OSProfix: match any input against profix.Value (case-insensitive equals)
    os_profix = ""
    for profix in params.profixs:
        for inp in inputs:
            if inp.lower() == profix.value.lower():
                os_profix = profix.name
                break
        if os_profix:
            break

    # Find OSType: best word-overlap match against OSProductNames
    os_type = ""
    best_count = 0
    for product_name in params.product_names:
        for inp in inputs:
            count = _get_same_word_count(inp, product_name.value)
            if count > 0 and not os_type:
                os_type = product_name.name
            if count > best_count:
                os_type = product_name.name
                best_count = count

    # Apply OSRules whose Name == OSProfix
    # For each rule, concatenate the mapping tokens:
    #   "OSProfix"      -> os_profix
    #   "OSType"        -> os_type
    #   "OSArchitecture" -> architecture
    #   "OSVersion"     -> release_id
    #   "_"             -> "_" (literal separator)
    #   anything else   -> the literal string
    # If any token resolves to empty, the entire code is skipped (matching C#)
    results: list[str] = []
    for rule in params.rules:
        if rule.name.lower() != os_profix.lower():
            continue
        code = ""
        for token in rule.mapping:
            resolved = {
                "OSProfix": os_profix,
                "OSType": os_type,
                "OSArchitecture": architecture,
                "OSVersion": release_id,
            }.get(token, token)
            if resolved:
                code += resolved
            else:
                code = ""
                break
        if code:
            results.append(code)

    return results


def create_os_code(
    os_product_name: str,
    os_version_name_str: str,
    architecture: str,
    release_id: str,
) -> str:
    """Return the primary OS code (first result from create_os_codes).

    Mirrors DownloadSUDFUpdateTask.cs:68:
      getUpdatesBySysIdRequest.OS = new OSParamsCreator().Creat().FirstOrDefault()
    """
    codes = create_os_codes(os_product_name, os_version_name_str, architecture, release_id)
    return codes[0] if codes else ""
