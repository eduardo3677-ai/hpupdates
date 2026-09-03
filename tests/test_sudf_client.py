"""Tests for the SUDF client — crypto, signing, and API operations."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from hpupdates.infrastructure.sudf import (
    MessagesRequest,
    PrinterUpdatesRequest,
    SudfAuthenticationError,
    SudfClient,
    SudfEnvironment,
    SudfRequest,
    aes_decrypt_string,
    create_sha256_cache_id,
    decrypt_embedded_api_key,
    get_dpapi_entropy,
    sign_request,
    to_guid,
)

# ---------------------------------------------------------------------------
# Crypto tests
# ---------------------------------------------------------------------------

class TestAesDecryption:
    def test_decrypt_v4_api_key(self) -> None:
        """V4 API key should decrypt to a 40-char string with known SHA-256."""
        key = decrypt_embedded_api_key("V4")
        assert len(key) == 40
        assert all(c.isascii() and c.isprintable() for c in key)
        assert hashlib.sha256(key.encode()).hexdigest() == (
            "a063e2cf52fa67abe2a4fce37e7b6ca5e6ef57353ab7b438536f8f969cd8465b"
        )

    def test_decrypt_v3_api_key(self) -> None:
        """V3 API key should decrypt to a 40-char string with known SHA-256."""
        key = decrypt_embedded_api_key("V3")
        assert len(key) == 40
        assert all(c.isascii() and c.isprintable() for c in key)
        assert hashlib.sha256(key.encode()).hexdigest() == (
            "78dcc8f2a751dd2d7a6841fbf4d0916b584ef78db02864837bcc17e33582957f"
        )

    def test_aes_decrypt_string_roundtrip(self) -> None:
        """aes_decrypt_string should reverse AES-256-CBC encryption."""
        from cryptography.hazmat.primitives import padding as sym_padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key_str = "APIKEY_V4"
        iv_str = "APIKEYIV"
        aes_key = hashlib.sha256(key_str.encode()).digest()
        aes_iv = hashlib.sha256(iv_str.encode()).digest()[:16]
        plaintext = b"test_secret_value_123"
        padder = sym_padding.PKCS7(128).padder()
        padded = padder.update(plaintext) + padder.finalize()
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv))
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        import base64
        encrypted_b64 = base64.b64encode(encrypted).decode()
        result = aes_decrypt_string(encrypted_b64, key_str, iv_str)
        assert result == plaintext.decode()


class TestSigV4Signing:
    def test_sign_request_produces_valid_header(self) -> None:
        """sign_request should produce an AWS4-HMAC-SHA256 Authorization header."""
        auth = sign_request(
            method="POST",
            api_name="GetUpdatesBySysId",
            host="sudf-api.hpcloud.hp.com",
            payload_hash=hashlib.sha256(b"{}").hexdigest(),
            content_length="2",
            amz_date="20240101T000000Z",
            date_stamp="20240101",
            api_key="test_key",
            secret="",
        )
        assert auth.startswith(
            "AWS4-HMAC-SHA256 Credential=/20240101/us-west-2/apigateway/aws4_request"
        )
        assert "SignedHeaders=content-type;host;x-amz-date;x-api-key" in auth
        assert "Signature=" in auth
        # Signature should be a 64-char hex string
        sig = auth.rsplit("Signature=", 1)[1]
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_sign_request_is_deterministic(self) -> None:
        """Same inputs should always produce the same signature."""
        kwargs = dict(
            method="POST",
            api_name="GetUpdatesBySysId",
            host="sudf-api.hpcloud.hp.com",
            payload_hash="abc123",
            content_length="100",
            amz_date="20240101T000000Z",
            date_stamp="20240101",
            api_key="test_key",
            secret="",
        )
        auth1 = sign_request(**kwargs)
        auth2 = sign_request(**kwargs)
        assert auth1 == auth2


# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------

class TestSudfClient:
    def test_default_credentials_use_embedded_key(self) -> None:
        """Default credentials should use the embedded V4 API key."""
        client = SudfClient()
        assert client.credentials.api_key == decrypt_embedded_api_key("V4")
        assert len(client.credentials.api_key) == 40

    def test_production_endpoint(self) -> None:
        """Production environment should resolve to the known endpoint."""
        client = SudfClient()
        assert client.base_url == "https://sudf-api.hpcloud.hp.com/v3"
        assert client.host == "sudf-api.hpcloud.hp.com"

    def test_custom_endpoint(self) -> None:
        """Custom environment should use the user-supplied URL."""
        client = SudfClient(
            environment=SudfEnvironment.custom,
            custom_url="https://custom-sudf.example.com/v3",
        )
        assert client.base_url == "https://custom-sudf.example.com/v3"
        assert client.host == "custom-sudf.example.com"

    def test_custom_endpoint_prepends_https(self) -> None:
        """Custom URL without https:// should have it prepended."""
        client = SudfClient(
            environment="custom",
            custom_url="custom-sudf.example.com/v3",
        )
        assert client.base_url == "https://custom-sudf.example.com/v3"

    def test_custom_endpoint_requires_url(self) -> None:
        """Custom environment without a URL should raise."""
        with pytest.raises(SudfAuthenticationError, match="custom URL is required"):
            SudfClient(environment="custom")

    def test_custom_endpoint_appends_v3(self) -> None:
        """Custom URL without /v3 should have it appended."""
        client = SudfClient(
            environment="custom",
            custom_url="https://custom-sudf.example.com",
        )
        assert client.base_url == "https://custom-sudf.example.com/v3"

    def test_invalid_environment_raises(self) -> None:
        """Unknown environment should raise ValueError."""
        with pytest.raises(ValueError):
            SudfClient(environment="staging")


# ---------------------------------------------------------------------------
# Request model tests
# ---------------------------------------------------------------------------

class TestSudfRequest:
    def test_payload_fields(self) -> None:
        req = SudfRequest(
            use_case="HPSF",
            system_id="12345",
            country="US",
            language="en",
            os_code="W10x64",
            automatic=True,
        )
        payload = req.payload()
        assert payload["UseCase"] == "HPSF"
        assert payload["SysId"] == "12345"
        assert payload["Auto"] == "1"
        assert payload["OS"] == "W10x64"

    def test_cache_material(self) -> None:
        req = SudfRequest(
            use_case="HPSF",
            system_id="12345",
            os_code="W10x64",
        )
        # Cache material is concatenation of SysId+UseCase+OS+Auto+Country+Language
        material = req.cache_material()
        assert "12345" in material
        assert "HPSF" in material


class TestPrinterUpdatesRequest:
    def test_payload_fields(self) -> None:
        req = PrinterUpdatesRequest(
            product_number="L12345",
            model_name="HP Test",
            use_case="HPSF",
            os="W10x64",
            os_code="22222",
        )
        payload = req.payload()
        assert payload["productNumber"] == "L12345"
        assert payload["modelName"] == "HP Test"
        assert payload["UseCase"] == "HPSF"
        assert payload["OS"] == "W10x64"
        assert payload["OSCode"] == "22222"
        assert payload["ignoreLocale"] is False

    def test_cache_material_format(self) -> None:
        req = PrinterUpdatesRequest(
            product_number="L12345",
            model_name="HP",
            use_case="HPSF",
        )
        material = req.cache_material()
        # Format: .{pn}.{model}.{lc}.{cc}.{pid}.{uc}.{os}.{oscode}.{ignoreLocale}
        assert material.startswith(".L12345.HP.")
        assert material.endswith(".False")


class TestMessagesRequest:
    def test_payload_fields(self) -> None:
        req = MessagesRequest(use_case="HPSF", pl="en", country_cd="US")
        payload = req.payload()
        assert payload["UseCase"] == "HPSF"
        assert payload["PL"] == "en"
        assert payload["CountryCd"] == "US"


# ---------------------------------------------------------------------------
# Mock API response tests
# ---------------------------------------------------------------------------

class TestApiCalls:
    """Test API calls with mocked HTTP responses."""

    def test_get_updates_by_sysid_success(self) -> None:
        """get_updates_by_sysid should parse a successful response."""
        mock_response_data = {
            "Updates": [
                {"Guid": "test-guid", "Code": "SP12345", "Title": "Test Update"}
            ],
        }
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=mock_response_data)
        )
        client = SudfClient(client=httpx.Client(transport=transport))
        result = client.get_updates_by_sysid(
            SudfRequest(use_case="HPSF", system_id="12345")
        )
        assert "Updates" in result
        assert len(result["Updates"]) == 1

    def test_get_updates_by_sysid_fault(self) -> None:
        """get_updates_by_sysid should raise on FaultItemList."""
        mock_response_data = {
            "FaultItemList": [
                {"ReturnCode": "ERR001", "FieldName": "SysId"}
            ],
        }
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=mock_response_data)
        )
        client = SudfClient(client=httpx.Client(transport=transport))
        with pytest.raises(SudfAuthenticationError, match="fault response"):
            client.get_updates_by_sysid(
                SudfRequest(use_case="HPSF", system_id="bad")
            )

    def test_get_printer_updates_success(self) -> None:
        """get_printer_updates should parse a successful response."""
        mock_response_data = {
            "PrinterUpdates": [
                {
                    "SoftwareId": "123",
                    "ProductNumber": "L12345",
                    "HttpURL": "https://example.com/driver.exe",
                    "FtpURL": "ftp://example.com/driver.exe",
                    "Title": "Test Printer Driver",
                    "Version": "1.0.0",
                    "InstallCmd": "driver.exe /s",
                }
            ],
        }
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=mock_response_data)
        )
        client = SudfClient(client=httpx.Client(transport=transport))
        result = client.get_printer_updates(
            PrinterUpdatesRequest(product_number="L12345")
        )
        assert "PrinterUpdates" in result
        assert result["PrinterUpdates"][0]["HttpURL"] == "https://example.com/driver.exe"

    def test_get_messages_success(self) -> None:
        """get_messages should parse a successful response."""
        mock_response_data = {
            "Sequence": 1,
            "Messages": [
                {"Guid": "msg-1", "Title": "Test Message", "Severity": 2}
            ],
        }
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=mock_response_data)
        )
        client = SudfClient(client=httpx.Client(transport=transport))
        result = client.get_messages(MessagesRequest())
        assert "Messages" in result
        assert result["Messages"][0]["Guid"] == "msg-1"

    def test_http_error_raises(self) -> None:
        """HTTP errors should raise SudfAuthenticationError."""
        transport = httpx.MockTransport(
            lambda req: httpx.Response(500, text="Internal Server Error")
        )
        client = SudfClient(client=httpx.Client(transport=transport))
        with pytest.raises(SudfAuthenticationError, match="HTTP 500"):
            client.get_updates_by_sysid(
                SudfRequest(use_case="HPSF", system_id="12345")
            )


# ---------------------------------------------------------------------------
# Operation validation tests
# ---------------------------------------------------------------------------

class TestOperationValidation:
    def test_unknown_operation_rejected(self) -> None:
        """Unknown operations should be rejected before making a request."""
        client = SudfClient()
        with pytest.raises(SudfAuthenticationError, match="unknown SUDF operation"):
            client._post("InvalidOp", {}, "")


# ---------------------------------------------------------------------------
# Driver download tests
# ---------------------------------------------------------------------------

class TestDriverDownload:
    def test_download_driver_no_url_raises(self, tmp_path) -> None:
        """download_driver should raise if no URL in update entry."""
        client = SudfClient()
        with pytest.raises(Exception, match="no HttpURL or FtpURL"):
            client.download_driver({}, tmp_path)

    def test_download_driver_http(self, tmp_path) -> None:
        """download_driver should download from HttpURL."""
        mock_data = b"fake driver binary content"
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, content=mock_data)
        )
        client = SudfClient(client=httpx.Client(transport=transport))
        update = {
            "HttpURL": "https://example.com/SP12345.exe",
            "SoftwareId": "123",
        }
        path = client.download_driver(update, tmp_path)
        assert path.exists()
        assert path.name == "SP12345.exe"
        assert path.read_bytes() == mock_data

    def test_download_driver_fallback_ftp(self, tmp_path) -> None:
        """download_driver should fallback to FtpURL if HttpURL is empty."""
        client = SudfClient()
        update = {"FtpURL": "", "HttpURL": ""}
        with pytest.raises(Exception, match="no HttpURL or FtpURL"):
            client.download_driver(update, tmp_path)

    def test_force_https_replaces_http_scheme(self) -> None:
        """_force_https should replace http:// with https://."""
        assert SudfClient._force_https("http://ftp.hp.com/file.exe") == (
            "https://ftp.hp.com/file.exe"
        )
        assert SudfClient._force_https("https://ftp.hp.com/file.exe") == (
            "https://ftp.hp.com/file.exe"
        )
        assert SudfClient._force_https("ftp://ftp.hp.com/file.exe") == (
            "ftp://ftp.hp.com/file.exe"
        )

    def test_download_driver_forces_https(self, tmp_path) -> None:
        """download_driver should force HTTPS on HTTP URLs."""
        mock_data = b"driver content"
        captured_urls = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured_urls.append(str(req.url))
            return httpx.Response(200, content=mock_data)

        transport = httpx.MockTransport(handler)
        client = SudfClient(client=httpx.Client(transport=transport))
        update = {"HttpURL": "http://ftp.hp.com/SP12345.exe", "SoftPaqId": "sp12345"}
        path = client.download_driver(update, tmp_path)
        assert path.exists()
        # URL should have been forced to HTTPS
        assert all("https://" in url for url in captured_urls)

    def test_verify_softpaq_checksum_match(self, tmp_path) -> None:
        """verify_softpaq_checksum should return True when MD5 matches."""
        content = b"fake driver binary"
        local_file = tmp_path / "SP12345.exe"
        local_file.write_bytes(content)
        expected_md5 = hashlib.md5(content).hexdigest()

        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, text=expected_md5)
        )
        client = SudfClient(client=httpx.Client(transport=transport))
        assert client.verify_softpaq_checksum("sp12345", local_file) is True

    def test_verify_softpaq_checksum_mismatch(self, tmp_path) -> None:
        """verify_softpaq_checksum should return False when MD5 differs."""
        local_file = tmp_path / "SP12345.exe"
        local_file.write_bytes(b"actual content")

        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, text="d41d8cd98f00b204e9800998ecf8427e")
        )
        client = SudfClient(client=httpx.Client(transport=transport))
        assert client.verify_softpaq_checksum("sp12345", local_file) is False

    def test_download_driver_with_signature_downloads_hpsign(self, tmp_path) -> None:
        """download_driver_with_signature should download .hpsign if available."""
        driver_data = b"driver binary"
        sig_data = b"CASL signature data"

        def handler(req: httpx.Request) -> httpx.Response:
            url = str(req.url)
            if url.endswith(".hpsign"):
                return httpx.Response(200, content=sig_data)
            # Checksum service returns the correct MD5
            if "checksum" in url:
                return httpx.Response(
                    200, text=hashlib.md5(driver_data).hexdigest()
                )
            return httpx.Response(200, content=driver_data)

        transport = httpx.MockTransport(handler)
        client = SudfClient(client=httpx.Client(transport=transport))
        update = {
            "HttpURL": "https://ftp.hp.com/pub/softpaq/sp71001-71500/sp71234.exe",
            "SoftPaqId": "sp71234",
        }
        driver_path, sig_path = client.download_driver_with_signature(
            update, tmp_path
        )
        assert driver_path.exists()
        assert driver_path.read_bytes() == driver_data
        assert sig_path is not None
        assert sig_path.exists()
        assert sig_path.read_bytes() == sig_data


# ---------------------------------------------------------------------------
# Additional cryptographic operations tests
# ---------------------------------------------------------------------------

class TestAdditionalCrypto:
    def test_create_sha256_cache_id_is_uppercase_hex(self) -> None:
        """create_sha256_cache_id should return uppercase hex SHA-256."""
        result = create_sha256_cache_id("test material")
        assert result == hashlib.sha256(b"test material").hexdigest().upper()
        assert result == result.upper()
        assert len(result) == 64

    def test_to_guid_is_deterministic(self) -> None:
        """to_guid should produce a deterministic UUID from a string."""
        guid1 = to_guid("test string")
        guid2 = to_guid("test string")
        assert guid1 == guid2
        # Should be a valid UUID format
        assert len(guid1) == 36
        assert guid1.count("-") == 4

    def test_to_guid_different_inputs_different_output(self) -> None:
        """Different inputs should produce different GUIDs."""
        assert to_guid("input1") != to_guid("input2")

    def test_get_dpapi_entropy_is_correct_value(self) -> None:
        """get_dpapi_entropy should return the deobfuscated entropy bytes."""
        entropy = get_dpapi_entropy()
        assert entropy == b"gi2FGj2hw9o$"
        assert len(entropy) == 12
