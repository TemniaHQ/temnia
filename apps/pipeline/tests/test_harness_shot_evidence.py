"""Source binding, native score mapping and explicit detector availability."""

from __future__ import annotations

import asyncio
import copy
from fractions import Fraction
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest

from temnia_pipeline.contracts import Scope
from temnia_pipeline.harness import shot_evidence
from temnia_pipeline.harness.ledger import IdentityConflict
from temnia_pipeline.media import pyscene_shots
from temnia_pipeline.media.chapters import MediaTimelineFacts
from temnia_pipeline.media.source_shots import NativeShot, parse_source_scdet

if TYPE_CHECKING:
    from pathlib import Path

    from obstore.store import S3Store

SOURCE = UUID("10000000-0000-4000-8000-000000000001")
ARTIFACT = UUID("20000000-0000-4000-8000-000000000002")
SCOPE = Scope(
    organizationId=UUID("30000000-0000-4000-8000-000000000003"),
    userId=UUID("40000000-0000-4000-8000-000000000004"),
)
MASTER = {
    "key": f"org/{SCOPE.organizationId}/source/{SOURCE}/original/master.mp4",
    "sha256": "a" * 64,
    "sizeBytes": 123,
}
TIMELINE = MediaTimelineFacts(
    duration=Fraction(5),
    container_start=Fraction(-1, 2),
    source_start=Fraction(-1, 2),
    has_video=True,
    has_audio=False,
    video_stream_index=0,
    audio_stream_index=None,
    video_start=Fraction(-1, 2),
    audio_start=None,
    video_duration=Fraction(5),
    audio_duration=None,
    frame_rate=Fraction(25),
    video_time_base=Fraction(1, 1000),
    audio_time_base=None,
    sample_rate=None,
    width=64,
    height=64,
    rotation=0,
    audio_channels=None,
    audio_layout=None,
    variable_frame_rate=False,
    video_codec="h264",
    audio_codec=None,
)


def _binding() -> dict[str, Any]:
    return shot_evidence.source_shot_binding(
        scope=SCOPE,
        source_id=SOURCE,
        source_object=MASTER,
        timeline=TIMELINE,
        ffmpeg_sha256="b" * 64,
    )


def _binary_hash(_path: str) -> str:
    return "b" * 64


def _content() -> dict[str, Any]:
    return {
        "format": shot_evidence.FORMAT,
        "binding": _binding(),
        "status": "measured",
        "observations": [
            {"numerator": -1, "denominator": 4, "score": 15.0},
            {"numerator": 1, "denominator": 2, "score": 4.0},
            {"numerator": 2, "denominator": 1, "score": 30.0},
        ],
        "reason": None,
    }


def test_signed_pts_mapping_and_threshold_preserve_native_score_provenance() -> None:
    content = _content()
    shots = shot_evidence.shots_from_source_record(
        content, binding=_binding(), duration=Fraction(5)
    )
    assert [(row.timeMs, row.score) for row in shots] == [(250, 0.15), (2500, 0.3)]
    assert content["observations"][0]["score"] == 15.0
    assert "not-a-quality-probability" in _binding()["detector"]["scoreNormalization"]
    assert _binding()["detectorInput"]["object"] == MASTER


@pytest.mark.parametrize(
    "mutation", ["source", "proxy", "mapping", "detector", "score", "order", "range"]
)
def test_foreign_or_malformed_measurements_are_refused(mutation: str) -> None:
    content = copy.deepcopy(_content())
    if mutation == "source":
        content["binding"]["sourceObject"]["sha256"] = "c" * 64
    elif mutation == "proxy":
        content["binding"]["detectorInput"]["kind"] = "proxy"
    elif mutation == "mapping":
        content["binding"]["mapping"]["offsetNumerator"] = 123
    elif mutation == "detector":
        content["binding"]["detector"]["decisionThreshold"] = 0
    elif mutation == "score":
        content["observations"][0]["score"] = float("nan")
    elif mutation == "order":
        content["observations"].reverse()
    else:
        content["observations"][-1]["numerator"] = 100
    with pytest.raises((ValueError, IdentityConflict)):
        shot_evidence.shots_from_source_record(content, binding=_binding(), duration=Fraction(5))


def test_missing_and_unbound_legacy_proxy_are_never_measured_empty_results() -> None:
    assert shot_evidence.legacy_shot_availability(None) == {
        "status": "unavailable",
        "reason": "artifact_missing",
    }
    assert shot_evidence.legacy_shot_availability(b'{"version":1,"shots":[]}') == {
        "status": "unavailable",
        "reason": "legacy_proxy_provenance_missing",
    }
    content = _content()
    content["observations"] = []
    assert (
        shot_evidence.shots_from_source_record(content, binding=_binding(), duration=Fraction(5))
        == ()
    )
    content["status"] = "unavailable"
    with pytest.raises(ValueError, match="unavailable"):
        shot_evidence.shots_from_source_record(content, binding=_binding(), duration=Fraction(5))


def test_native_parser_keeps_signed_and_submillisecond_timestamps() -> None:
    native = parse_source_scdet(
        "frame:0 pts:-125 pts_time:-0.125\nlavfi.scd.score=15.5\n"
        "frame:1 pts:10001 pts_time:1.0001\nlavfi.scd.score=3.25\n"
    )
    assert native == (
        NativeShot(time=Fraction(-1, 8), score=15.5),
        NativeShot(time=Fraction(10001, 10000), score=3.25),
    )
    for malformed in (
        "pts_time:1",
        "lavfi.scd.score=20",
        "pts_time:1\nlavfi.scd.score=101",
        "corrupted metadata",
    ):
        with pytest.raises(ValueError, match="shot metadata"):
            parse_source_scdet(malformed)


@pytest.mark.parametrize(
    ("installed", "reason"),
    [
        ({}, "dependency_not_installed"),
        ({"scenedetect": "0.7.1"}, "headless_distribution_required"),
        ({"scenedetect-headless": "0.6.7"}, "unqualified_dependency_version"),
        ({"scenedetect-headless": "0.7.1"}, None),
        (
            {"scenedetect": "0.7.1", "scenedetect-headless": "0.7.1"},
            "conflicting_distributions",
        ),
    ],
)
def test_optional_challenger_never_claims_an_unperformed_audition(
    monkeypatch: pytest.MonkeyPatch, installed: dict[str, str], reason: str | None
) -> None:
    def package_version(package: str) -> str:
        if package not in installed:
            raise PackageNotFoundError(package)
        return installed[package]

    monkeypatch.setattr(pyscene_shots, "version", package_version)
    result = shot_evidence.pyscenedetect_availability()
    assert result["status"] == ("available" if reason is None else "unavailable")
    assert result["reason"] == reason
    assert result["auditionStatus"] == "not_auditioned"


@pytest.mark.parametrize("fails", [False, True])
async def test_measurement_and_known_failure_are_cached_with_owned_native_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fails: bool
) -> None:
    stored: dict[str, Any] = {}
    calls = 0
    record = SimpleNamespace(
        id=ARTIFACT, sha256="c" * 64, organization_id=SCOPE.organizationId, source_id=SOURCE
    )

    async def find(*_args: object, **_kwargs: object) -> SimpleNamespace | None:
        return record if stored else None

    async def publish(*_args: object, **kwargs: object) -> SimpleNamespace:
        stored.update(cast("dict[str, Any]", kwargs["content"]))
        return record

    async def read(*_args: object, **_kwargs: object) -> object:
        return stored

    async def detect(*_args: object, **_kwargs: object) -> tuple[NativeShot, ...]:
        nonlocal calls
        calls += 1
        if fails:
            raise OSError
        return (NativeShot(time=Fraction(1), score=20.0),)

    monkeypatch.setattr(shot_evidence, "_binary_hash", _binary_hash)
    monkeypatch.setattr(shot_evidence.artifacts, "find_artifact", find)
    monkeypatch.setattr(shot_evidence.artifacts, "publish_json", publish)
    monkeypatch.setattr(shot_evidence.artifacts, "read_artifact_json", read)
    monkeypatch.setattr(shot_evidence, "detect_source_shots", detect)
    args: dict[str, Any] = {
        "scope": SCOPE,
        "source_id": SOURCE,
        "store": object(),
        "source_path": tmp_path / "master",
        "source_object": MASTER,
        "timeline": TIMELINE,
        "ffmpeg": "ffmpeg",
    }
    first = await shot_evidence.build_source_shot_evidence("unused", **args)
    second = await shot_evidence.build_source_shot_evidence("unused", **args)
    assert calls == 1
    assert first == second
    assert first.shot_times_ms == (() if fails else (1500,))
    assert first.status == ("unavailable" if fails else "measured")
    assert first.provenance["reason"] == ("detector_failed:OSError" if fails else None)
    assert first.provenance["binding"]["detectorInput"]["kind"] == "master"


async def test_cancelled_measurement_is_not_published_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def find(*_args: object, **_kwargs: object) -> None:
        return None

    async def cancel(*_args: object, **_kwargs: object) -> tuple[NativeShot, ...]:
        raise asyncio.CancelledError

    async def fail_publish(*_args: object, **_kwargs: object) -> None:
        pytest.fail("cancelled detector cannot publish")

    monkeypatch.setattr(shot_evidence, "_binary_hash", _binary_hash)
    monkeypatch.setattr(shot_evidence.artifacts, "find_artifact", find)
    monkeypatch.setattr(shot_evidence.artifacts, "publish_json", fail_publish)
    monkeypatch.setattr(shot_evidence, "detect_source_shots", cancel)
    with pytest.raises(asyncio.CancelledError):
        await shot_evidence.build_source_shot_evidence(
            "unused",
            scope=SCOPE,
            source_id=SOURCE,
            store=cast("S3Store", object()),
            source_path=tmp_path / "master",
            source_object=MASTER,
            timeline=TIMELINE,
            ffmpeg="ffmpeg",
        )
