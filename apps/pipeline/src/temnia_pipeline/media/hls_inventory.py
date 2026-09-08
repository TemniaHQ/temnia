"""The exact artifacts a self-contained, unencrypted fMP4 VOD ladder references.

Object sizes prove presence and length, not equal-sized media corruption.
Playlist hashes pin the references and timing that were validated at publish.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

MAX_OBJECTS = 250_000
MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
_URI = re.compile(r'(?:^|,)URI="([^"]+)"(?:,|$)')
_EXTINF = re.compile(r"^#EXTINF:([^,]+),", re.MULTILINE)
_RANGE = re.compile(r'(?:^|,)BYTERANGE="([^"]+)"(?:,|$)')


def playlist_hash(text: str) -> str:
    """Hash the exact UTF-8 playlist bytes uploaded to storage."""
    return hashlib.sha256(text.encode()).hexdigest()


def _resolve(playlist: str, uri: str) -> str:
    path = PurePosixPath(uri)
    if (
        not uri
        or path.is_absolute()
        or any(part in {".", ".."} for part in uri.split("/"))
        or any(char in uri for char in (":", "?", "#", "\\", "%"))
    ):
        msg = f"unsafe HLS reference in {playlist}: {uri!r}"
        raise ValueError(msg)
    return (PurePosixPath(playlist).parent / path).as_posix()


def _range_end(value: str, size: int, previous_end: int | None) -> int:
    parts = value.split("@")
    try:
        length = int(parts[0])
        offset = int(parts[1]) if len(parts) == 2 else previous_end  # noqa: PLR2004
    except ValueError as error:
        msg = "invalid HLS byte range"
        raise ValueError(msg) from error
    if len(parts) > 2 or offset is None or length <= 0 or offset < 0 or offset + length > size:  # noqa: PLR2004
        msg = "HLS byte range exceeds its media object or has no preceding offset"
        raise ValueError(msg)
    return offset + length


def playlist_duration(text: str) -> float:
    """The finite, positive duration described by a media playlist's segments."""
    durations = [float(value) for value in _EXTINF.findall(text)]
    if not durations or any(not math.isfinite(value) or value <= 0 for value in durations):
        msg = "missing or invalid HLS segment duration"
        raise ValueError(msg)
    return sum(durations)


def playlist_references(name: str, text: str, sizes: dict[str, int]) -> set[str]:  # noqa: C901, PLR0912, PLR0915
    """Validate VOD references and ranges, returning the direct object names."""
    if len(text.encode()) > MAX_PLAYLIST_BYTES or not text.startswith("#EXTM3U\n"):
        msg = f"invalid or oversized HLS playlist: {name}"
        raise ValueError(msg)
    media = name != "master.m3u8"
    if media and "#EXT-X-ENDLIST" not in text.splitlines():
        msg = f"unfinished HLS playlist: {name}"
        raise ValueError(msg)
    found: set[str] = set()
    pending_range: str | None = None
    previous_ref: str | None = None
    previous_end: int | None = None
    segments = 0
    has_map = False
    for line in text.splitlines():
        if line.startswith("#EXT-X-KEY:"):
            msg = "encrypted HLS is outside the ladder contract"
            raise ValueError(msg)
        if line.startswith("#EXT-X-BYTERANGE:"):
            pending_range = line.partition(":")[2]
            continue
        uri: str | None = None
        byte_range: str | None = None
        if line.startswith("#"):
            attributes = line.partition(":")[2]
            matched = _URI.search(attributes)
            if matched:
                uri = matched[1]
                ranged = _RANGE.search(attributes)
                byte_range = ranged[1] if ranged else None
            if line.startswith("#EXT-X-MAP:"):
                has_map = uri is not None
                if uri is None:
                    msg = f"missing init URI in {name}"
                    raise ValueError(msg)
        elif line:
            uri = line
            byte_range = pending_range
            pending_range = None
            if media:
                segments += 1
        if uri is None:
            continue
        reference = _resolve(name, uri)
        size = sizes.get(reference, 0)
        if size <= 0:
            msg = f"missing or empty HLS object: {reference}"
            raise ValueError(msg)
        if media and reference.endswith(".m3u8"):
            msg = f"nested media playlist: {reference}"
            raise ValueError(msg)
        if byte_range is not None:
            previous_end = _range_end(
                byte_range, size, previous_end if previous_ref == reference else None
            )
        else:
            previous_end = None
        previous_ref = reference
        found.add(reference)
    if pending_range is not None or (media and (not segments or not has_map)) or not found:
        msg = f"incomplete fMP4 playlist: {name}"
        raise ValueError(msg)
    if media:
        playlist_duration(text)
        if segments != len(_EXTINF.findall(text)):
            msg = f"HLS segment references and durations disagree: {name}"
            raise ValueError(msg)
    return found


def validate_inventory(
    sizes: dict[str, int],
    playlists: dict[str, str],
    renditions: set[str],
    hashes: dict[str, str] | None = None,
) -> set[str]:
    """Require every rendition in the master and every referenced object by exact name."""
    expected = {"master.m3u8", *(f"{name}/index.m3u8" for name in renditions)}
    if not renditions or set(playlists) != expected or len(sizes) > MAX_OBJECTS:
        msg = "HLS inventory does not contain exactly the expected playlists"
        raise ValueError(msg)
    for name, text in playlists.items():
        if sizes.get(name) != len(text.encode()) or (
            hashes is not None and hashes.get(name) != playlist_hash(text)
        ):
            msg = f"changed HLS playlist: {name}"
            raise ValueError(msg)
    master_refs = playlist_references("master.m3u8", playlists["master.m3u8"], sizes)
    if master_refs != expected - {"master.m3u8"}:
        msg = "HLS master does not reference exactly the expected renditions"
        raise ValueError(msg)
    referenced = set(expected)
    for name in expected - {"master.m3u8"}:
        referenced.update(playlist_references(name, playlists[name], sizes))
    return referenced


def local_inventory(out_dir: Path, renditions: set[str]) -> tuple[dict[str, int], dict[str, str]]:
    """Collect only referenced local artifacts; stale unreferenced files stay outside the marker."""
    files = [path for path in out_dir.rglob("*") if path.is_file()]
    if len(files) > MAX_OBJECTS:
        msg = "HLS local inventory exceeds its object limit"
        raise ValueError(msg)
    sizes = {path.relative_to(out_dir).as_posix(): path.stat().st_size for path in files}
    names = {"master.m3u8", *(f"{name}/index.m3u8" for name in renditions)}
    if any(sizes.get(name, MAX_PLAYLIST_BYTES + 1) > MAX_PLAYLIST_BYTES for name in names):
        msg = "missing or oversized local HLS playlist"
        raise ValueError(msg)
    playlists = {name: (out_dir / name).read_text() for name in names}
    referenced = validate_inventory(sizes, playlists, renditions)
    return (
        {name: sizes[name] for name in sorted(referenced)},
        {name: playlist_hash(text) for name, text in sorted(playlists.items())},
    )
