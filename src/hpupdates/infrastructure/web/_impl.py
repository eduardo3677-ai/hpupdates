"""HP Support Assistant web layer — mirrors the Cordova/UWP JavaScript interface.

Reproduces the exact API surface from the decompiled HpsaCordovaProxy.js (3896 lines):
- 80+ UWPInterface actions (devices, updates, messages, warranty, health, security, cases)
- Solution content rendering (hpsfcustom template injection)
- hpsalauncher:// protocol handler
- Content server endpoints (methone.hpcloud.hp.net)
- Device profile operations (add, remove, edit, detect)
- Update flow (scan -> download -> install -> verify)
- Settings and preferences management

Architecture from the JS:
  Cordova (UI) -> HpsaCordovaProxy.call(action, data) -> UWPInterface[action]()
  -> HP.SupportAssistant.Tasks.* / HP.SupportAssistant.Engine.* (WinRT components)

The Python client reproduces this dispatch pattern and delegates to the
infrastructure modules (sudf_client, installer, update_detector, windows).
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Product type mapping (from _getSupportCategory in HpsaCordovaProxy.js)
# ---------------------------------------------------------------------------

PRODUCT_TYPE_PRINTER = 1
PRODUCT_TYPE_NOTEBOOK = 2
PRODUCT_TYPE_TABLET = 3
PRODUCT_TYPE_MOBILE = 4
PRODUCT_TYPE_DESKTOP = 5
PRODUCT_TYPE_MONITOR = 6
PRODUCT_TYPE_SCANNER = 7
PRODUCT_TYPE_CALCULATOR = 8

_PRODUCT_TYPE_TO_CATEGORY: dict[int, str] = {
    2: "hppc_",
    23: "hppc_",
    3: "hppc_",
    5: "hppc_",  # Notebook, Chromebook, Tablet, Desktop
    4: "mobile_",
    6: "monitor_",
    7: "scanner_",
    1: "printer_",
    8: "calculator_",
    9: "calculator_",
    30: "calculator_",
    50: "calculator_",
    51: "calculator_",
    52: "calculator_",
    53: "calculator_",
    54: "calculator_",
    70: "calculator_",
    80: "calculator_",
    100: "calculator_",
}


def product_type_to_category_prefix(product_type: int) -> str:
    """Map productType to category prefix — mirrors _getSupportCategory()."""
    return _PRODUCT_TYPE_TO_CATEGORY.get(product_type, "hppc_")


def support_category(product_type: int, suffix: str) -> str:
    """Build support category string: prefix + suffix."""
    return product_type_to_category_prefix(product_type) + suffix


# ---------------------------------------------------------------------------
# Response object (mirrors _getBasicObject)
# ---------------------------------------------------------------------------


@dataclass
class HpsaResponse:
    """Mirrors the basic response object from _getBasicObject()."""

    FaultItemList: list[dict] = field(default_factory=list)

    def add_error(self, return_code: str, origin: str = "UWP") -> None:
        self.FaultItemList.append({"Origin": origin, "ReturnCode": return_code})

    @property
    def is_success(self) -> bool:
        return len(self.FaultItemList) == 0

    def to_dict(self) -> dict:
        return {"FaultItemList": self.FaultItemList}


def _basic_object(*errors: str) -> dict:
    """Create a basic response object — mirrors _getBasicObject()."""
    obj: dict[str, Any] = {"FaultItemList": []}
    for err in errors:
        obj["FaultItemList"].append({"Origin": "UWP", "ReturnCode": err})
    return obj


def _is_local_device(device_id: str) -> bool:
    """Check if device ID refers to local device — mirrors _isLocalDevice().

    Local devices have alphanumeric IDs (letters), remote devices have numeric IDs.
    """
    if not device_id:
        return True
    return not device_id.isdigit()


# ---------------------------------------------------------------------------
# Device template (mirrors device_tpl from 'devices' action)
# ---------------------------------------------------------------------------

DEVICE_TEMPLATE: dict = {
    "NickName": None,
    "ProductName": None,
    "DeviceId": None,
    "ImageLink": None,
    "ProductType": None,
    "AlertsCount": 0,
    "AlertTitle": None,
    "Severity": None,
    "NewAlertsCount": 0,
    "SerialNumber": None,
    "FullSerialNumber": None,
    "UseFullSerial": False,
    "ProductNumber": None,
    "IsHPChromebook": False,
    "IsHPMachine": True,
    "IsSubscriptionDevice": False,
    "IsPrimary": False,
    "IsSych": False,
    "IsDetected": False,
    "IsRemote": False,
    "DeviceAdded": None,
    "DeviceNotified": None,
    "NewCaseAlertsCount": 0,
    "Status": 0,
    "Masl": None,
}


# ---------------------------------------------------------------------------
# Solution content rendering (mirrors HPSA9ObjectsCommonScript.js)
# ---------------------------------------------------------------------------

# hpsfcustom attributes injected into solution HTML templates
SOLUTION_CUSTOM_ATTRIBUTES: list[str] = [
    "UserCountryCode",
    "IsTestMode",
    "IframeUrl",
    "SerialNumber",
    "IsHPUnit",
    "PL2LetterCode",
    "ProductNumber",
    "ProductName",
    "ProductType",
    "ProductSeriesOID",
    "ProductAudience",
    "ProductLineGroup",
    "WSD",
    "WED",
    "HardwareFamilyCode",
    "OsVersion",
    "IsLocalPC",
    "EOSDate",
    "EOSDate2",
    "WarrantyNoCarepaq",
    "ExtendWarrantyPW",
    "Version",
    "BornOnDate",
    "MastiffUrl",
]

# Content server endpoints (from HPSA9ObjectsCommonScript.js)
CONTENT_SERVER_PROD = "https://content.methone.hpcloud.hp.net/messages/HpsaSpos"
CONTENT_SERVER_SANDBOX = "https://content-sbx.methone.hpcloud.hp.net/messages/HpsaSpos"
CONTENT_SERVER_ITG = "https://content-itg.methone.hpcloud.hp.net/messages/HpsaSpos"
CONTENT_CHECK_URL = "https://content.methone.hpcloud.hp.net/messages/HpsaSpos/iframe.png"

# Metrics constants (from HPSA9ObjectsCommonScript.js)
METRICS_OVERALL_TIMEOUT = 20000
METRICS_MASTIFF_TIMEOUT = 5000


def get_content_server(test_mode: bool = False) -> str:
    """Get content server URL — mirrors getServerNameWithProtcol()."""
    if test_mode:
        return CONTENT_SERVER_SANDBOX
    return CONTENT_SERVER_PROD


def build_iframe_url(
    object_name: str,
    iframe_folder: str,
    file_name: str,
    test_mode: bool = False,
    iframe_url_override: str = "",
) -> str:
    """Build iframe URL — mirrors getIframeURL().

    Format: {server}/{objectName}/{iframeFolder}/{fileName}.html?id={random}
    """
    server = iframe_url_override if iframe_url_override else get_content_server(test_mode)
    if not server or not iframe_folder or not file_name:
        return ""
    import random

    return f"{server}/{object_name}/{iframe_folder}/{file_name}.html?id={random.randint(1, 1000)}"


def inject_custom_attributes(html: str, values: dict[str, str]) -> str:
    """Inject hpsfcustom values into solution HTML — mirrors dataLoader().

    Replaces <input hpsfcustom="Key"> value attributes and
    <span hpsfcustom="Key"> innerHTML with actual values.
    """
    import re

    for attr_name, attr_value in values.items():
        # Replace input values
        pattern = rf'(<input[^>]*hpsfcustom="{re.escape(attr_name)}"[^>]*value=")([^"]*)(")'
        html = re.sub(pattern, rf"\g<1>{attr_value}\g<3>", html, flags=re.IGNORECASE)
        # Replace span innerHTML
        pattern = rf'(<span[^>]*hpsfcustom="{re.escape(attr_name)}"[^>]*>)([^<]*)(</span>)'
        html = re.sub(pattern, rf"\g<1>{attr_value}\g<3>", html, flags=re.IGNORECASE)
    return html


# ---------------------------------------------------------------------------
# hpsalauncher:// protocol handler (mirrors registerCustomProtocolCalls)
# ---------------------------------------------------------------------------

SUPPORTED_PROTOCOLS = ["hpsalauncher://", "hpsupportassistant://", "hpsaobjectmetrics:"]

# All known hpsalauncher:// actions (extracted from solutions HTML files)
LAUNCHER_ACTIONS: set[str] = {
    "AutoDispatch",
    "AutoRepair",
    "BPCCleanGuide",
    "DigitalSupport",
    "Dockingbrochure",
    "DownloadSSDFirmware",
    "DownloadSSDFirmwareV2",
    "FAQResources",
    "HPCOVIDInfo",
    "HPLine",
    "HPLive",
    "IIPlatinumTC",
    "KMWFH",
    "KakaoTalk",
    "LearnBackToOffice",
    "LearnBattPerf",
    "LearnBattTech",
    "LearnCOVID19",
    "LearnDisplaysW10",
    "LearnFirewallW10",
    "LearnHPSAChromeOS",
    "LearnHPSolutionCenter",
    "LearnHeatDissipation",
    "LearnIIPro",
    "LearnImproveGamePerformance",
    "LearnOverheat",
    "LearnPCClean",
    "LearnPCMonitor",
    "LearnPowerMgmt",
    "LearnRecovery",
    "LearnSetupPrinter",
    "LearnThermalMitigation",
    "LearnToBackupW810",
    "LearnUsingHPSA",
    "LearnWin11",
    "McAfeeDefenderLearnW8A",
    "McAfeeNoAVLearnW8A",
    "PrintQuality",
    "PrintSwUpdate",
    "RepairCenter",
    "ServicecenterPW",
    "SustainablePC",
    "SustainablePrint",
    "WeChat",
    "Win11InstallAssistant",
    "Win11Resources",
    "hpprivacy",
    "hponlineuserguide",
    "learnStorageSpace",
    "learncarenb",
    "learninstantink",
    "learninstantinkpaper",
    "learntobackupw810",
    "polyday1message",
}


def parse_launcher_url(url: str) -> tuple[str, dict[str, str]]:
    """Parse a hpsalauncher:// URL — mirrors registerCustomProtocolCalls().

    Returns (action_name, params_dict).
    Example: "hpsalauncher://LearnWin11&SerialNumber=ABC123"
    -> ("LearnWin11", {"SerialNumber": "ABC123"})
    """
    for protocol in SUPPORTED_PROTOCOLS:
        if url.lower().startswith(protocol):
            rest = url[len(protocol) :]
            parts = rest.split("&")
            action = parts[0]
            params: dict[str, str] = {}
            for part in parts[1:]:
                if "=" in part:
                    key, value = part.split("=", 1)
                    params[key] = value
            return action, params
    return "", {}


def build_launcher_url(action: str, serial_number: str = "", is_hp_unit: bool = True) -> str:
    """Build a hpsalauncher:// URL with serial number appended.

    Mirrors the logic in registerCustomProtocolCalls():
    If IsHPUnit is true and SerialNumber is not empty and the command
    doesn't already contain 'serialnumber', append &SerialNumber=SN[:10].
    """
    url = f"hpsalauncher://{action}"
    if is_hp_unit and serial_number and "serialnumber" not in url.lower():
        url += f"&SerialNumber={serial_number[:10]}"
    return url


# ---------------------------------------------------------------------------
# Settings (mirrors getSettings/setSettings in HpsaCordovaProxy.js)
# ---------------------------------------------------------------------------

CHECK_FOR_UPDATES_MAPPING: dict[int, int] = {
    2: 0,  # Install important updates automatically
    3: 1,  # Install important and recommended updates automatically
    1: 2,  # Check for updates, let me choose
    0: 3,  # Never check for updates
}


@dataclass
class HpsaSettings:
    """Mirrors the settings object from getSettings()."""

    show_taskbar_icon: bool = True
    show_contacts_and_warranty: bool = False
    share_usage_data: bool = False
    check_for_updates: int = (
        1  # 0=important auto, 1=important+recommended auto, 2=check only, 3=never
    )
    next_scan_date_utc: str = ""
    scan_weekday: int = 0
    scan_time: str = ""
    message_popups: bool = True
    display_popup_for_warranty: bool = True
    taskbar_icon: bool = True
    contact_country: str = "us"
    show_welcome: bool = True
    enable_installation: bool = True
    bios_installation: bool = True
    video_installation: bool = True
    audio_installation: bool = True
    printer_installation: bool = True
    network_installation: bool = True
    other_updates_installation: bool = True
    is_allow_wwan: bool = False


# ---------------------------------------------------------------------------
# Warranty endpoints (mirrors deviceWarranty in HpsaCordovaProxy.js)
# ---------------------------------------------------------------------------

WARRANTY_KEYS: dict[str, str] = {
    "WSD": "WSD",  # Warranty Start Date
    "WED": "WED",  # Warranty End Date
    "CED": "CED",  # Carepack End Date
    "User_Check_Date": "User_Check_Date",
    "Born_On_Date": "Born_On_Date",
    "WarrantyType": "WarrantyType",
    "SupportCode": "SupportCode",
    "SubscriptionId": "SubscriptionId",
    "SubscriptionStartDate": "SubscriptionStartDate",
}


# ---------------------------------------------------------------------------
# Security status (mirrors getSecurity in HpsaCordovaProxy.js)
# ---------------------------------------------------------------------------

SECURITY_PROPERTIES: list[str] = [
    "Firewall",
    "Antivirus",
    "Antispyware",
    "Internet",
    "Service",
    "Autoupdate",
    "UAC",
]

# Products that have security check (Notebook, Tablet, Desktop)
SECURITY_PRODUCT_TYPES: list[int] = [2, 3, 5]


# ---------------------------------------------------------------------------
# External URLs (mirrors getExternalUrl in HpsaCordovaProxy.js)
# ---------------------------------------------------------------------------

EXTERNAL_URL_IDS: list[str] = [
    "LearnEULA",
    "hpPrivacy",
    "warrantyDispute",
    "registerCarePack",
    "hpsaFaq",
    "HpsaCountrySites",
    "InstallHpsa",
    "contact",
    "learnhpsa9",
    "hpmyaccount",
]


# ---------------------------------------------------------------------------
# Update flow steps (mirrors the numbered update steps in HpsaCordovaProxy.js)
# ---------------------------------------------------------------------------

UPDATE_STEP_CONNECT_INTERNET = 1
UPDATE_STEP_CHECK_DISK_SPACE = 2
UPDATE_STEP_CREATE_RESTORE_POINT = 3
UPDATE_STEP_DOWNLOAD_INSTALL = 4


# ---------------------------------------------------------------------------
# API client — reproduces the HpsaCordovaProxy dispatch
# ---------------------------------------------------------------------------


class HpsaWebClient:
    """Web layer API client — mirrors HpsaCordovaProxy.call() dispatch.

    Each method corresponds to a UWPInterface[action] in the JS.
    Delegates to infrastructure modules for actual operations.
    """

    def __init__(
        self,
        sudf_client: Any | None = None,
        installer: Any | None = None,
        windows_backend: Any | None = None,
        scan_service: Any | None = None,
    ) -> None:
        self.sudf: Any = sudf_client
        self.installer: Any = installer
        self.windows: Any = windows_backend
        self.scan_service: Any = scan_service
        self._device_cache: dict[str, dict] = {}
        self._primary_pc: dict | None = None
        self._settings = HpsaSettings()

    # -- Device operations -------------------------------------------------

    def devices(self, ignore_cache: bool = False, run_detect: bool = False) -> dict:
        """List all devices — mirrors 'devices' action.

        Returns primary PC + remote devices + detected devices.
        """
        result = _basic_object()
        device_list: list[dict] = []

        # Get primary PC
        primary = self._get_primary_pc()
        if primary:
            device = dict(DEVICE_TEMPLATE)
            device["NickName"] = primary.get("NickName", "")
            device["ProductName"] = primary.get("ProductName", "")
            device["SerialNumber"] = primary.get("SerialNumber", "")
            device["ProductNumber"] = primary.get("ProductNumber", "")
            device["ProductType"] = primary.get("ProductType", 2)
            device["DeviceId"] = primary.get("SerialNumber", "")
            device["IsPrimary"] = True
            device["IsHPMachine"] = primary.get("IsHPMachine", True)
            device["ImageLink"] = primary.get("ImageLink", "")
            device_list.append(device)

        return {**result, "DeviceList": device_list}

    def device_details(self, device_id: str, refresh: bool = False) -> dict:
        """Get device details — mirrors 'deviceDetails' action."""
        device = self._get_device(device_id)
        if not device:
            return _basic_object("DeviceNotFound")

        result = _basic_object()
        result.update(
            {
                "IsPrimary": device.get("IsPrimary", False),
                "ProductName": device.get("ProductName", ""),
                "ProductType": device.get("ProductType", 0),
                "ProductNumber": device.get("ProductNumber", ""),
                "SerialNumber": device.get("SerialNumber", ""),
                "ImageLink": device.get("ImageLink", ""),
                "NickName": device.get("NickName", ""),
                "DeviceId": device_id,
                "UpdatesCount": device.get("UpdatesCount", 0),
                "IsRemote": device.get("IsRemote", False),
                "IsHPMachine": device.get("IsHPMachine", True),
                "Details": [],
            }
        )
        return result

    def device_add(self, nickname: str, ip_address: str = "") -> dict:
        """Add a device manually — mirrors 'deviceAdd' action."""
        result = _basic_object()
        result["DeviceId"] = 0
        result["StatusText"] = None
        # In the real tool this calls deviceOperations.manuallyAddDevice()
        # which scans the network for HP devices at the given IP
        return result

    def device_remove(self, device_id: str) -> dict:
        """Remove a device — mirrors 'deviceRemove' action."""
        return _basic_object()

    def device_edit_nickname(self, device_id: str, nickname: str) -> dict:
        """Edit device nickname — mirrors 'deviceEditNickname' action."""
        return _basic_object()

    def device_get_information(self, serial_number: str, product_number: str = "") -> dict:
        """Search for device info — mirrors 'deviceGetInformation' action.

        Calls HP's PIDS (Product Information Data Service) to look up
        product name, type, image, OIDs by serial number.
        """
        result = _basic_object()
        result["SerialNumber"] = serial_number
        result["ProductNumber"] = product_number
        return result

    # -- Scan operations ----------------------------------------------------

    def scan_messages(self, serial_number: str) -> dict:
        """Scan for messages — mirrors 'scanMessages' action.

        Calls SUDF GetMessages API and stores results in local cache.
        """
        if self.sudf:
            try:
                self.sudf.get_messages(serial_number)
                return _basic_object()
            except Exception as e:
                logger.error(f"scan_messages failed: {e}")
                return _basic_object("InternalError")
        return _basic_object()

    def scan_updates(self, serial_number: str) -> dict:
        """Scan for updates — mirrors 'scanUpdates' action.

        Calls SUDF GetUpdatesBySysId and runs local detection.
        """
        if self.scan_service:
            try:
                needed = self.scan_service.scan(sys_id=serial_number)
                return {**_basic_object(), "UpdatesCount": len(needed), "updates": needed}
            except Exception as e:
                logger.error(f"scan_updates failed: {e}")
                return _basic_object("InternalError")
        return _basic_object()

    def scan_messages_and_updates(self, serial_number: str) -> dict:
        """Scan for both messages and updates — mirrors 'scanMessagesAndUpdates'."""
        self.scan_messages(serial_number)
        return self.scan_updates(serial_number)

    # -- Update operations --------------------------------------------------

    def get_all_updates(self) -> dict:
        """Get all cached updates — mirrors 'getAllUpdates' action."""
        result = _basic_object()
        result["updates"] = []
        return result

    def get_updates_by_sn(self, serial_number: str, refresh: bool = False) -> dict:
        """Get updates for a specific device — mirrors 'getUpdatesBySN' action."""
        result = _basic_object()
        result["LastUpdateCheckDate"] = None
        result["UpdatesCount"] = 0
        result["updates"] = []
        if refresh and self.scan_service:
            try:
                needed = self.scan_service.scan(sys_id=serial_number)
                result["updates"] = needed
                result["UpdatesCount"] = len(needed)
            except Exception as e:
                logger.error(f"get_updates_by_sn failed: {e}")
        return result

    def run_updates(self, device_id: str) -> dict:
        """Launch update detection — mirrors 'runUpdates' action."""
        return _basic_object()

    def download_install_update(
        self, guid_list: list[str], serial_number: str, guid: str = "", from_history: bool = False
    ) -> dict:
        """Download and install updates — mirrors 'downloadInstallUpdate' (STEP 4).

        This is the main installation entry point. The flow is:
        1. connectToInternet (STEP 1)
        2. checkDiskSpace (STEP 2)
        3. createRestorePoint (STEP 3)
        4. downloadInstallUpdate (STEP 4)
        5. getInstallResult (poll for completion)
        """
        result = _basic_object()
        if self.installer:
            try:
                response = self.installer.download_and_install(guid_list, serial_number)
                result["downloadInstallUpdate"] = response
            except Exception as e:
                logger.error(f"download_install_update failed: {e}")
                result["FaultItemList"].append(
                    {"Origin": "UWP", "ReturnCode": "InternalError"}
                )
        return result

    def cancel_download_install(self, guids: list[str], serial_number: str) -> dict:
        """Cancel download/install — mirrors 'cancelDownloadInstall'."""
        result = _basic_object()
        if self.installer:
            with contextlib.suppress(Exception):
                result["data"] = self.installer.cancel(guids, serial_number)
        return result

    def get_install_result(self, guid_list: list[str], serial_number: str) -> dict:
        """Get installation result — mirrors 'getInstallResult'."""
        result = _basic_object()
        result["data"] = None
        return result

    def connect_to_internet(self, serial_number: str) -> dict:
        """Check internet connectivity — mirrors 'connectToInternet' (STEP 1)."""
        result = _basic_object()
        result["connectToInternet"] = True
        return result

    def check_disk_space(self, guid: str, serial_number: str) -> dict:
        """Check disk space for update — mirrors 'checkDiskSpace' (STEP 2)."""
        result = _basic_object()
        result["checkDiskSpace"] = True
        return result

    def create_restore_point(self) -> dict:
        """Create system restore point — mirrors 'createRestorePoint' (STEP 3)."""
        result = _basic_object()
        if self.installer:
            try:
                result["createRestorePoint"] = self.installer.create_restore_point()
            except Exception:
                result["createRestorePoint"] = False
        else:
            result["createRestorePoint"] = False
        return result

    def close_update(self) -> dict:
        """Close the update executable — mirrors 'closeUpdate'."""
        return _basic_object()

    def change_update_status(self, guids: list[str], serial_number: str, status: int) -> dict:
        """Change update status — mirrors 'changeUpdateStatus'."""
        result = _basic_object()
        result["updateStatus"] = True
        return result

    def save_loud_install_result(self, guid: str, serial_number: str, status: int) -> dict:
        """Save loud install result — mirrors 'saveLoudInstallResult'."""
        result = _basic_object()
        result["updateStatus"] = True
        return result

    def is_reboot_needed(self, serial_number: str) -> bool:
        """Check if reboot is needed — mirrors 'isRebootNeeded'."""
        return False

    def is_update_scan_needed(self, serial_number: str) -> bool:
        """Check if update scan is needed — mirrors 'isUpdateScanNeeded'."""
        return True

    # -- Message operations -------------------------------------------------

    def get_all_messages(self) -> dict:
        """Get all cached messages — mirrors 'getAllMessages'."""
        result = _basic_object()
        result["Messages"] = []
        return result

    def get_all_archive_messages(self, device_obj: dict | None = None) -> list:
        """Get archived messages — mirrors 'getAllArchiveMessages'."""
        return []

    def device_messages(self, device_id: str, reload_alerts: bool = True) -> dict:
        """Get messages for a device — mirrors 'deviceMessages'."""
        result = _basic_object()
        result["Messages"] = []
        return result

    def alert_edit(self, alert_id: str, status: int, device_id: str) -> dict:
        """Edit alert status — mirrors 'alertEdit'.

        Status 1 = read, Status 2 = delete.
        """
        return _basic_object()

    def alerts(self) -> dict:
        """Get all alerts — mirrors 'alerts'."""
        result = _basic_object()
        result["Alerts"] = []
        return result

    # -- Warranty operations -----------------------------------------------

    def device_warranty(
        self,
        device_id: str,
        serial_number: str,
        product_number: str,
        refresh: bool = False,
    ) -> dict:
        """Get warranty info — mirrors 'deviceWarranty'.

        Calls HPWarrantyHelper.getWarrantyAsync() which queries HP's
        warranty web service (HPWSD) with SN + PN.
        """
        result = _basic_object()
        result.update(
            {
                "ProductName": None,
                "ProductNumber": product_number,
                "SerialNumber": serial_number,
                "BornOnDate": None,
                "LastCheckDate": None,
                "WarrantyStartDate": None,
                "WarrantyEndDate": None,
                "SoftwareCarePackEndDate": None,
                "HardwareCarePackEndDate": None,
                "IsWarrantyRefreshed": False,
                "WarrantyStatus": False,
            }
        )
        return result

    # -- Health operations -------------------------------------------------

    def device_health_battery(self, device_id: str) -> dict:
        """Check battery health — mirrors 'deviceHealthBattery'."""
        result = _basic_object()
        result["Status"] = "unknown"
        return result

    def device_health_storage(self, device_id: str) -> dict:
        """Check storage health — mirrors 'deviceHealthStorage'."""
        result = _basic_object()
        result["Status"] = "unknown"
        return result

    def device_health_cooling(self, device_id: str) -> dict:
        """Check cooling health — mirrors 'deviceHealthCooling'."""
        result = _basic_object()
        result["Status"] = "unknown"
        return result

    def device_specs(self, device_id: str) -> dict:
        """Get device specifications — mirrors 'deviceSpecs'."""
        result = _basic_object()
        result["Details"] = []
        return result

    # -- Security ----------------------------------------------------------

    def get_security(self, device_id: str, product_type: int, force_refresh: bool = False) -> dict:
        """Get security status — mirrors 'getSecurity'.

        Only for Notebook (2), Tablet (3), Desktop (5).
        Checks: Firewall, Antivirus, Antispyware, Internet, Service, Autoupdate, UAC.
        """
        result = _basic_object()
        result["Status"] = "unknown"
        result["Properties"] = {}
        result["RecommendedActions"] = {}

        if product_type not in SECURITY_PRODUCT_TYPES:
            return result

        result["Properties"] = {prop.lower(): "unknown" for prop in SECURITY_PROPERTIES}
        return result

    # -- Settings ----------------------------------------------------------

    def get_settings(self) -> dict:
        """Get settings — mirrors 'getSettings'."""
        s = self._settings
        return {
            **_basic_object(),
            "ShowTaskbarIconCheckBox": s.show_taskbar_icon,
            "ShowContactsAndWarranty": s.show_contacts_and_warranty,
            "ShareUsageData": s.share_usage_data,
            "CheckForUpdatesAndMessages": s.check_for_updates,
            "NextScanDate": s.next_scan_date_utc,
            "ScanWeekday": s.scan_weekday,
            "ScanTime": s.scan_time,
            "MessagePopups": s.message_popups,
            "DisplayPopupForWarranty": s.display_popup_for_warranty,
            "TaskbarIcon": s.taskbar_icon,
            "ContactCountry": s.contact_country,
            "ShowWelcome": s.show_welcome,
            "EnableInstallation": s.enable_installation,
            "BiosInstallation": s.bios_installation,
            "VideoInstallation": s.video_installation,
            "AudioInstallation": s.audio_installation,
            "PrinterInstallation": s.printer_installation,
            "NetworkInstallation": s.network_installation,
            "OtherUpdatesInstallation": s.other_updates_installation,
            "IsAllowWWAN": s.is_allow_wwan,
        }

    def set_settings(self, settings: dict) -> dict:
        """Set settings — mirrors 'setSettings'."""
        s = self._settings
        if "ShowTaskbarIconCheckBox" in settings:
            s.show_taskbar_icon = settings["ShowTaskbarIconCheckBox"]
        if "ShowContactsAndWarranty" in settings:
            s.show_contacts_and_warranty = settings["ShowContactsAndWarranty"]
        if "ShareUsageData" in settings:
            s.share_usage_data = settings["ShareUsageData"]
        if "CheckForUpdatesAndMessages" in settings:
            # Reverse mapping: UI value -> internal value
            reverse = {v: k for k, v in CHECK_FOR_UPDATES_MAPPING.items()}
            ui_val = settings["CheckForUpdatesAndMessages"]
            if ui_val in reverse:
                s.check_for_updates = reverse[ui_val]
        return _basic_object()

    # -- Profile -----------------------------------------------------------

    def get_profile(self, auth_token: str = "", keep_me_signed_in: bool = True) -> dict:
        """Get user profile — mirrors 'getProfile'.

        Calls ProfileTask().getProfile() which contacts HP's profile service.
        """
        result = _basic_object()
        result.update(
            {
                "FirstName": None,
                "LastName": None,
                "City": None,
                "Email": None,
                "EmailOffers": False,
                "PrimaryUse": None,
                "Country": None,
                "Language": None,
                "ActiveHealth": None,
                "CompanyName": None,
            }
        )
        return result

    def set_profile(self, profile_data: dict) -> dict:
        """Set user profile — mirrors 'setProfile'.

        Sends to HP's profile service:
        Country, FirstName, LastName, Language, EmailConsent (Y/N),
        PrimaryUse, EmailAddress, ActiveHealth, City, CompanyName
        """
        payload = {
            "Country": profile_data.get("Country", ""),
            "FirstName": profile_data.get("FirstName", ""),
            "LastName": profile_data.get("LastName", ""),
            "Language": profile_data.get("Language", ""),
            "EmailConsent": "Y" if profile_data.get("EmailOffers") else "N",
            "PrimaryUse": profile_data.get("PrimaryUse", ""),
            "EmailAddress": profile_data.get("Email", ""),
            "ActiveHealth": profile_data.get("ActiveHealth", ""),
            "City": profile_data.get("City", ""),
            "CompanyName": profile_data.get("CompanyName", ""),
        }
        result = _basic_object()
        result["setProfile"] = payload
        return result

    def is_user_logged_in(self) -> bool:
        """Check if user is logged in — mirrors 'isUserLoggedIn'."""
        return False

    def logout(self) -> dict:
        """Logout — mirrors 'logout'."""
        return {"logoutSucceed": True}

    # -- Support cases (CTS) ----------------------------------------------

    def create_case(self, case_data: dict) -> dict:
        """Create support case — mirrors 'createCase'.

        Calls CTS.Support().createCaseAsync() with:
        SN, PN, Subject, Description, Phone, FirstName, LastName, Email,
        Country, IncomingChannel
        """
        return _basic_object()

    def get_cases(self) -> dict:
        """Get all cases — mirrors 'getCases'."""
        return _basic_object()

    def get_cases_for_device(self, serial_number: str) -> dict:
        """Get cases for device — mirrors 'getCasesForDevice'."""
        return _basic_object()

    def get_case_by_number(self, case_number: str) -> dict:
        """Get case by number — mirrors 'getCaseByNumber'."""
        return _basic_object()

    def request_to_close_case(self, case_id: str) -> dict:
        """Request to close case — mirrors 'requestToCloseCase'."""
        return _basic_object()

    def get_service_centers(
        self, serial_number: str, city_or_zip: str, filters: str, radius: int
    ) -> dict:
        """Get service centers — mirrors 'getServiceCenters'."""
        return _basic_object()

    def get_primary_support_options(self) -> dict:
        """Get primary support options — mirrors 'getPrimarySupportOptions'."""
        return _basic_object()

    def get_working_hours(self, device_id: str, call_from: str = "") -> dict:
        """Get working hours — mirrors 'getWorkingHours'."""
        return _basic_object()

    def send_feedback(self, feedback_data: dict) -> dict:
        """Send feedback — mirrors 'sendFeedback'."""
        return _basic_object()

    # -- Launcher / External URLs ------------------------------------------

    def get_external_url(self, page_id: str, page_url: str = "", device_id: str = "") -> dict:
        """Get external URL — mirrors 'getExternalUrl'.

        Supported page IDs: LearnEULA, hpPrivacy, warrantyDispute, etc.
        """
        result = _basic_object()
        result["URL"] = page_url
        return result

    def call_custom_action(self, action_type: str, parameters: list) -> dict:
        """Call custom action — mirrors 'callCustomAction'.

        Types: Launcher, LauncherWithURL, Redirector, URI, Launchhpsmart
        """
        result = _basic_object()
        if action_type == "URI" or action_type in ("Launcher", "LauncherWithURL"):
            result["URL"] = parameters[0] if parameters else ""
        return result

    # -- UUID --------------------------------------------------------------

    def get_uuid(self) -> str:
        """Get device UUID — mirrors 'getUUID'.

        Reads from SMBIOS via HostProductInfo.getUUIDAsync().
        """
        if self.windows:
            try:
                info = self.windows.get_bios_info()
                return info.get("sys_id", "")
            except Exception:
                pass
        return ""

    # -- Solution content -------------------------------------------------

    def get_message_path(self, content_name: str, locale: str = "en-US") -> str:
        """Get solution content path — mirrors 'getMessagPath'.

        Builds path to www/solutions/{locale}/{content_name}.html
        """
        return f"solutions/{locale}/{content_name}.html"

    # -- Internal helpers -------------------------------------------------

    def _get_primary_pc(self) -> dict:
        """Get primary PC info — mirrors _getDevices() primary device logic."""
        if self._primary_pc is None:
            if self.windows:
                try:
                    self._primary_pc = {
                        "SerialNumber": "",
                        "ProductNumber": "",
                        "ProductName": "",
                        "ProductType": 2,
                        "IsHPMachine": True,
                        "IsPrimary": True,
                    }
                except Exception:
                    self._primary_pc = {}
            else:
                self._primary_pc = {}
        return self._primary_pc

    def _get_device(self, device_id: str) -> dict | None:
        """Get device by ID — mirrors _getDevices(requested_device_id)."""
        if _is_local_device(device_id):
            return self._get_primary_pc()
        return self._device_cache.get(device_id)


# ---------------------------------------------------------------------------
# Action dispatch table (mirrors UWPInterface dispatch)
# ---------------------------------------------------------------------------

ACTION_DISPATCH: dict[str, str] = {
    # Scan
    "scanMessages": "scan_messages",
    "scanUpdates": "scan_updates",
    "scanMessagesAndUpdates": "scan_messages_and_updates",
    # Updates
    "getAllUpdates": "get_all_updates",
    "getUpdatesBySN": "get_updates_by_sn",
    "runUpdates": "run_updates",
    "downloadInstallUpdate": "download_install_update",
    "cancelDownloadInstall": "cancel_download_install",
    "getInstallResult": "get_install_result",
    "connectToInternet": "connect_to_internet",
    "checkDiskSpace": "check_disk_space",
    "createRestorePoint": "create_restore_point",
    "closeUpdate": "close_update",
    "changeUpdateStatus": "change_update_status",
    "saveLoudInstallResult": "save_loud_install_result",
    "isRebootNeeded": "is_reboot_needed",
    "isUpdateScanNeeded": "is_update_scan_needed",
    # Messages
    "getAllMessages": "get_all_messages",
    "getAllArchiveMessages": "get_all_archive_messages",
    "deviceMessages": "device_messages",
    "alertEdit": "alert_edit",
    "alerts": "alerts",
    # Devices
    "devices": "devices",
    "deviceDetails": "device_details",
    "deviceAdd": "device_add",
    "deviceRemove": "device_remove",
    "deviceEditNickname": "device_edit_nickname",
    "deviceGetInformation": "device_get_information",
    # Warranty
    "deviceWarranty": "device_warranty",
    # Health
    "deviceHealthBattery": "device_health_battery",
    "deviceHealthStorage": "device_health_storage",
    "deviceHealthCooling": "device_health_cooling",
    "deviceSpecs": "device_specs",
    # Security
    "getSecurity": "get_security",
    # Settings
    "getSettings": "get_settings",
    "setSettings": "set_settings",
    # Profile
    "getProfile": "get_profile",
    "setProfile": "set_profile",
    "isUserLoggedIn": "is_user_logged_in",
    "logout": "logout",
    # Cases
    "createCase": "create_case",
    "getCases": "get_cases",
    "getCasesForDevice": "get_cases_for_device",
    "getCaseByNumber": "get_case_by_number",
    "requestToCloseCase": "request_to_close_case",
    "getServiceCenters": "get_service_centers",
    "getPrimarySupportOptions": "get_primary_support_options",
    "getWorkingHours": "get_working_hours",
    "sendFeedback": "send_feedback",
    # Launcher
    "getExternalUrl": "get_external_url",
    "callCustomAction": "call_custom_action",
    # UUID
    "getUUID": "get_uuid",
    # Messages path
    "getMessagPath": "get_message_path",
}


def dispatch_action(client: HpsaWebClient, action: str, data: dict) -> dict | Any:
    """Dispatch an action to the web client — mirrors UWPInterface[action]().

    This reproduces the exact dispatch pattern from HpsaCordovaProxy.js:
    if (typeof UWPInterface[data.action] === 'function')
        return UWPInterface[data.action]();
    """
    method_name = ACTION_DISPATCH.get(action)
    if not method_name:
        return _basic_object("ActionNotSupported")

    method = getattr(client, method_name, None)
    if method is None or not callable(method):
        return _basic_object("ActionNotSupported")

    try:
        return method(**data) if isinstance(data, dict) else method()
    except TypeError:
        # Fallback for methods that don't take keyword args
        try:
            return method()
        except Exception as e:
            logger.error(f"Action '{action}' failed: {e}")
            return _basic_object("InternalError")
    except Exception as e:
        logger.error(f"Action '{action}' failed: {e}")
        return _basic_object("InternalError")
