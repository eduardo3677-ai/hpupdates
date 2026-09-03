import hashlib
from pathlib import Path

import httpx
import pytest

from hpupdates.infrastructure.downloader import Downloader, DownloadError


def test_downloads_https_file_and_verifies_sha256(tmp_path: Path) -> None:
    payload = b"signed-driver-placeholder"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    destination = tmp_path / "driver.exe"

    result = Downloader(httpx.Client(transport=transport)).download(
        "https://ftp.hp.com/driver.exe",
        destination,
        hashlib.sha256(payload).hexdigest(),
    )

    assert result == destination
    assert destination.read_bytes() == payload


def test_rejects_non_https_download(tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="HTTPS"):
        Downloader().download("http://example.test/driver.exe", tmp_path / "driver.exe", "a" * 64)


def test_removes_file_when_hash_does_not_match(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"tampered"))
    destination = tmp_path / "driver.exe"

    with pytest.raises(DownloadError, match="SHA-256"):
        Downloader(httpx.Client(transport=transport)).download(
            "https://ftp.ext.hp.com/driver.exe", destination, "a" * 64
        )

    assert not destination.exists()


def test_rejects_unapproved_https_host_before_network_or_file_creation(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"payload")

    destination = tmp_path / "driver.exe"
    with pytest.raises(DownloadError, match="approved"):
        Downloader(httpx.Client(transport=httpx.MockTransport(handler))).download(
            "https://ftp.hp.com.attacker.test/driver.exe", destination, "a" * 64
        )
    assert calls == 0
    assert not destination.exists()


def test_rejects_redirect_without_following_it(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://example.test/file.exe"})

    with pytest.raises(DownloadError, match="redirect"):
        Downloader(httpx.Client(transport=httpx.MockTransport(handler))).download(
            "https://ftp.hp.com/file.exe", tmp_path / "driver.exe", "a" * 64
        )
    assert calls == 1
