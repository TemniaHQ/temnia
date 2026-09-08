"""Bounded inventory checks use the real local obstore implementation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
from obstore.store import MemoryStore

from temnia_pipeline import storage, transcode

if TYPE_CHECKING:
    from obstore.store import S3Store


async def test_real_obstore_listing_crosses_pages_and_enforces_the_limit() -> None:
    store = cast("S3Store", MemoryStore())
    for number in range(1051):
        await storage.upload_bytes(store, f"hls/{number:05d}", b"test", "video/mp4")
    objects = await storage.list_objects(store, "hls/", max_objects=1051)
    assert len(objects) == 1051
    assert sum(size for _, size in objects) == 4204
    with pytest.raises(ValueError, match="inventory exceeds 1000"):
        await storage.list_objects(store, "hls/", max_objects=1000)
    with pytest.raises(ValueError, match="object exceeds 3 bytes"):
        await storage.read_text(store, "hls/00000", max_bytes=3)


async def test_slow_inventory_heartbeats_saved_handle_and_cancels_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = asyncio.Event()
    reports: list[str | None] = []

    async def blocked_read(
        store: S3Store,
        key: str,
        *,
        max_bytes: int | None = None,
    ) -> str | None:
        _ = store, key, max_bytes
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        return None

    async def heartbeat(progress: transcode.LadderProgress, handle: str | None) -> None:
        assert progress.stage == "publish"
        reports.append(handle)

    monkeypatch.setattr(transcode, "read_text", blocked_read)
    monkeypatch.setattr(transcode, "INVENTORY_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(transcode, "INVENTORY_TIMEOUT_SECONDS", 0.03)
    job = transcode.LadderJob(
        master_key="source/master.mp4",
        artifact_prefix="source/",
        size_bytes=1,
        video=None,
        has_audio=True,
        expected_seconds=2,
    )
    with pytest.raises(TimeoutError):
        await transcode.stored_ladder(
            cast("S3Store", MemoryStore()), job, on_progress=heartbeat, resume="fc-still-running"
        )
    assert reports
    assert set(reports) == {"fc-still-running"}
    assert cancelled.is_set()
