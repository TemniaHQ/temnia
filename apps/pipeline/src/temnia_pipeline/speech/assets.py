"""Verified atomic installation for the explicitly prepared Silero asset."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import BinaryIO, Protocol

REVISION = "7e30209a3e901f9842f81b225f3e93d8199902b1"
URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    f"{REVISION}/src/silero_vad/data/silero_vad_16k_op15.onnx"
)
SIZE_BYTES = 1_289_603
SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"
DOWNLOAD_TIMEOUT_SECONDS = 30


class Opener(Protocol):
    """Injectable URL opener used by the offline unit tests."""

    def __call__(self, url: str) -> BinaryIO:
        """Open a byte stream."""
        ...


def _open_url(url: str) -> BinaryIO:
    return urllib.request.urlopen(  # noqa: S310
        url, timeout=DOWNLOAD_TIMEOUT_SECONDS
    )


def verify_asset(body: bytes) -> None:
    """Reject any upstream replacement, truncation, or wrong asset."""
    if len(body) != SIZE_BYTES:
        message = f"Silero asset is {len(body)} bytes; expected {SIZE_BYTES}"
        raise ValueError(message)
    digest = hashlib.sha256(body).hexdigest()
    if digest != SHA256:
        message = f"Silero asset sha256 is {digest}; expected {SHA256}"
        raise ValueError(message)


def install_asset(destination: Path, *, opener: Opener = _open_url) -> bool:
    """Verify and atomically install; return False when a valid asset already exists."""
    if destination.exists():
        verify_asset(destination.read_bytes())
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with opener(URL) as response:
            body = response.read(SIZE_BYTES + 1)
        verify_asset(body)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
        temporary = None
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
