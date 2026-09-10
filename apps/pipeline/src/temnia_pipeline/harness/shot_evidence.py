"""Bound shot evidence, preserving native scores and explicit availability."""

# ruff: noqa: EM101, PLR0913, TRY003

from __future__ import annotations

import asyncio
import hashlib
import shutil
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from temnia_pipeline.contracts import HarnessEvidenceShot
from temnia_pipeline.harness import artifacts
from temnia_pipeline.harness.ledger import IdentityConflict
from temnia_pipeline.harness.rendering import timeline_identity
from temnia_pipeline.harness.validators import rounded_milliseconds
from temnia_pipeline.media.pyscene_shots import (
    ADAPTIVE_THRESHOLD,
    CONTENT_METRIC,
    MAX_ADAPTIVE_RATIO,
    MIN_CONTENT,
    RATIO_METRIC,
    adaptive_detector_identity,
    detect_adaptive_source_shots,
    pyscenedetect_availability,
)
from temnia_pipeline.media.source_shots import (
    DECISION_THRESHOLD,
    DETECTOR_VERSION,
    EMIT_FLOOR,
    MAX_SCORE,
    SCALE_HEIGHT,
    detect_source_shots,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from obstore.store import S3Store

    from temnia_pipeline.contracts import Scope
    from temnia_pipeline.media.chapters import MediaTimelineFacts

FORMAT = "source-shot-evidence/1"
DECODE_TIMEOUT_SECONDS = 3600
type ShotDetector = Literal["scdet", "pyscenedetect-adaptive"]

__all__ = [
    "ShotDetector",
    "SourceShotEvidence",
    "build_source_shot_evidence",
    "legacy_shot_availability",
    "pyscenedetect_availability",
    "shots_from_source_record",
    "source_shot_binding",
]


class _NativeShot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    numerator: int
    denominator: Annotated[int, Field(gt=0)]
    score: Annotated[float, Field(ge=0, le=MAX_ADAPTIVE_RATIO)]
    metrics: dict[str, float] = Field(default_factory=dict)


class _SourceShots(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["source-shot-evidence/1"] = FORMAT
    binding: dict[str, Any]
    status: Literal["measured", "unavailable"]
    observations: list[_NativeShot]
    reason: str | None


@dataclass(frozen=True, slots=True)
class SourceShotEvidence:
    """Compiler observations and immutable provenance, including empty measurements."""

    shots: tuple[HarnessEvidenceShot, ...]
    artifact_id: UUID
    provenance: Mapping[str, Any]
    status: Literal["measured", "unavailable"]

    @property
    def shot_times_ms(self) -> tuple[int, ...]:
        """Decision-threshold-filtered source times for the segmentation seam."""
        return tuple(shot.timeMs for shot in self.shots)


def _validate_object(scope: Scope, source_id: UUID, obj: Mapping[str, Any]) -> None:
    prefix = f"org/{scope.organizationId}/source/{source_id}/"
    key, sha, size = obj.get("key"), obj.get("sha256"), obj.get("sizeBytes")
    if (
        not isinstance(key, str)
        or not key.startswith(prefix)
        or ".." in key
        or not isinstance(sha, str)
        or len(sha) != artifacts.SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in sha)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        raise IdentityConflict("shot evidence requires scoped hash-verified object identities")


def source_shot_binding(
    *,
    scope: Scope,
    source_id: UUID,
    source_object: Mapping[str, Any],
    timeline: MediaTimelineFacts,
    ffmpeg_sha256: str | None,
    detector: ShotDetector = "scdet",
) -> dict[str, Any]:
    """Bind master detection to the actual input bytes and selected source clock.

    Legacy proxy grids lack their producer's master/proxy mapping and cannot be
    promoted by attaching a guessed offset here. This binding uses the master
    itself as detectorInput, avoiding any undeclared proxy transform.
    """
    _validate_object(scope, source_id, source_object)
    if ffmpeg_sha256 is not None and (
        len(ffmpeg_sha256) != artifacts.SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in ffmpeg_sha256)
    ):
        raise IdentityConflict("shot detector binary hash is invalid")
    binding = {
        "organizationId": str(scope.organizationId),
        "sourceId": str(source_id),
        "sourceObject": dict(source_object),
        "detectorInput": {"kind": "master", "object": dict(source_object)},
        "sourceTimeline": timeline_identity(timeline),
        "mapping": {
            "policy": "input-pts-minus-source-start/1",
            "offsetNumerator": -timeline.source_start.numerator,
            "offsetDenominator": timeline.source_start.denominator,
        },
        "detector": {
            "name": "ffmpeg.scdet",
            "adapterVersion": DETECTOR_VERSION,
            "binarySha256": ffmpeg_sha256,
            "height": SCALE_HEIGHT,
            "emitFloor": EMIT_FLOOR,
            "decisionThreshold": DECISION_THRESHOLD,
            "nativeScore": "scdet-percentage-of-maximum-change",
            "scoreNormalization": "native-score-divided-by-100;not-a-quality-probability",
            "selectedVideoStream": timeline.video_stream_index,
            "timestamps": "copyts;metadata-pts_time-decimal",
        },
    }
    if detector == "pyscenedetect-adaptive":
        binding["detector"] = {
            **adaptive_detector_identity(),
            "selectedVideoStream": timeline.video_stream_index,
        }
    return binding


def _normalized_native_score(row: _NativeShot, *, is_adaptive: bool) -> float | None:
    if is_adaptive:
        if (
            not ADAPTIVE_THRESHOLD <= row.score <= MAX_ADAPTIVE_RATIO
            or set(row.metrics) != {RATIO_METRIC, CONTENT_METRIC}
            or row.metrics[RATIO_METRIC] != row.score
            or row.metrics[CONTENT_METRIC] < MIN_CONTENT
        ):
            raise ValueError("adaptive shot record does not preserve its decision metrics")
        return row.score / MAX_ADAPTIVE_RATIO
    if not EMIT_FLOOR <= row.score <= MAX_SCORE or row.metrics:
        raise ValueError("scdet shot record has invalid native metrics")
    return row.score / MAX_SCORE if row.score >= DECISION_THRESHOLD else None


def shots_from_source_record(
    content: object, *, binding: Mapping[str, Any], duration: Fraction
) -> tuple[HarnessEvidenceShot, ...]:
    """Refuse mismatched ownership, bad mapping and malformed native measurements."""
    saved = _SourceShots.model_validate(content, strict=True)
    if saved.binding != binding:
        raise IdentityConflict("shot evidence does not name the selected source and detector")
    if saved.status == "unavailable":
        if not saved.reason or saved.observations:
            raise ValueError("unavailable shot evidence cannot contain measured observations")
        return ()
    detector = binding["detector"]
    is_adaptive = detector["name"] == "pyscenedetect.AdaptiveDetector"
    known_detector = (
        detector["availability"]["status"] == "available"
        if is_adaptive
        else detector["name"] == "ffmpeg.scdet" and detector["binarySha256"] is not None
    )
    if saved.reason is not None or not known_detector:
        raise ValueError("measured shots require a known detector identity and no failure")
    offset = Fraction(
        binding["mapping"]["offsetNumerator"], binding["mapping"]["offsetDenominator"]
    )
    result: dict[int, HarnessEvidenceShot] = {}
    previous: Fraction | None = None
    for row in saved.observations:
        normalized = _normalized_native_score(row, is_adaptive=is_adaptive)
        native = Fraction(row.numerator, row.denominator)
        mapped = native + offset
        if previous is not None and native <= previous:
            raise ValueError("shot observations must be strictly ordered")
        if not 0 <= mapped <= duration:
            raise ValueError("shot observation maps outside the selected source")
        previous = native
        if normalized is not None:
            time_ms = rounded_milliseconds(mapped)
            if time_ms not in result or normalized > result[time_ms].score:
                result[time_ms] = HarnessEvidenceShot(timeMs=time_ms, score=normalized)
    return tuple(result.values())


def legacy_shot_availability(content: bytes | None) -> dict[str, str]:
    """Old ingest grids have no hash-bound detector-input clock, even when empty."""
    return {
        "status": "unavailable",
        "reason": "artifact_missing" if content is None else "legacy_proxy_provenance_missing",
    }


def _binary_hash(ffmpeg: str) -> str:
    path = shutil.which(ffmpeg)
    if path is None:
        raise FileNotFoundError("shot detector executable is unavailable")
    with Path(path).open("rb") as executable:
        return hashlib.file_digest(executable, "sha256").hexdigest()


async def build_source_shot_evidence(
    database_url: str,
    *,
    scope: Scope,
    source_id: UUID,
    store: S3Store,
    source_path: Path,
    source_object: Mapping[str, Any],
    timeline: MediaTimelineFacts,
    ffmpeg: str,
    detector: ShotDetector = "scdet",
) -> SourceShotEvidence:
    """Reuse or measure one explicit detector under the caller's verified source lease.

    Existing unbound HLS grids are intentionally not relabeled as master
    observations. Known failures are cached as unavailable; cancellation and
    artifact integrity failures propagate. Unknown is never a no-shots claim.
    """
    unavailable: str | None = None
    binary_sha: str | None = None
    if detector == "scdet":
        try:
            binary_sha = await asyncio.to_thread(_binary_hash, ffmpeg)
        except OSError:
            unavailable = "detector_executable_unavailable"
    binding = source_shot_binding(
        scope=scope,
        source_id=source_id,
        source_object=source_object,
        timeline=timeline,
        ffmpeg_sha256=binary_sha,
        detector=detector,
    )
    if detector == "pyscenedetect-adaptive":
        unavailable = binding["detector"]["availability"]["reason"]
    identity = artifacts.ArtifactIdentity(
        kind="evidence",
        fingerprint=artifacts.fingerprint_for(
            kind=FORMAT, inputs=binding, config={"format": FORMAT}
        ),
    )
    accepted = await artifacts.find_artifact(
        database_url, scope=scope, source_id=source_id, identity=identity
    )
    if accepted is None:
        observations: list[_NativeShot] = []
        if not timeline.has_video or timeline.video_stream_index is None:
            unavailable = "source_has_no_video"
        elif unavailable is None:
            try:
                async with asyncio.timeout(DECODE_TIMEOUT_SECONDS):
                    if detector == "pyscenedetect-adaptive":
                        adaptive = await detect_adaptive_source_shots(
                            source_path, timeline=timeline
                        )
                        observations = [
                            _NativeShot(
                                numerator=row.time.numerator,
                                denominator=row.time.denominator,
                                score=row.adaptive_ratio,
                                metrics={
                                    RATIO_METRIC: row.adaptive_ratio,
                                    CONTENT_METRIC: row.content_value,
                                },
                            )
                            for row in adaptive
                        ]
                    else:
                        measured = await detect_source_shots(
                            source_path, timeline=timeline, ffmpeg=ffmpeg
                        )
                        observations = [
                            _NativeShot(
                                numerator=row.time.numerator,
                                denominator=row.time.denominator,
                                score=row.score,
                            )
                            for row in measured
                        ]
            except (
                ImportError,
                OSError,
                RuntimeError,
                TimeoutError,
                TypeError,
                ValueError,
            ) as error:
                unavailable = f"detector_failed:{type(error).__name__}"
        saved = _SourceShots(
            binding=binding,
            status="unavailable" if unavailable else "measured",
            observations=observations,
            reason=unavailable,
        )
        content = saved.model_dump(mode="json")
        shots_from_source_record(content, binding=binding, duration=timeline.duration)
        accepted = await artifacts.publish_json(
            database_url,
            scope=scope,
            source_id=source_id,
            store=store,
            identity=identity,
            content=content,
            metadata={"format": FORMAT, "status": saved.status},
        )
    else:
        if accepted.organization_id != scope.organizationId or accepted.source_id != source_id:
            raise IdentityConflict("shot artifact is outside the selected organization/source")
        content = await artifacts.read_artifact_json(
            database_url, scope=scope, source_id=source_id, store=store, artifact_id=accepted.id
        )
    shots = shots_from_source_record(content, binding=binding, duration=timeline.duration)
    saved = _SourceShots.model_validate(content, strict=True)
    return SourceShotEvidence(
        shots=shots,
        artifact_id=accepted.id,
        status=saved.status,
        provenance={
            "artifactId": str(accepted.id),
            "artifactSha256": accepted.sha256,
            "format": FORMAT,
            "status": saved.status,
            "reason": saved.reason,
            "binding": binding,
            "observationCount": len(saved.observations),
            "selectedCount": len(shots),
        },
    )
