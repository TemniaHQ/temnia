"""Object storage through obstore: download the master, upload artifact trees."""

from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

import boto3
import obstore as obs
from botocore.config import Config
from obstore.store import S3Store

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mypy_boto3_s3 import S3Client

if TYPE_CHECKING:
    from pathlib import Path

    from temnia_pipeline.settings import StorageSettings

UPLOAD_CONCURRENCY = 8
CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".jpg": "image/jpeg",
    ".json": "application/json",
}


def make_store(settings: StorageSettings) -> S3Store:
    """A store bound to the bucket; plain HTTP is allowed for compose."""
    return S3Store(
        settings.bucket,
        endpoint=settings.endpoint,
        region=settings.region,
        access_key_id=settings.access_key_id,
        secret_access_key=settings.secret_access_key,
        virtual_hosted_style_request=False,
        client_options={"allow_http": True, "timeout": timedelta(hours=6)},
    )


def content_type_for(path: Path) -> str:
    """Content type by extension; the proxy trusts what the store returns."""
    known = CONTENT_TYPES.get(path.suffix.lower())
    return known or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


async def download(store: S3Store, key: str, dest: Path, *, expected_size: int | None) -> int:
    """Stream an object to disk and verify its size when known."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = await obs.get_async(store, key)
    size = 0
    with dest.open("wb") as handle:
        async for chunk in result.stream(min_chunk_size=8 * 1024 * 1024):
            handle.write(chunk)
            size += len(chunk)
    if expected_size is not None and size != expected_size:
        msg = f"downloaded {size} bytes of {expected_size} for {key}"
        raise OSError(msg)
    return size


async def read_text(store: S3Store, key: str) -> str | None:
    """Fetch a small object as text; None when the key is not there.

    For completion markers, where absent is an answer and not a fault.
    """
    try:
        result = await obs.get_async(store, key)
    except FileNotFoundError:
        return None
    return bytes(await result.bytes_async()).decode()


async def read_json(store: S3Store, key: str) -> dict[str, Any] | None:
    """Fetch a JSON object; None when the key is not there.

    Python's json reads the bare NaN a whisperx alignment score can be, which
    is why the engine's own response travels as a file rather than through a
    parser that would refuse it.
    """
    text = await read_text(store, key)
    if text is None:
        return None
    loaded: object = json.loads(text)
    if not isinstance(loaded, dict):
        msg = f"{key} is not a JSON object"
        raise TypeError(msg)
    return cast("dict[str, Any]", loaded)


async def key_exists(store: S3Store, key: str) -> bool:
    """True when the object is in the store."""
    try:
        await obs.head_async(store, key)
    except FileNotFoundError:
        return False
    return True


async def upload_file(store: S3Store, key: str, path: Path) -> int:
    """Put one file; returns its size."""
    with path.open("rb") as handle:
        await obs.put_async(
            store,
            key,
            handle,
            attributes={"Content-Type": content_type_for(path)},
        )
    return path.stat().st_size


async def upload_bytes(store: S3Store, key: str, body: bytes, content_type: str) -> int:
    """Put one small object built in memory; returns its size."""
    await obs.put_async(store, key, body, attributes={"Content-Type": content_type})
    return len(body)


async def upload_tree(
    store: S3Store,
    prefix: str,
    root: Path,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> int:
    """Upload every file under `root` to `prefix`; returns total bytes.

    `on_progress(done_bytes, total_bytes)` is awaited after every file. A
    2.5-hour ladder is about 4,500 segments and several gigabytes; without a
    heartbeat inside this loop the activity times out mid-publish (staging,
    2026-09-06) and the retry throws the finished ladder away.
    """
    files = [p for p in root.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    done = 0
    semaphore = asyncio.Semaphore(UPLOAD_CONCURRENCY)

    async def one(path: Path) -> int:
        nonlocal done
        async with semaphore:
            key = prefix + path.relative_to(root).as_posix()
            size = await upload_file(store, key, path)
            done += size
            if on_progress is not None:
                await on_progress(done, total)
            return size

    sizes = await asyncio.gather(*(one(p) for p in files))
    return sum(sizes)


async def list_keys(store: S3Store, prefix: str) -> list[str]:
    """Every key under the prefix."""
    keys: list[str] = []
    async for page in obs.list(store, prefix):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        keys.extend(str(item["path"]) for item in page)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    return keys


async def delete_prefix(store: S3Store, prefix: str) -> int:
    """Delete every object under the prefix; returns the count."""
    keys = await list_keys(store, prefix)
    if keys:
        await obs.delete_async(store, keys)
    return len(keys)


def make_control_client(settings: StorageSettings) -> S3Client:
    """boto3 for the one call obstore lacks (AbortMultipartUpload).

    Checksums are computed only when required: the SDK's default CRC32 headers
    are rejected by R2 and were rejected by older Garage.
    """
    return boto3.client(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        "s3",
        endpoint_url=settings.endpoint,
        region_name=settings.region,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        config=Config(
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={"addressing_style": "path"},
        ),
    )


async def abort_multipart(settings: StorageSettings, key: str, upload_id: str) -> None:
    """Abort a multipart upload the reaper found idle; a missing upload is fine."""
    client = make_control_client(settings)

    def run() -> None:
        with contextlib.suppress(client.exceptions.NoSuchUpload):
            client.abort_multipart_upload(Bucket=settings.bucket, Key=key, UploadId=upload_id)

    await asyncio.to_thread(run)
