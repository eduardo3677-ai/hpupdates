from __future__ import annotations


import hashlib
from pathlib import Path

import httpx

from hpupdates.infrastructure.endpoints import EndpointPolicyError, require_softpaq_url


class DownloadError(RuntimeError):
    pass


class Downloader:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(follow_redirects=False, timeout=60.0)

    def download(self, url: str, destination: Path, expected_sha256: str) -> Path:
        try:
            require_softpaq_url(url)
        except EndpointPolicyError as exc:
            raise DownloadError(str(exc)) from exc
        if not expected_sha256 or len(expected_sha256) != 64:
            raise DownloadError("a valid SHA-256 digest is required")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        try:
            with self.client.stream("GET", url) as response:
                if response.is_redirect:
                    raise DownloadError("package download redirects are not allowed")
                response.raise_for_status()
                with temporary.open("wb") as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
                        digest.update(chunk)
            if digest.hexdigest().lower() != expected_sha256.lower():
                raise DownloadError("SHA-256 verification failed")
            temporary.replace(destination)
            return destination
        except Exception:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
