"""Tests for the HPSA web client — mirrors HpsaCordovaProxy dispatch."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hpupdates.infrastructure.web import (
    ACTION_DISPATCH,
    CHECK_FOR_UPDATES_MAPPING,
    CONTENT_CHECK_URL,
    CONTENT_SERVER_ITG,
    CONTENT_SERVER_PROD,
    CONTENT_SERVER_SANDBOX,
    DEVICE_TEMPLATE,
    EXTERNAL_URL_IDS,
    HpsaResponse,
    HpsaSettings,
    HpsaWebClient,
    LAUNCHER_ACTIONS,
    METRICS_MASTIFF_TIMEOUT,
    METRICS_OVERALL_TIMEOUT,
    PRODUCT_TYPE_CALCULATOR,
    PRODUCT_TYPE_DESKTOP,
    PRODUCT_TYPE_MOBILE,
    PRODUCT_TYPE_MONITOR,
    PRODUCT_TYPE_NOTEBOOK,
    PRODUCT_TYPE_PRINTER,
    PRODUCT_TYPE_SCANNER,
    PRODUCT_TYPE_TABLET,
    SECURITY_PRODUCT_TYPES,
    SECURITY_PROPERTIES,
    SOLUTION_CUSTOM_ATTRIBUTES,
    SUPPORTED_PROTOCOLS,
    UPDATE_STEP_CHECK_DISK_SPACE,
    UPDATE_STEP_CONNECT_INTERNET,
    UPDATE_STEP_CREATE_RESTORE_POINT,
    UPDATE_STEP_DOWNLOAD_INSTALL,
    WARRANTY_KEYS,
    _basic_object,
    _is_local_device,
    build_iframe_url,
    build_launcher_url,
    dispatch_action,
    get_content_server,
    inject_custom_attributes,
    parse_launcher_url,
    product_type_to_category_prefix,
    support_category,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_action_dispatch_has_53_actions(self) -> None:
        assert len(ACTION_DISPATCH) == 53

    def test_content_servers(self) -> None:
        assert CONTENT_SERVER_PROD == (
            "https://content.methone.hpcloud.hp.net/messages/HpsaSpos"
        )
        assert CONTENT_SERVER_SANDBOX == (
            "https://content-sbx.methone.hpcloud.hp.net/messages/HpsaSpos"
        )
        assert CONTENT_SERVER_ITG == (
            "https://content-itg.methone.hpcloud.hp.net/messages/HpsaSpos"
        )

    def test_content_check_url(self) -> None:
        assert "iframe.png" in CONTENT_CHECK_URL

    def test_metrics_timeouts(self) -> None:
        assert METRICS_OVERALL_TIMEOUT == 20000
        assert METRICS_MASTIFF_TIMEOUT == 5000

    def test_update_step_values(self) -> None:
        assert UPDATE_STEP_CONNECT_INTERNET == 1
        assert UPDATE_STEP_CHECK_DISK_SPACE == 2
        assert UPDATE_STEP_CREATE_RESTORE_POINT == 3
        assert UPDATE_STEP_DOWNLOAD_INSTALL == 4

    def test_product_type_values(self) -> None:
        assert PRODUCT_TYPE_PRINTER == 1
        assert PRODUCT_TYPE_NOTEBOOK == 2
        assert PRODUCT_TYPE_TABLET == 3
        assert PRODUCT_TYPE_MOBILE == 4
        assert PRODUCT_TYPE_DESKTOP == 5
        assert PRODUCT_TYPE_MONITOR == 6
        assert PRODUCT_TYPE_SCANNER == 7
        assert PRODUCT_TYPE_CALCULATOR == 8

    def test_supported_protocols(self) -> None:
        assert "hpsalauncher://" in SUPPORTED_PROTOCOLS
        assert "hpsupportassistant://" in SUPPORTED_PROTOCOLS
        assert "hpsaobjectmetrics:" in SUPPORTED_PROTOCOLS

    def test_security_product_types(self) -> None:
        assert SECURITY_PRODUCT_TYPES == [2, 3, 5]

    def test_security_properties(self) -> None:
        for prop in ["Firewall", "Antivirus", "Antispyware", "Internet",
                      "Service", "Autoupdate", "UAC"]:
            assert prop in SECURITY_PROPERTIES

    def test_warranty_keys(self) -> None:
        for key in ["WSD", "WED", "CED", "User_Check_Date", "Born_On_Date"]:
            assert key in WARRANTY_KEYS

    def test_external_url_ids(self) -> None:
        for uid in ["LearnEULA", "hpPrivacy", "warrantyDispute", "registerCarePack"]:
            assert uid in EXTERNAL_URL_IDS

    def test_launcher_actions_populated(self) -> None:
        assert len(LAUNCHER_ACTIONS) > 40
        assert "LearnWin11" in LAUNCHER_ACTIONS
        assert "Win11InstallAssistant" in LAUNCHER_ACTIONS

    def test_solution_custom_attributes(self) -> None:
        for attr in ["UserCountryCode", "SerialNumber", "ProductNumber", "ProductName"]:
            assert attr in SOLUTION_CUSTOM_ATTRIBUTES

    def test_device_template_structure(self) -> None:
        assert DEVICE_TEMPLATE["AlertsCount"] == 0
        assert DEVICE_TEMPLATE["NewAlertsCount"] == 0
        assert DEVICE_TEMPLATE["Status"] == 0
        assert DEVICE_TEMPLATE["IsHPMachine"] is True
        assert DEVICE_TEMPLATE["IsPrimary"] is False
        assert DEVICE_TEMPLATE["IsRemote"] is False


# ---------------------------------------------------------------------------
# Product type -> category mapping
# ---------------------------------------------------------------------------

class TestProductTypeToCategory:
    @pytest.mark.parametrize("ptype,expected", [
        (PRODUCT_TYPE_NOTEBOOK, "hppc_"),
        (PRODUCT_TYPE_TABLET, "hppc_"),
        (PRODUCT_TYPE_DESKTOP, "hppc_"),
        (PRODUCT_TYPE_MOBILE, "mobile_"),
        (PRODUCT_TYPE_MONITOR, "monitor_"),
        (PRODUCT_TYPE_SCANNER, "scanner_"),
        (PRODUCT_TYPE_PRINTER, "printer_"),
        (PRODUCT_TYPE_CALCULATOR, "calculator_"),
    ])
    def test_known_types(self, ptype: int, expected: str) -> None:
        assert product_type_to_category_prefix(ptype) == expected

    def test_chromebook_23_maps_to_hppc(self) -> None:
        assert product_type_to_category_prefix(23) == "hppc_"

    def test_unknown_type_defaults_to_hppc(self) -> None:
        assert product_type_to_category_prefix(99) == "hppc_"

    def test_support_category_combines_prefix_and_suffix(self) -> None:
        assert support_category(PRODUCT_TYPE_NOTEBOOK, "driver") == "hppc_driver"

    def test_support_category_printer(self) -> None:
        assert support_category(PRODUCT_TYPE_PRINTER, "update") == "printer_update"


# ---------------------------------------------------------------------------
# Content server and iframe URLs
# ---------------------------------------------------------------------------

class TestContentServer:
    def test_get_content_server_prod(self) -> None:
        assert get_content_server(False) == CONTENT_SERVER_PROD

    def test_get_content_server_sandbox(self) -> None:
        assert get_content_server(True) == CONTENT_SERVER_SANDBOX

    def test_build_iframe_url_prod(self) -> None:
        url = build_iframe_url("obj", "folder", "file")
        assert url.startswith(CONTENT_SERVER_PROD + "/obj/folder/file.html?id=")

    def test_build_iframe_url_sandbox(self) -> None:
        url = build_iframe_url("obj", "folder", "file", test_mode=True)
        assert url.startswith(CONTENT_SERVER_SANDBOX + "/obj/folder/file.html?id=")

    def test_build_iframe_url_override(self) -> None:
        url = build_iframe_url(
            "obj", "folder", "file",
            iframe_url_override="https://custom.hp.com",
        )
        assert url.startswith("https://custom.hp.com/obj/folder/file.html?id=")

    def test_build_iframe_url_has_random_id(self) -> None:
        url1 = build_iframe_url("obj", "folder", "file")
        url2 = build_iframe_url("obj", "folder", "file")
        # Random IDs may rarely collide; structure should be identical
        assert url1.split("?id=")[0] == url2.split("?id=")[0]

    def test_build_iframe_url_empty_folder_returns_empty(self) -> None:
        assert build_iframe_url("obj", "", "file") == ""

    def test_build_iframe_url_empty_file_returns_empty(self) -> None:
        assert build_iframe_url("obj", "folder", "") == ""


# ---------------------------------------------------------------------------
# inject_custom_attributes
# ---------------------------------------------------------------------------

class TestInjectCustomAttributes:
    def test_replaces_input_value(self) -> None:
        html = '<input hpsfcustom="SerialNumber" value="" />'
        result = inject_custom_attributes(html, {"SerialNumber": "SN123"})
        assert 'value="SN123"' in result

    def test_replaces_span_innerhtml(self) -> None:
        html = '<span hpsfcustom="ProductName">old</span>'
        result = inject_custom_attributes(html, {"ProductName": "EliteBook"})
        assert ">EliteBook<" in result

    def test_replaces_multiple_attributes(self) -> None:
        html = (
            '<input hpsfcustom="SerialNumber" value="" />'
            '<span hpsfcustom="ProductName">old</span>'
        )
        result = inject_custom_attributes(
            html, {"SerialNumber": "SN123", "ProductName": "EliteBook"}
        )
        assert 'value="SN123"' in result
        assert ">EliteBook<" in result

    def test_no_match_returns_unchanged(self) -> None:
        html = '<div>no custom attrs</div>'
        result = inject_custom_attributes(html, {"SerialNumber": "SN123"})
        assert result == html

    def test_case_insensitive_attribute_match(self) -> None:
        html = '<input HPSFCUSTOM="SerialNumber" value="" />'
        result = inject_custom_attributes(html, {"SerialNumber": "SN123"})
        assert 'value="SN123"' in result


# ---------------------------------------------------------------------------
# Launcher URL parsing and building
# ---------------------------------------------------------------------------

class TestParseLauncherUrl:
    def test_parse_with_params(self) -> None:
        action, params = parse_launcher_url(
            "hpsalauncher://LearnWin11&SerialNumber=ABC123"
        )
        assert action == "LearnWin11"
        assert params == {"SerialNumber": "ABC123"}

    def test_parse_without_params(self) -> None:
        action, params = parse_launcher_url("hpsalauncher://LearnWin11")
        assert action == "LearnWin11"
        assert params == {}

    def test_parse_multiple_params(self) -> None:
        action, params = parse_launcher_url(
            "hpsalauncher://AutoRepair&SerialNumber=SN&ProductNumber=PN"
        )
        assert action == "AutoRepair"
        assert params == {"SerialNumber": "SN", "ProductNumber": "PN"}

    def test_parse_hpsupportassistant_protocol(self) -> None:
        action, params = parse_launcher_url("hpsupportassistant://AutoRepair")
        assert action == "AutoRepair"
        assert params == {}

    def test_parse_case_insensitive_protocol(self) -> None:
        action, _ = parse_launcher_url("HPSALAUNCHER://LearnWin11")
        assert action == "LearnWin11"

    def test_parse_unknown_protocol_returns_empty(self) -> None:
        action, params = parse_launcher_url("http://example.com")
        assert action == ""
        assert params == {}

    def test_parse_empty_url(self) -> None:
        action, params = parse_launcher_url("")
        assert action == ""
        assert params == {}


class TestBuildLauncherUrl:
    def test_build_with_serial_number(self) -> None:
        url = build_launcher_url("LearnWin11", "ABC1234567890")
        assert url == "hpsalauncher://LearnWin11&SerialNumber=ABC1234567"

    def test_serial_number_truncated_to_10_chars(self) -> None:
        url = build_launcher_url("LearnWin11", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        assert "SerialNumber=ABCDEFGHIJ" in url

    def test_build_not_hp_unit(self) -> None:
        url = build_launcher_url("LearnWin11", "ABC123", is_hp_unit=False)
        assert url == "hpsalauncher://LearnWin11"

    def test_build_empty_serial_number(self) -> None:
        url = build_launcher_url("LearnWin11", "")
        assert url == "hpsalauncher://LearnWin11"


# ---------------------------------------------------------------------------
# HpsaResponse
# ---------------------------------------------------------------------------

class TestHpsaResponse:
    def test_empty_is_success(self) -> None:
        r = HpsaResponse()
        assert r.is_success is True

    def test_with_error_not_success(self) -> None:
        r = HpsaResponse()
        r.add_error("ERR001")
        assert r.is_success is False

    def test_add_error_with_origin(self) -> None:
        r = HpsaResponse()
        r.add_error("ERR001", origin="Custom")
        assert r.FaultItemList[0]["Origin"] == "Custom"
        assert r.FaultItemList[0]["ReturnCode"] == "ERR001"

    def test_to_dict(self) -> None:
        r = HpsaResponse()
        r.add_error("ERR001")
        d = r.to_dict()
        assert d == {"FaultItemList": [{"Origin": "UWP", "ReturnCode": "ERR001"}]}

    def test_default_origin_is_uwp(self) -> None:
        r = HpsaResponse()
        r.add_error("ERR001")
        assert r.FaultItemList[0]["Origin"] == "UWP"


# ---------------------------------------------------------------------------
# _basic_object and _is_local_device
# ---------------------------------------------------------------------------

class TestBasicObject:
    def test_empty_object(self) -> None:
        obj = _basic_object()
        assert obj["FaultItemList"] == []
        assert "FaultItemList" in obj

    def test_with_errors(self) -> None:
        obj = _basic_object("ERR1", "ERR2")
        assert len(obj["FaultItemList"]) == 2
        assert obj["FaultItemList"][0]["ReturnCode"] == "ERR1"
        assert obj["FaultItemList"][1]["ReturnCode"] == "ERR2"

    def test_all_errors_have_uwp_origin(self) -> None:
        obj = _basic_object("ERR1")
        assert obj["FaultItemList"][0]["Origin"] == "UWP"


class TestIsLocalDevice:
    def test_alphanumeric_is_local(self) -> None:
        assert _is_local_device("ABC123") is True

    def test_numeric_is_remote(self) -> None:
        assert _is_local_device("12345") is False

    def test_empty_is_local(self) -> None:
        assert _is_local_device("") is True

    def test_pure_letters_is_local(self) -> None:
        assert _is_local_device("SNID") is True


# ---------------------------------------------------------------------------
# HpsaWebClient — device operations
# ---------------------------------------------------------------------------

class TestWebClientDevices:
    def test_devices_empty_without_windows_backend(self) -> None:
        c = HpsaWebClient()
        result = c.devices()
        assert result["FaultItemList"] == []
        assert "DeviceList" in result
        assert isinstance(result["DeviceList"], list)

    def test_device_details_not_found(self) -> None:
        c = HpsaWebClient()
        result = c.device_details("nonexistent")
        assert len(result["FaultItemList"]) == 1
        assert result["FaultItemList"][0]["ReturnCode"] == "DeviceNotFound"

    def test_device_add_returns_basic(self) -> None:
        c = HpsaWebClient()
        result = c.device_add("MyDevice")
        assert result["FaultItemList"] == []
        assert "DeviceId" in result

    def test_device_remove_returns_basic(self) -> None:
        c = HpsaWebClient()
        result = c.device_remove("123")
        assert result["FaultItemList"] == []

    def test_device_edit_nickname(self) -> None:
        c = HpsaWebClient()
        result = c.device_edit_nickname("123", "NewName")
        assert result["FaultItemList"] == []

    def test_device_get_information(self) -> None:
        c = HpsaWebClient()
        result = c.device_get_information("SN123", "PN456")
        assert result["SerialNumber"] == "SN123"
        assert result["ProductNumber"] == "PN456"


# ---------------------------------------------------------------------------
# HpsaWebClient — scan operations
# ---------------------------------------------------------------------------

class TestWebClientScan:
    def test_scan_messages_no_sudf(self) -> None:
        c = HpsaWebClient()
        result = c.scan_messages("SN")
        assert result["FaultItemList"] == []

    def test_scan_messages_with_sudf(self) -> None:
        sudf = MagicMock()
        sudf.get_messages.return_value = {}
        c = HpsaWebClient(sudf_client=sudf)
        result = c.scan_messages("SN")
        assert result["FaultItemList"] == []
        sudf.get_messages.assert_called_once_with("SN")

    def test_scan_messages_sudf_error(self) -> None:
        sudf = MagicMock()
        sudf.get_messages.side_effect = RuntimeError("boom")
        c = HpsaWebClient(sudf_client=sudf)
        result = c.scan_messages("SN")
        assert len(result["FaultItemList"]) == 1
        assert result["FaultItemList"][0]["ReturnCode"] == "InternalError"

    def test_scan_updates_no_scan_service(self) -> None:
        c = HpsaWebClient()
        result = c.scan_updates("SN")
        assert result["FaultItemList"] == []

    def test_scan_updates_with_service(self) -> None:
        scan_svc = MagicMock()
        scan_svc.scan.return_value = [{"guid": "g1"}, {"guid": "g2"}]
        c = HpsaWebClient(scan_service=scan_svc)
        result = c.scan_updates("SN")
        assert result["UpdatesCount"] == 2
        assert len(result["updates"]) == 2

    def test_scan_updates_service_error(self) -> None:
        scan_svc = MagicMock()
        scan_svc.scan.side_effect = RuntimeError("boom")
        c = HpsaWebClient(scan_service=scan_svc)
        result = c.scan_updates("SN")
        assert result["FaultItemList"][0]["ReturnCode"] == "InternalError"

    def test_scan_messages_and_updates(self) -> None:
        scan_svc = MagicMock()
        scan_svc.scan.return_value = [{"guid": "g1"}]
        c = HpsaWebClient(scan_service=scan_svc)
        result = c.scan_messages_and_updates("SN")
        assert result["UpdatesCount"] == 1


# ---------------------------------------------------------------------------
# HpsaWebClient — update operations
# ---------------------------------------------------------------------------

class TestWebClientUpdates:
    def test_get_all_updates_empty(self) -> None:
        c = HpsaWebClient()
        result = c.get_all_updates()
        assert result["updates"] == []

    def test_get_updates_by_sn_no_refresh(self) -> None:
        c = HpsaWebClient()
        result = c.get_updates_by_sn("SN")
        assert result["UpdatesCount"] == 0
        assert result["updates"] == []

    def test_get_updates_by_sn_with_refresh(self) -> None:
        scan_svc = MagicMock()
        scan_svc.scan.return_value = [{"guid": "g1"}]
        c = HpsaWebClient(scan_service=scan_svc)
        result = c.get_updates_by_sn("SN", refresh=True)
        assert result["UpdatesCount"] == 1

    def test_get_updates_by_sn_refresh_error(self) -> None:
        scan_svc = MagicMock()
        scan_svc.scan.side_effect = RuntimeError("boom")
        c = HpsaWebClient(scan_service=scan_svc)
        result = c.get_updates_by_sn("SN", refresh=True)
        assert result["UpdatesCount"] == 0
        assert result["updates"] == []

    def test_run_updates(self) -> None:
        c = HpsaWebClient()
        result = c.run_updates("dev1")
        assert result["FaultItemList"] == []

    def test_download_install_update_with_installer(self) -> None:
        installer = MagicMock()
        installer.download_and_install.return_value = {"status": "ok"}
        c = HpsaWebClient(installer=installer)
        result = c.download_install_update(["g1"], "SN")
        assert result["downloadInstallUpdate"] == {"status": "ok"}

    def test_download_install_update_installer_error(self) -> None:
        installer = MagicMock()
        installer.download_and_install.side_effect = RuntimeError("boom")
        c = HpsaWebClient(installer=installer)
        result = c.download_install_update(["g1"], "SN")
        assert len(result["FaultItemList"]) == 1
        assert result["FaultItemList"][0]["ReturnCode"] == "InternalError"

    def test_download_install_update_no_installer(self) -> None:
        c = HpsaWebClient()
        result = c.download_install_update(["g1"], "SN")
        assert result["FaultItemList"] == []

    def test_cancel_download_install(self) -> None:
        installer = MagicMock()
        installer.cancel.return_value = True
        c = HpsaWebClient(installer=installer)
        result = c.cancel_download_install(["g1"], "SN")
        assert result["data"] is True

    def test_get_install_result(self) -> None:
        c = HpsaWebClient()
        result = c.get_install_result(["g1"], "SN")
        assert result["data"] is None

    def test_connect_to_internet(self) -> None:
        c = HpsaWebClient()
        result = c.connect_to_internet("SN")
        assert result["connectToInternet"] is True

    def test_check_disk_space(self) -> None:
        c = HpsaWebClient()
        result = c.check_disk_space("g1", "SN")
        assert result["checkDiskSpace"] is True

    def test_create_restore_point_with_installer(self) -> None:
        installer = MagicMock()
        installer.create_restore_point.return_value = True
        c = HpsaWebClient(installer=installer)
        result = c.create_restore_point()
        assert result["createRestorePoint"] is True

    def test_create_restore_point_no_installer(self) -> None:
        c = HpsaWebClient()
        result = c.create_restore_point()
        assert result["createRestorePoint"] is False

    def test_close_update(self) -> None:
        c = HpsaWebClient()
        result = c.close_update()
        assert result["FaultItemList"] == []

    def test_change_update_status(self) -> None:
        c = HpsaWebClient()
        result = c.change_update_status(["g1"], "SN", 1)
        assert result["updateStatus"] is True

    def test_save_loud_install_result(self) -> None:
        c = HpsaWebClient()
        result = c.save_loud_install_result("g1", "SN", 1)
        assert result["updateStatus"] is True

    def test_is_reboot_needed(self) -> None:
        c = HpsaWebClient()
        assert c.is_reboot_needed("SN") is False

    def test_is_update_scan_needed(self) -> None:
        c = HpsaWebClient()
        assert c.is_update_scan_needed("SN") is True


# ---------------------------------------------------------------------------
# HpsaWebClient — messages
# ---------------------------------------------------------------------------

class TestWebClientMessages:
    def test_get_all_messages(self) -> None:
        c = HpsaWebClient()
        result = c.get_all_messages()
        assert result["Messages"] == []

    def test_get_all_archive_messages(self) -> None:
        c = HpsaWebClient()
        assert c.get_all_archive_messages() == []

    def test_device_messages(self) -> None:
        c = HpsaWebClient()
        result = c.device_messages("dev1")
        assert result["Messages"] == []

    def test_alert_edit(self) -> None:
        c = HpsaWebClient()
        result = c.alert_edit("a1", 1, "dev1")
        assert result["FaultItemList"] == []

    def test_alerts(self) -> None:
        c = HpsaWebClient()
        result = c.alerts()
        assert result["Alerts"] == []


# ---------------------------------------------------------------------------
# HpsaWebClient — warranty, health, specs
# ---------------------------------------------------------------------------

class TestWebClientWarrantyHealth:
    def test_device_warranty(self) -> None:
        c = HpsaWebClient()
        result = c.device_warranty("dev1", "SN", "PN")
        assert result["SerialNumber"] == "SN"
        assert result["ProductNumber"] == "PN"
        assert result["WarrantyStatus"] is False

    def test_device_health_battery(self) -> None:
        c = HpsaWebClient()
        result = c.device_health_battery("dev1")
        assert result["Status"] == "unknown"

    def test_device_health_storage(self) -> None:
        c = HpsaWebClient()
        result = c.device_health_storage("dev1")
        assert result["Status"] == "unknown"

    def test_device_health_cooling(self) -> None:
        c = HpsaWebClient()
        result = c.device_health_cooling("dev1")
        assert result["Status"] == "unknown"

    def test_device_specs(self) -> None:
        c = HpsaWebClient()
        result = c.device_specs("dev1")
        assert result["Details"] == []


# ---------------------------------------------------------------------------
# HpsaWebClient — security
# ---------------------------------------------------------------------------

class TestWebClientSecurity:
    def test_get_security_notebook(self) -> None:
        c = HpsaWebClient()
        result = c.get_security("dev1", PRODUCT_TYPE_NOTEBOOK)
        assert "Properties" in result
        for prop in SECURITY_PROPERTIES:
            assert prop.lower() in result["Properties"]

    def test_get_security_desktop(self) -> None:
        c = HpsaWebClient()
        result = c.get_security("dev1", PRODUCT_TYPE_DESKTOP)
        assert len(result["Properties"]) == len(SECURITY_PROPERTIES)

    def test_get_security_printer_empty_properties(self) -> None:
        c = HpsaWebClient()
        result = c.get_security("dev1", PRODUCT_TYPE_PRINTER)
        assert result["Properties"] == {}

    def test_get_security_mobile_empty_properties(self) -> None:
        c = HpsaWebClient()
        result = c.get_security("dev1", PRODUCT_TYPE_MOBILE)
        assert result["Properties"] == {}

    def test_get_security_has_recommended_actions(self) -> None:
        c = HpsaWebClient()
        result = c.get_security("dev1", PRODUCT_TYPE_NOTEBOOK)
        assert "RecommendedActions" in result


# ---------------------------------------------------------------------------
# HpsaWebClient — settings
# ---------------------------------------------------------------------------

class TestWebClientSettings:
    def test_get_settings_defaults(self) -> None:
        c = HpsaWebClient()
        result = c.get_settings()
        assert result["ShowTaskbarIconCheckBox"] is True
        assert result["ShareUsageData"] is False
        assert result["EnableInstallation"] is True
        assert result["ContactCountry"] == "us"
        assert result["ShowWelcome"] is True

    def test_set_settings_updates_values(self) -> None:
        c = HpsaWebClient()
        c.set_settings({
            "ShowTaskbarIconCheckBox": False,
            "ShareUsageData": True,
        })
        result = c.get_settings()
        assert result["ShowTaskbarIconCheckBox"] is False
        assert result["ShareUsageData"] is True

    def test_set_settings_check_for_updates_mapping(self) -> None:
        c = HpsaWebClient()
        # Reverse mapping: UI value -> internal value
        c.set_settings({"CheckForUpdatesAndMessages": CHECK_FOR_UPDATES_MAPPING[1]})
        assert c._settings.check_for_updates == 1

    def test_check_for_updates_mapping_values(self) -> None:
        assert CHECK_FOR_UPDATES_MAPPING[0] == 3  # Never
        assert CHECK_FOR_UPDATES_MAPPING[1] == 2  # Check only
        assert CHECK_FOR_UPDATES_MAPPING[2] == 0  # Important auto
        assert CHECK_FOR_UPDATES_MAPPING[3] == 1  # Important+recommended auto


# ---------------------------------------------------------------------------
# HpsaWebClient — profile
# ---------------------------------------------------------------------------

class TestWebClientProfile:
    def test_get_profile_defaults(self) -> None:
        c = HpsaWebClient()
        result = c.get_profile()
        assert result["FirstName"] is None
        assert result["Email"] is None
        assert result["Country"] is None

    def test_set_profile(self) -> None:
        c = HpsaWebClient()
        result = c.set_profile({
            "Country": "US",
            "FirstName": "John",
            "LastName": "Doe",
            "Email": "john@example.com",
            "EmailOffers": True,
        })
        assert result["FaultItemList"] == []

    def test_is_user_logged_in(self) -> None:
        c = HpsaWebClient()
        assert c.is_user_logged_in() is False

    def test_logout(self) -> None:
        c = HpsaWebClient()
        result = c.logout()
        assert result["logoutSucceed"] is True


# ---------------------------------------------------------------------------
# HpsaWebClient — cases
# ---------------------------------------------------------------------------

class TestWebClientCases:
    def test_create_case(self) -> None:
        c = HpsaWebClient()
        result = c.create_case({"subject": "test"})
        assert result["FaultItemList"] == []

    def test_get_cases(self) -> None:
        c = HpsaWebClient()
        result = c.get_cases()
        assert result["FaultItemList"] == []

    def test_get_cases_for_device(self) -> None:
        c = HpsaWebClient()
        result = c.get_cases_for_device("SN")
        assert result["FaultItemList"] == []

    def test_get_case_by_number(self) -> None:
        c = HpsaWebClient()
        result = c.get_case_by_number("123")
        assert result["FaultItemList"] == []

    def test_request_to_close_case(self) -> None:
        c = HpsaWebClient()
        result = c.request_to_close_case("case1")
        assert result["FaultItemList"] == []

    def test_get_service_centers(self) -> None:
        c = HpsaWebClient()
        result = c.get_service_centers("SN", "12345", "filter", 10)
        assert result["FaultItemList"] == []

    def test_get_primary_support_options(self) -> None:
        c = HpsaWebClient()
        result = c.get_primary_support_options()
        assert result["FaultItemList"] == []

    def test_get_working_hours(self) -> None:
        c = HpsaWebClient()
        result = c.get_working_hours("dev1")
        assert result["FaultItemList"] == []

    def test_send_feedback(self) -> None:
        c = HpsaWebClient()
        result = c.send_feedback({"msg": "good"})
        assert result["FaultItemList"] == []


# ---------------------------------------------------------------------------
# HpsaWebClient — launcher/external
# ---------------------------------------------------------------------------

class TestWebClientLauncher:
    def test_get_external_url(self) -> None:
        c = HpsaWebClient()
        result = c.get_external_url("LearnEULA", "https://example.com")
        assert result["URL"] == "https://example.com"

    def test_call_custom_action_uri(self) -> None:
        c = HpsaWebClient()
        result = c.call_custom_action("URI", ["https://example.com"])
        assert result["URL"] == "https://example.com"

    def test_call_custom_action_launcher(self) -> None:
        c = HpsaWebClient()
        result = c.call_custom_action("Launcher", ["hpsalauncher://x"])
        assert result["URL"] == "hpsalauncher://x"

    def test_call_custom_action_empty_params(self) -> None:
        c = HpsaWebClient()
        result = c.call_custom_action("URI", [])
        assert result["URL"] == ""

    def test_get_uuid_with_windows(self) -> None:
        windows = MagicMock()
        windows.get_bios_info.return_value = {"sys_id": "8A4F"}
        c = HpsaWebClient(windows_backend=windows)
        assert c.get_uuid() == "8A4F"

    def test_get_uuid_no_windows(self) -> None:
        c = HpsaWebClient()
        assert c.get_uuid() == ""

    def test_get_uuid_windows_error(self) -> None:
        windows = MagicMock()
        windows.get_bios_info.side_effect = RuntimeError("boom")
        c = HpsaWebClient(windows_backend=windows)
        assert c.get_uuid() == ""

    def test_get_message_path(self) -> None:
        c = HpsaWebClient()
        assert c.get_message_path("MyMsg", "en-US") == "solutions/en-US/MyMsg.html"


# ---------------------------------------------------------------------------
# dispatch_action
# ---------------------------------------------------------------------------

class TestDispatchAction:
    def test_dispatch_known_action(self) -> None:
        c = HpsaWebClient()
        result = dispatch_action(c, "connectToInternet", {"serial_number": "SN"})
        assert result["connectToInternet"] is True

    def test_dispatch_unknown_action(self) -> None:
        c = HpsaWebClient()
        result = dispatch_action(c, "Nonexistent", {})
        assert result["FaultItemList"][0]["ReturnCode"] == "ActionNotSupported"

    def test_dispatch_devices(self) -> None:
        c = HpsaWebClient()
        result = dispatch_action(c, "devices", {})
        assert "DeviceList" in result

    def test_dispatch_get_settings(self) -> None:
        c = HpsaWebClient()
        result = dispatch_action(c, "getSettings", {})
        assert "ShowTaskbarIconCheckBox" in result

    def test_dispatch_all_actions_resolve_to_methods(self) -> None:
        """Every ACTION_DISPATCH entry maps to an existing method on the client."""
        c = HpsaWebClient()
        for action, method_name in ACTION_DISPATCH.items():
            method = getattr(c, method_name, None)
            assert method is not None, f"{action} -> {method_name} not found"
            assert callable(method), f"{action} -> {method_name} not callable"
